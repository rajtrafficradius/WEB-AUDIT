"""Defensive tabular and crawl-export validation without executing untrusted data."""

from __future__ import annotations

import codecs
import csv
import hashlib
import re
import stat
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException


class UploadValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


@dataclass(frozen=True, slots=True)
class ImportLimits:
    max_file_bytes: int = 50_000_000
    max_archive_entries: int = 5_000
    max_uncompressed_bytes: int = 100_000_000
    max_member_bytes: int = 25_000_000
    max_compression_ratio: float = 100.0
    max_rows: int = 1_000_000
    max_columns: int = 500
    max_cell_characters: int = 32_767
    max_xml_depth: int = 128
    max_xml_elements: int = 1_000_000
    max_xml_attributes: int = 500

    def __post_init__(self) -> None:
        if (
            min(
                self.max_file_bytes,
                self.max_archive_entries,
                self.max_uncompressed_bytes,
                self.max_member_bytes,
                self.max_rows,
                self.max_columns,
                self.max_cell_characters,
                self.max_xml_depth,
                self.max_xml_elements,
                self.max_xml_attributes,
            )
            <= 0
            or self.max_compression_ratio < 1
        ):
            raise ValueError("Import limits must be positive")


@dataclass(frozen=True, slots=True)
class SheetReport:
    name: str
    row_count: int
    column_count: int
    headers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ImportReport:
    file_name: str
    media_type: str
    byte_size: int
    sha256: str
    sheets: tuple[SheetReport, ...]


FORMULA_PREFIXES = ("=", "+", "-", "@")
SAFE_NEGATIVE_NUMBER = re.compile(r"^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


def _formula_like(value: str) -> bool:
    stripped = value.lstrip(" \t\r\n")
    if not stripped or stripped[0] not in FORMULA_PREFIXES:
        return False
    return not bool(SAFE_NEGATIVE_NUMBER.fullmatch(stripped))


def _safe_path(path: Path, allowed_root: Path | None) -> Path:
    if path.is_symlink():
        raise UploadValidationError("symlink", "Symbolic-link uploads are not permitted.")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise UploadValidationError("file_missing", "Uploaded file is not available.") from exc
    if not resolved.is_file():
        raise UploadValidationError("not_a_file", "Upload must be a regular file.")
    if allowed_root is not None:
        try:
            root = allowed_root.resolve(strict=True)
        except OSError as exc:
            raise UploadValidationError(
                "root_missing", "Configured upload root is unavailable."
            ) from exc
        if not resolved.is_relative_to(root):
            raise UploadValidationError(
                "path_escape", "Upload path is outside the permitted staging directory."
            )
    return resolved


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_headers(values: list[str], limits: ImportLimits) -> tuple[str, ...]:
    if not values:
        raise UploadValidationError("missing_headers", "Import must contain a header row.")
    if len(values) > limits.max_columns:
        raise UploadValidationError(
            "too_many_columns", "Import exceeds the configured column limit."
        )
    headers = tuple(value.strip() for value in values)
    if any(not value for value in headers):
        raise UploadValidationError("blank_header", "Import headers cannot be blank.")
    folded = [value.casefold() for value in headers]
    if len(set(folded)) != len(folded):
        raise UploadValidationError("duplicate_header", "Import headers must be unique.")
    if any(len(value) > 255 or _formula_like(value) for value in headers):
        raise UploadValidationError(
            "unsafe_header", "Import contains an invalid or formula-like header."
        )
    return headers


def _validate_cell(value: str, limits: ImportLimits) -> None:
    if len(value) > limits.max_cell_characters:
        raise UploadValidationError(
            "cell_too_long", "Import contains a cell above the configured size limit."
        )
    if "\x00" in value:
        raise UploadValidationError("nul_byte", "Import contains a prohibited NUL byte.")
    if _formula_like(value):
        raise UploadValidationError(
            "formula", "Spreadsheet formulas and formula-like cells are not permitted."
        )


def _validate_csv(path: Path, limits: ImportLimits) -> tuple[SheetReport, ...]:
    with path.open("rb") as raw:
        prefix = raw.read(min(65_536, limits.max_file_bytes + 1))
    if b"\x00" in prefix:
        raise UploadValidationError("nul_byte", "CSV contains a prohibited NUL byte.")
    try:
        sample = prefix.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UploadValidationError("encoding", "CSV must use UTF-8 encoding.") from exc
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = 0
    max_columns = 0
    headers: tuple[str, ...] | None = None
    try:
        with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as handle:
            reader = csv.reader(handle, dialect)
            for row in reader:
                rows += 1
                if rows > limits.max_rows:
                    raise UploadValidationError(
                        "too_many_rows", "CSV exceeds the configured row limit."
                    )
                if len(row) > limits.max_columns:
                    raise UploadValidationError(
                        "too_many_columns", "CSV exceeds the configured column limit."
                    )
                for value in row:
                    _validate_cell(value, limits)
                max_columns = max(max_columns, len(row))
                if headers is None:
                    headers = _validate_headers(row, limits)
    except UnicodeDecodeError as exc:
        raise UploadValidationError("encoding", "CSV must use UTF-8 encoding.") from exc
    except csv.Error as exc:
        raise UploadValidationError(
            "malformed_csv", "CSV structure could not be parsed safely."
        ) from exc
    if headers is None:
        raise UploadValidationError("empty_file", "CSV contains no rows.")
    return (SheetReport("CSV", rows, max_columns, headers),)


XML_PROHIBITED = (b"<!DOCTYPE", b"<!ENTITY")
PROHIBITED_XLSX_PARTS = (
    "vbaproject.bin",
    "xl/externallinks/",
    "xl/embeddings/",
    "xl/activex/",
    "oleobject",
    "xl/connections.xml",
    "customui/",
)


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limits: ImportLimits) -> bytes:
    if info.file_size > limits.max_member_bytes:
        raise UploadValidationError(
            "member_too_large", "Workbook contains an oversized internal file."
        )
    data = archive.read(info)
    upper = data[:4096].upper()
    if info.filename.casefold().endswith((".xml", ".rels")) and any(
        marker in upper for marker in XML_PROHIBITED
    ):
        raise UploadValidationError("xml_entity", "Workbook contains a prohibited XML declaration.")
    return data


