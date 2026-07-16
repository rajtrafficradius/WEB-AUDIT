"""Quarantine, validate, and persist evidence imports without trusting filenames."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any, BinaryIO
from uuid import uuid4

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction

from app.domain.constants import AvailabilityStatus
from app.domain.models import Project, SourceImport, User
from app.domain.permissions import can_manage_project

from .uploads import ImportLimits, ImportReport, UploadValidationError, validate_import

IMPORT_SCHEMA_VERSION = "imports-1.0"
CRAWL_IMPORT_SCHEMA_VERSION = "imports-1.1"
SOURCE_TYPES = {
    "ahrefs",
    "screaming_frog",
    "brightlocal_gbp",
    "mapped_csv_xlsx",
    "crawl_data_file",
}

SOURCE_HEADER_ALIASES: dict[str, frozenset[str]] = {
    "ahrefs": frozenset(
        {
            "backlink url",
            "linking page",
            "referring page",
            "referring page url",
            "source url",
            "target url",
            "url",
        }
    ),
    "screaming_frog": frozenset({"address", "crawl url", "url"}),
    "brightlocal_gbp": frozenset(
        {
            "address",
            "business name",
            "keyword",
            "location",
            "location name",
            "rank",
            "search term",
            "store code",
        }
    ),
    "crawl_data_file": frozenset(
        {
            "a",
            "address",
            "crawl url",
            "href",
            "loc",
            "location",
            "original",
            "original url",
            "page url",
            "uri",
            "url",
        }
    ),
}
SOURCE_LABELS = {
    "ahrefs": "Ahrefs",
    "screaming_frog": "Screaming Frog",
    "brightlocal_gbp": "BrightLocal / GBP",
    "crawl_data_file": "CDX / CDD / XML crawl data",
}
SOURCE_SUFFIXES = {
    "ahrefs": frozenset({".csv", ".xlsx"}),
    "screaming_frog": frozenset({".csv", ".xlsx"}),
    "brightlocal_gbp": frozenset({".csv", ".xlsx"}),
    "mapped_csv_xlsx": frozenset({".csv", ".xlsx"}),
    "crawl_data_file": frozenset({".cdx", ".cdd", ".xml"}),
}


class ImportStorageError(RuntimeError):
    """Raised when immutable private storage does not preserve exact bytes."""


def _safe_filename(value: str) -> str:
    name = PurePosixPath(str(value or "").replace("\\", "/")).name
    cleaned = "".join(character for character in name if " " <= character != "\x7f").strip()
    return (cleaned or "evidence-upload")[:255]


def _normalized_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _validate_source_schema(report: ImportReport, source_type: str) -> None:
    required = SOURCE_HEADER_ALIASES.get(source_type)
    if not required:
        return
    observed = {_normalized_header(value) for sheet in report.sheets for value in sheet.headers}
    if observed.isdisjoint(required):
        label = SOURCE_LABELS[source_type]
        raise UploadValidationError(
            "source_schema", f"{label} import is missing a recognized identifier column."
        )


def _schema_version(source_type: str) -> str:
    return CRAWL_IMPORT_SCHEMA_VERSION if source_type == "crawl_data_file" else IMPORT_SCHEMA_VERSION


def _validate_source_suffix(source_type: str, suffix: str) -> None:
    if suffix in SOURCE_SUFFIXES[source_type]:
        return
    if source_type == "crawl_data_file":
        message = "Choose a CDX, CDD, or XML file for the crawl-data source."
    else:
        message = "Choose a CSV or XLSX file for this evidence source."
    raise UploadValidationError("source_file_type", message)


def _storage_key(project: Project, digest: str, suffix: str) -> str:
    allowed_suffixes = {suffix for values in SOURCE_SUFFIXES.values() for suffix in values}
    safe_suffix = suffix.casefold() if suffix.casefold() in allowed_suffixes else ""
    return PurePosixPath(
        "clients",
        str(project.client_id),
        "projects",
        str(project.pk),
        "imports",
        "sha256",
        digest[:2],
        digest + safe_suffix,
    ).as_posix()


def _stream_sha256(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1_048_576), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _verify_stored(key: str, expected_digest: str) -> None:
    try:
        with default_storage.open(key, "rb") as stored:
            actual = _stream_sha256(stored)
    except Exception as exc:
        raise ImportStorageError("Private import storage could not be verified") from exc
    if actual != expected_digest:
        raise ImportStorageError("Private import storage failed SHA-256 verification")


def _store_validated(path: Path, project: Project, report: ImportReport) -> str:
    key = _storage_key(project, report.sha256, path.suffix)
    try:
        if default_storage.exists(key):
            _verify_stored(key, report.sha256)
            return key
        with path.open("rb") as stream:
            returned = default_storage.save(key, File(stream, name=key))
        if returned != key:
            raise ImportStorageError("Private storage changed the content-addressed import key")
        _verify_stored(key, report.sha256)
        return key
    except ImportStorageError:
        raise
    except Exception as exc:
        raise ImportStorageError("Private import storage operation failed") from exc


def _mapping(report: ImportReport, as_of_date: str | None) -> dict[str, Any]:
    sheets = [
        {
            "name": sheet.name,
            "row_count": sheet.row_count,
            "column_count": sheet.column_count,
            "headers": list(sheet.headers),
        }
        for sheet in report.sheets
    ]
    return {
        "as_of_date": as_of_date or None,
        "media_type": report.media_type,
        "row_count": sum(max(0, sheet.row_count - 1) for sheet in report.sheets),
        "sheets": sheets,
    }


def _record_rejection(
    *,
    project: Project,
    actor: User,
    source_type: str,
    schema_version: str,
    original_filename: str,
    uploaded: UploadedFile,
    digest: str,
    size: int,
    error: UploadValidationError,
) -> SourceImport:
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=project.pk)
        item, _ = SourceImport.objects.get_or_create(
            project=project,
            source_type=source_type,
            sha256=digest,
            schema_version=schema_version,
            defaults={
                "created_by": actor,
                "original_filename": original_filename,
                "media_type": str(getattr(uploaded, "content_type", "") or "application/octet-stream")[:100],
                "size_bytes": size,
                "storage_key": "",
                "status": SourceImport.Status.REJECTED,
                "availability": AvailabilityStatus.ERROR,
                "unavailable_reason": error.safe_message,
                "validation_issues": [
                    {
                        "code": error.code,
                        "message": error.safe_message,
                        "digest_scope": "observed_prefix" if error.code == "file_too_large" else "full_upload",
                    }
                ],
            },
        )
        return item


def persist_validated_import(
    *,
    project: Project,
    actor: User,
    source_type: str,
    uploaded: UploadedFile,
    as_of_date: str | None = None,
    limits: ImportLimits | None = None,
) -> tuple[SourceImport, bool]:
    """Stream one upload through quarantine, validation, immutable storage, and canonical metadata."""
    if not can_manage_project(actor, project):
        raise PermissionError("Project management permission is required for evidence imports")
    if source_type not in SOURCE_TYPES:
        raise UploadValidationError("source_type", "Choose a supported evidence source type.")
    policy = limits or ImportLimits()
    original_filename = _safe_filename(uploaded.name)
    suffix = Path(original_filename).suffix.casefold()
    schema_version = _schema_version(source_type)
    quarantine = Path(settings.MEDIA_ROOT) / "quarantine"
    quarantine.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0

    with TemporaryDirectory(prefix="evidence-", dir=quarantine) as temporary:
        staged = Path(temporary) / f"{uuid4().hex}{suffix}"
        try:
            with staged.open("xb") as destination:
                for chunk in uploaded.chunks():
                    if size + len(chunk) > policy.max_file_bytes:
                        raise UploadValidationError(
                            "file_too_large", "Upload exceeds the configured byte limit."
                        )
                    size += len(chunk)
                    digest.update(chunk)
                    destination.write(chunk)
            _validate_source_suffix(source_type, suffix)
            report = validate_import(staged, allowed_root=Path(temporary), limits=policy)
            _validate_source_schema(report, source_type)
        except UploadValidationError as exc:
            if staged.is_file() and size:
                _record_rejection(
                    project=project,
                    actor=actor,
                    source_type=source_type,
                    schema_version=schema_version,
                    original_filename=original_filename,
                    uploaded=uploaded,
                    digest=digest.hexdigest(),
                    size=size,
                    error=exc,
                )
            raise
        if report.sha256 != digest.hexdigest() or report.byte_size != size:
            raise ImportStorageError("Quarantined import changed during validation")

        with transaction.atomic():
            Project.objects.select_for_update().get(pk=project.pk)
            existing = SourceImport.objects.filter(
                project=project,
                source_type=source_type,
                sha256=report.sha256,
                schema_version=schema_version,
            ).first()
            expected_key = _storage_key(project, report.sha256, staged.suffix)
            if existing is not None:
                if existing.status != SourceImport.Status.ACCEPTED or existing.storage_key != expected_key:
                    raise ImportStorageError("An existing import conflicts with the validated payload")
                _verify_stored(expected_key, report.sha256)
                return existing, False
            key = _store_validated(staged, project, report)
            item = SourceImport.objects.create(
            project=project,
            source_type=source_type,
            sha256=report.sha256,
            schema_version=schema_version,
            created_by=actor,
            original_filename=original_filename,
            media_type=report.media_type,
            size_bytes=report.byte_size,
            storage_key=key,
            status=SourceImport.Status.ACCEPTED,
            availability=AvailabilityStatus.AVAILABLE,
            column_mapping=_mapping(report, as_of_date),
            validation_issues=[],
        )
            return item, True
