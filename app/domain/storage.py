"""Private, append-only, content-addressed artifact storage services."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction

from .constants import RiskClass
from .models import Artifact, AuditRun, User

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_SUFFIXES = {
    ".csv",
    ".docx",
    ".html",
    ".json",
    ".md",
    ".pdf",
    ".pptx",
    ".txt",
    ".xlsx",
    ".zip",
}


class ArtifactIntegrityError(ValueError):
    """Stored bytes do not match their immutable artifact identity."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_stream(stream: BinaryIO, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(chunk_size):
        digest.update(chunk)
    return digest.hexdigest()


def artifact_object_key(run: AuditRun, digest: str, filename: str) -> str:
    """Return a tenant-scoped key containing no user-controlled path segment."""
    if not SHA256_RE.fullmatch(digest):
        raise ValueError("A lowercase SHA-256 digest is required")
    suffix = Path(filename).suffix.casefold()
    suffix = suffix if suffix in SAFE_SUFFIXES else ""
    key = PurePosixPath(
        "clients",
        str(run.project.client_id),
        "projects",
        str(run.project_id),
        "runs",
        str(run.pk),
        "artifacts",
        "sha256",
        digest[:2],
        digest + suffix,
    )
    return key.as_posix()


def _verify_existing_object(storage_key: str, digest: str) -> None:
    try:
        with default_storage.open(storage_key, "rb") as stream:
            actual = sha256_stream(stream)
    except OSError as exc:
        raise ArtifactIntegrityError("The existing private object could not be read") from exc
    if actual != digest:
        raise ArtifactIntegrityError("A content-addressed object exists with different bytes")


@transaction.atomic
def save_artifact_bytes(
    *,
    run: AuditRun,
    payload: bytes,
    filename: str,
    title: str,
    artifact_type: str,
    media_type: str,
    created_by: User | None,
    risk_class: str = RiskClass.LOW,
    approval_required: bool = False,
    expected_sha256: str | None = None,
    metadata: dict | None = None,
) -> tuple[Artifact, bool]:
    """Persist exact bytes once and register an idempotent canonical artifact row."""
    if not payload:
        raise ValueError("Artifact bytes cannot be empty")
    maximum = int(getattr(settings, "MAX_ARTIFACT_BYTES", 250 * 1024 * 1024))
    if len(payload) > maximum:
        raise ValueError("Artifact exceeds the configured byte limit")
    digest = sha256_bytes(payload)
    if expected_sha256 and expected_sha256 != digest:
        raise ArtifactIntegrityError("Artifact bytes do not match expected_sha256")
    storage_key = artifact_object_key(run, digest, filename)

    if default_storage.exists(storage_key):
        _verify_existing_object(storage_key, digest)
    else:
        returned_key = default_storage.save(storage_key, ContentFile(payload))
        if returned_key != storage_key:
            raise ArtifactIntegrityError("Storage changed the append-only content-addressed key")
        _verify_existing_object(storage_key, digest)

    artifact, created = Artifact.objects.get_or_create(
        run=run,
        storage_key=storage_key,
        defaults={
            "created_by": created_by,
            "artifact_type": artifact_type,
            "title": title,
            "format": Path(filename).suffix.lstrip(".").casefold() or "binary",
            "sha256": digest,
            "size_bytes": len(payload),
            "media_type": media_type,
            "risk_class": risk_class,
            "approval_required": approval_required,
            "metadata": metadata or {},
        },
    )
    if artifact.sha256 != digest or artifact.size_bytes != len(payload):
        raise ArtifactIntegrityError("Artifact metadata conflicts with immutable stored bytes")
    return artifact, created


def artifact_bytes_available(artifact: Artifact) -> bool:
    """True when the artifact's stored bytes are actually retrievable.

    Local container storage is ephemeral across redeploys, so a DB row alone
    does not prove the object still exists; callers use this to decide
    between serving a download and rebuilding the artifact.
    """

    if not artifact.storage_key:
        return False
    path = PurePosixPath(artifact.storage_key)
    if path.is_absolute() or ".." in path.parts:
        return False
    try:
        return default_storage.exists(artifact.storage_key)
    except Exception:
        return False


def open_verified_artifact(artifact: Artifact) -> BinaryIO:
    """Open an artifact only after checking its path and content hash."""
    path = PurePosixPath(artifact.storage_key)
    if path.is_absolute() or ".." in path.parts or not artifact.storage_key:
        raise ArtifactIntegrityError("Unsafe artifact storage key")
    if not default_storage.exists(artifact.storage_key):
        raise ArtifactIntegrityError("Artifact object is unavailable")
    stream = default_storage.open(artifact.storage_key, "rb")
    try:
        if sha256_stream(stream) != artifact.sha256:
            raise ArtifactIntegrityError("Artifact object failed its SHA-256 integrity check")
        stream.seek(0)
        return stream
    except Exception:
        stream.close()
        raise