def _check_archive(archive: zipfile.ZipFile, limits: ImportLimits) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > limits.max_archive_entries:
        raise UploadValidationError("too_many_parts", "Workbook contains too many internal files.")
    total = 0
    mapping: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        pure = PurePosixPath(info.filename.replace("\\", "/"))
        if pure.is_absolute() or ".." in pure.parts:
            raise UploadValidationError(
                "archive_path_escape", "Workbook contains an unsafe archive path."
            )
        if info.flag_bits & 0x1:
            raise UploadValidationError("encrypted", "Encrypted workbooks are not supported.")
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        if unix_mode and stat.S_ISLNK(unix_mode):
            raise UploadValidationError(
                "archive_symlink", "Workbook archive contains a symbolic link."
            )
        normalized = pure.as_posix().casefold()
        if normalized in mapping:
            raise UploadValidationError(
                "duplicate_part", "Workbook contains ambiguous duplicate internal paths."
            )
        if any(part in normalized for part in PROHIBITED_XLSX_PARTS):
            raise UploadValidationError(
                "active_content", "Workbook contains external, embedded, or active content."
            )
        total += info.file_size
        if info.file_size > limits.max_member_bytes or total > limits.max_uncompressed_bytes:
            raise UploadValidationError("zip_bomb", "Workbook exceeds safe decompression limits.")
        ratio = info.file_size / max(1, info.compress_size)
        if info.file_size > 1_024 and ratio > limits.max_compression_ratio:
            raise UploadValidationError("zip_bomb", "Workbook has an unsafe compression ratio.")
        mapping[normalized] = info
    return mapping


def _xml(data: bytes) -> Element:
    try:
        return DefusedET.fromstring(data)
    except (DefusedXmlException, ParseError) as exc:
        raise UploadValidationError("malformed_xml", "Workbook contains malformed XML.") from exc


@dataclass(slots=True)
class _XMLFrame:
    tag: str
    attributes: tuple[tuple[str, str], ...]
    child_count: int = 0
    children: list[tuple[str, bool, str | None]] = field(default_factory=list)


def _local_xml_name(value: str) -> str:
    return value.rsplit("}", 1)[-1].rsplit(":", 1)[-1].strip()


def _validate_crawl_cell(value: str, limits: ImportLimits) -> None:
    if any(ord(character) < 32 and character not in "\t\r\n" for character in value):
        raise UploadValidationError(
            "binary_content", "Crawl import contains prohibited binary control characters."
        )
    if value.strip() == "-":
        if len(value) > limits.max_cell_characters or "\x00" in value:
            _validate_cell(value, limits)
        return
    _validate_cell(value, limits)


def _scan_xml_declarations(path: Path) -> None:
    overlap = b""
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1_048_576), b""):
                if b"\x00" in chunk:
                    raise UploadValidationError(
                        "encoding", "XML crawl imports must use UTF-8 encoding."
                    )
                decoder.decode(chunk)
                combined = (overlap + chunk).upper()
                if any(marker in combined for marker in XML_PROHIBITED):
                    raise UploadValidationError(
                        "xml_entity",
                        "XML crawl imports cannot contain DTD or entity declarations.",
                    )
                overlap = combined[-32:]
        decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise UploadValidationError(
            "encoding", "XML crawl imports must use UTF-8 encoding."
        ) from exc


def _xml_headers(frame: _XMLFrame, limits: ImportLimits) -> tuple[str, ...] | None:
    if frame.child_count == 0 and frame.attributes:
        return _validate_headers([name for name, _ in frame.attributes], limits)
    if (
        not frame.children
        or frame.child_count != len(frame.children)
        or any(has_children for _, has_children, _ in frame.children)
    ):
        return None
    child_names = [
        declared_name.strip() if declared_name else child_name
        for child_name, _, declared_name in frame.children
    ]
    if len({name.casefold() for name in child_names}) != len(child_names):
        return None
    attribute_names = [name for name, _ in frame.attributes]
    headers = attribute_names + child_names
    if len({name.casefold() for name in headers}) != len(headers):
        headers = [f"attribute_{name}" for name in attribute_names] + child_names
    return _validate_headers(headers, limits)


def _validate_crawl_xml(path: Path, limits: ImportLimits) -> tuple[SheetReport, ...]:
    _scan_xml_declarations(path)
    stack: list[_XMLFrame] = []
    shapes: Counter[tuple[str, tuple[str, ...]]] = Counter()
    element_count = 0
    try:
        events = DefusedET.iterparse(
            path,
            events=("start", "end"),
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
        for event, element in events:
            if event == "start":
                element_count += 1
                if element_count > limits.max_xml_elements:
                    raise UploadValidationError(
                        "too_many_elements", "XML crawl import exceeds the element limit."
                    )
                if len(stack) + 1 > limits.max_xml_depth:
                    raise UploadValidationError(
                        "xml_too_deep", "XML crawl import exceeds the nesting-depth limit."
                    )
                if len(element.attrib) > limits.max_xml_attributes:
                    raise UploadValidationError(
                        "too_many_attributes", "XML crawl import exceeds the attribute limit."
                    )
                attributes: list[tuple[str, str]] = []
                for raw_name, value in element.attrib.items():
                    name = _local_xml_name(raw_name)
                    _validate_crawl_cell(name, limits)
                    _validate_crawl_cell(value, limits)
                    attributes.append((name, value))
                stack.append(_XMLFrame(_local_xml_name(element.tag), tuple(attributes)))
                continue

            frame = stack.pop()
            if element.text and element.text.strip():
                _validate_crawl_cell(element.text, limits)
            if element.tail and element.tail.strip():
                _validate_crawl_cell(element.tail, limits)
            headers = _xml_headers(frame, limits)
            if headers is not None:
                if len(shapes) >= 1_024 and (frame.tag, headers) not in shapes:
                    raise UploadValidationError(
                        "xml_too_complex", "XML crawl import contains too many record shapes."
                    )
                shapes[(frame.tag, headers)] += 1
            if stack:
                parent = stack[-1]
                parent.child_count += 1
                if len(parent.children) < limits.max_columns + 1:
                    declared_name = dict(frame.attributes).get("name")
                    parent.children.append((frame.tag, bool(frame.child_count), declared_name))
            element.clear()
    except UploadValidationError:
        raise
    except (DefusedXmlException, ParseError, OSError) as exc:
        raise UploadValidationError(
            "malformed_xml", "XML crawl data could not be parsed safely."
        ) from exc
    if not shapes:
        raise UploadValidationError(
            "missing_records", "XML crawl import contains no recognizable records."
        )
    (record_tag, headers), record_count = max(
        shapes.items(), key=lambda item: (item[1], len(item[0][1]))
    )
    if record_count + 1 > limits.max_rows:
        raise UploadValidationError(
            "too_many_rows", "XML crawl import exceeds the configured row limit."
        )
    return (
        SheetReport(
            f"XML {record_tag}"[:100],
            record_count + 1,
            len(headers),
            headers,
        ),
    )


def _crawl_text_rows(path: Path, sample: str):
    first_line = next((line.strip() for line in sample.splitlines() if line.strip()), "")
    dialect = None
    if not first_line.casefold().startswith("cdx ") and any(
        delimiter in first_line for delimiter in ",;\t|"
    ):
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = None
    handle = path.open("r", encoding="utf-8-sig", errors="strict", newline="")
    if dialect is not None:
        return handle, csv.reader(handle, dialect)
    return handle, (re.split(r"\s+", line.strip()) for line in handle if line.strip())


def _validate_crawl_text(
    path: Path, limits: ImportLimits, *, label: str
) -> tuple[SheetReport, ...]:
    with path.open("rb") as raw:
        prefix = raw.read(min(65_536, limits.max_file_bytes + 1))
    if b"\x00" in prefix:
        raise UploadValidationError("nul_byte", "Crawl import contains a prohibited NUL byte.")
    try:
        sample = prefix.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UploadValidationError(
            "encoding", "CDX and CDD crawl imports must use UTF-8 encoding."
        ) from exc
    rows = 0
    max_columns = 0
    headers: tuple[str, ...] | None = None
    try:
        handle, parsed_rows = _crawl_text_rows(path, sample)
        with handle:
            for raw_row in parsed_rows:
                row = list(raw_row)
                if not row or all(not value.strip() for value in row):
                    continue
                if headers is None and row[0].casefold() == "cdx" and len(row) > 1:
                    row = row[1:]
                rows += 1
                if rows > limits.max_rows:
                    raise UploadValidationError(
                        "too_many_rows", "Crawl import exceeds the configured row limit."
                    )
                if len(row) > limits.max_columns:
                    raise UploadValidationError(
                        "too_many_columns", "Crawl import exceeds the configured column limit."
                    )
                for value in row:
                    _validate_crawl_cell(value, limits)
                max_columns = max(max_columns, len(row))
                if headers is None:
                    headers = _validate_headers(row, limits)
    except UnicodeDecodeError as exc:
        raise UploadValidationError(
            "encoding", "CDX and CDD crawl imports must use UTF-8 encoding."
        ) from exc
    except csv.Error as exc:
        raise UploadValidationError(
            "malformed_crawl_data", "Crawl data structure could not be parsed safely."
        ) from exc
    if headers is None:
        raise UploadValidationError("empty_file", "Crawl import contains no rows.")
    if rows < 2:
        raise UploadValidationError(
            "missing_records", "Crawl import must include a header and at least one record."
        )
    return (SheetReport(label, rows, max_columns, headers),)


def _looks_like_xml(path: Path) -> bool:
    with path.open("rb") as handle:
        prefix = handle.read(4_096)
    if prefix.startswith(b"\xef\xbb\xbf"):
        prefix = prefix[3:]
    return prefix.lstrip(b" \t\r\n").startswith(b"<")


def _validate_crawl_file(
    path: Path, limits: ImportLimits, *, suffix: str
) -> tuple[str, tuple[SheetReport, ...]]:
    if suffix == ".xml" or _looks_like_xml(path):
        return "application/xml", _validate_crawl_xml(path, limits)
    return "text/plain", _validate_crawl_text(path, limits, label=suffix[1:].upper())


def _cell_text(cell: Element, shared: list[str]) -> str:
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    if cell.find(namespace + "f") is not None:
        raise UploadValidationError("formula", "Workbook formulas are not permitted.")
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(namespace + "t"))
    value = cell.find(namespace + "v")
    raw = value.text if value is not None and value.text is not None else ""
    if cell_type == "s" and raw:
        try:
            return shared[int(raw)]
        except (ValueError, IndexError) as exc:
            raise UploadValidationError(
                "shared_string", "Workbook has an invalid shared-string reference."
            ) from exc
    return raw


def _column_number(reference: str) -> int:
    match = re.match(r"^([A-Za-z]+)", reference)
    if not match:
        return 0
    value = 0
    for char in match.group(1).upper():
        value = value * 26 + (ord(char) - 64)
    return value


def _validate_xlsx(path: Path, limits: ImportLimits) -> tuple[SheetReport, ...]:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UploadValidationError("invalid_xlsx", "File is not a valid XLSX workbook.") from exc
    with archive:
        parts = _check_archive(archive, limits)
        required = {"[content_types].xml", "xl/workbook.xml"}
        if not required.issubset(parts):
            raise UploadValidationError("invalid_xlsx", "Workbook is missing required XLSX parts.")
        content_types = _read_member(archive, parts["[content_types].xml"], limits).lower()
        if b"macroenabled" in content_types or b"vba" in content_types:
            raise UploadValidationError("macros", "Macro-enabled workbooks are not permitted.")
        for lowered, info in parts.items():
            if lowered.endswith(".rels"):
                root = _xml(_read_member(archive, info, limits))
                for relationship in root:
                    if relationship.attrib.get("TargetMode", "").casefold() == "external":
                        raise UploadValidationError(
                            "external_link", "Workbook external relationships are not permitted."
                        )

        shared: list[str] = []
        shared_info = parts.get("xl/sharedstrings.xml")
        if shared_info:
            root = _xml(_read_member(archive, shared_info, limits))
            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            for item in root.findall(ns + "si"):
                value = "".join(node.text or "" for node in item.iter(ns + "t"))
                _validate_cell(value, limits)
                shared.append(value)

        sheet_infos = sorted(
            (
                info
                for name, info in parts.items()
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            ),
            key=lambda item: item.filename.casefold(),
        )
        if not sheet_infos:
            raise UploadValidationError("missing_sheet", "Workbook contains no worksheets.")
        reports: list[SheetReport] = []
        total_rows = 0
        namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        for index, info in enumerate(sheet_infos, start=1):
            data = _read_member(archive, info, limits)
            root = _xml(data)
            rows = root.iter(namespace + "row")
            row_count = 0
            max_column = 0
            headers: tuple[str, ...] | None = None
            for row in rows:
                row_count += 1
                total_rows += 1
                if total_rows > limits.max_rows:
                    raise UploadValidationError(
                        "too_many_rows", "Workbook exceeds the configured row limit."
                    )
                values_by_column: dict[int, str] = {}
                for cell in row.findall(namespace + "c"):
                    column = _column_number(cell.attrib.get("r", "")) or len(values_by_column) + 1
                    if column > limits.max_columns:
                        raise UploadValidationError(
                            "too_many_columns", "Workbook exceeds the configured column limit."
                        )
                    value = _cell_text(cell, shared)
                    _validate_cell(value, limits)
                    values_by_column[column] = value
                    max_column = max(max_column, column)
                if headers is None and values_by_column:
                    width = max(values_by_column)
                    headers = _validate_headers(
                        [values_by_column.get(column, "") for column in range(1, width + 1)], limits
                    )
            if headers is None:
                raise UploadValidationError(
                    "empty_sheet", f"Worksheet {index} contains no header row."
                )
            reports.append(SheetReport(f"Sheet {index}", row_count, max_column, headers))
        return tuple(reports)


def validate_import(
    file_path: str | Path,
    *,
    allowed_root: str | Path | None = None,
    limits: ImportLimits | None = None,
) -> ImportReport:
    """Validate an upload and return metadata; never extracts or executes it."""

    policy = limits or ImportLimits()
    path = _safe_path(Path(file_path), Path(allowed_root) if allowed_root is not None else None)
    size = path.stat().st_size
    if size == 0:
        raise UploadValidationError("empty_file", "Upload is empty.")
    if size > policy.max_file_bytes:
        raise UploadValidationError("file_too_large", "Upload exceeds the configured byte limit.")
    suffix = path.suffix.casefold()
    if suffix == ".csv":
        media_type = "text/csv"
        sheets = _validate_csv(path, policy)
    elif suffix == ".xlsx":
        with path.open("rb") as handle:
            if handle.read(4) != b"PK\x03\x04":
                raise UploadValidationError("signature", "XLSX file signature is invalid.")
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        sheets = _validate_xlsx(path, policy)
    elif suffix in {".cdx", ".cdd", ".xml"}:
        media_type, sheets = _validate_crawl_file(path, policy, suffix=suffix)
    elif suffix in {".xlsm", ".xlsb", ".xls", ".zip"}:
        raise UploadValidationError(
            "active_or_legacy",
            "Macro-enabled, binary, legacy, and generic ZIP uploads are not permitted.",
        )
    else:
        raise UploadValidationError(
            "unsupported_type",
            "Only CSV, XLSX, CDX, CDD, and XML evidence imports are supported.",
        )
    return ImportReport(path.name, media_type, size, _hash_file(path), sheets)
