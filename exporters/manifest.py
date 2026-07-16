"""Package manifests, checksums, and deterministic ZIP assembly.

The manifest intentionally does not hash itself. It enumerates every client-facing
payload, then ``checksums.sha256`` covers those payloads plus the manifest. The
adjacent ZIP checksum covers the final archive.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import zipfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

CONTROL_FILES = {"06_QA_and_Manifest/package-manifest.json", "06_QA_and_Manifest/checksums.sha256"}


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    path: str
    media_type: str
    bytes: int
    sha256: str
    artifact_type: str
    title: str
    source_records: tuple[str, ...] = ()
    derivative_of: str | None = None
    approval_state: str = "approved"
    as_of_date: str | None = None


@dataclass(slots=True)
class PackageManifest:
    package_id: str
    project_id: str
    run_id: str
    rule_version: str
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    approved_domains: tuple[str, ...] = ()
    evidence_as_of: str | None = None
    entries: list[ManifestEntry] = field(default_factory=list)
    reconciliation: dict[str, int | float | str | None] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def add_file(
        self,
        package_root: Path,
        path: Path,
        *,
        artifact_type: str,
        title: str,
        source_records: Iterable[str] = (),
        derivative_of: str | None = None,
        approval_state: str = "approved",
        as_of_date: str | None = None,
    ) -> ManifestEntry:
        root = package_root.resolve()
        resolved = path.resolve()
        if not resolved.is_file() or not resolved.is_relative_to(root):
            raise ValueError(f"Artifact must be a file inside the package: {path}")
        relative = PurePosixPath(resolved.relative_to(root)).as_posix()
        if relative in CONTROL_FILES:
            raise ValueError("Control files are added after manifest enumeration")
        media_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        entry = ManifestEntry(
            path=relative,
            media_type=media_type,
            bytes=resolved.stat().st_size,
            sha256=self.sha256(resolved),
            artifact_type=artifact_type,
            title=title,
            source_records=tuple(source_records),
            derivative_of=derivative_of,
            approval_state=approval_state,
            as_of_date=as_of_date or self.evidence_as_of,
        )
        self.entries.append(entry)
        return entry

    def assert_integrity(self, package_root: Path) -> None:
        package_root = package_root.resolve()
        seen_paths: set[str] = set()
        hashes: dict[str, str] = {}
        entries_by_path = {entry.path: entry for entry in self.entries}
        for entry in self.entries:
            if entry.path in seen_paths:
                raise ValueError(f"Duplicate manifest path: {entry.path}")
            seen_paths.add(entry.path)
            candidate = (package_root / Path(entry.path)).resolve()
            if not candidate.is_relative_to(package_root):
                raise ValueError(f"Manifest path escaped package: {entry.path}")
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError(f"Manifest payload is missing or symbolic: {entry.path}")
            actual = self.sha256(candidate)
            if actual != entry.sha256:
                raise ValueError(f"Checksum drift: {entry.path}")
            if actual in hashes:
                raise ValueError(
                    f"Unexplained byte-identical files: {hashes[actual]} and {entry.path}"
                )
            hashes[actual] = entry.path
            if entry.derivative_of:
                source = entries_by_path.get(entry.derivative_of)
                if source is None:
                    raise ValueError(
                        f"Derivative source is absent for {entry.path}: {entry.derivative_of}"
                    )
                if source.path == entry.path:
                    raise ValueError(f"Artifact cannot derive from itself: {entry.path}")
                if Path(source.path).suffix.casefold() == Path(entry.path).suffix.casefold():
                    raise ValueError(
                        f"Cross-format derivative must change format: {entry.path}"
                    )

        actual_paths: set[str] = set()
        for candidate in package_root.rglob("*"):
            if candidate.is_symlink():
                raise ValueError(f"Symbolic paths are forbidden in packages: {candidate}")
            if not candidate.is_file():
                continue
            relative = PurePosixPath(candidate.relative_to(package_root)).as_posix()
            if relative not in CONTROL_FILES:
                actual_paths.add(relative)
        if actual_paths != seen_paths:
            untracked = sorted(actual_paths - seen_paths)
            missing = sorted(seen_paths - actual_paths)
            raise ValueError(
                f"Manifest coverage mismatch; untracked={untracked}, missing={missing}"
            )

    def write(self, package_root: Path) -> Path:
        self.assert_integrity(package_root)
        output = package_root / "06_QA_and_Manifest" / "package-manifest.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "1.0",
            "package_id": self.package_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "rule_version": self.rule_version,
            "generated_at": self.generated_at,
            "approved_domains": list(self.approved_domains),
            "evidence_as_of": self.evidence_as_of,
            "manifest_policy": {
                "scope": "Every client-facing payload; control files are covered by checksums.sha256.",
                "duplicate_policy": "Byte-identical payloads are forbidden.",
                "derivatives": "Cross-format derivatives declare derivative_of.",
            },
            "reconciliation": self.reconciliation,
            "limitations": self.limitations,
            "files": [asdict(entry) for entry in sorted(self.entries, key=lambda item: item.path)],
        }
        output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return output

    def write_checksums(self, package_root: Path, manifest_path: Path) -> Path:
        output = package_root / "06_QA_and_Manifest" / "checksums.sha256"
        files = [package_root / Path(entry.path) for entry in self.entries] + [manifest_path]
        lines = [
            f"{self.sha256(path)}  {PurePosixPath(path.relative_to(package_root)).as_posix()}"
            for path in sorted(files, key=lambda item: item.as_posix())
        ]
        output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        return output


def build_zip(package_root: Path, zip_path: Path) -> tuple[Path, Path]:
    """Build a deterministic archive and adjacent SHA-256 file."""
    package_root = package_root.resolve()
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    candidates = sorted(
        (path for path in package_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(package_root).as_posix(),
    )
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in candidates:
            relative = PurePosixPath(package_root.name) / PurePosixPath(
                source.relative_to(package_root)
            )
            info = zipfile.ZipInfo(str(relative), date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            with source.open("rb") as stream:
                archive.writestr(info, stream.read())
    checksum = zip_path.with_suffix(zip_path.suffix + ".sha256")
    checksum.write_text(
        f"{PackageManifest.sha256(zip_path)}  {zip_path.name}\n", encoding="utf-8", newline="\n"
    )
    return zip_path, checksum


def verify_zip_members(zip_path: Path, expected_root: str) -> None:
    with zipfile.ZipFile(zip_path, "r") as archive:
        names: set[str] = set()
        total_uncompressed = 0
        for item in archive.infolist():
            raw_name = item.filename
            if "\\" in raw_name or "\x00" in raw_name:
                raise ValueError(f"Unsafe ZIP member: {raw_name}")
            normalized = PurePosixPath(raw_name)
            if normalized.is_absolute() or ".." in normalized.parts:
                raise ValueError(f"Unsafe ZIP member: {item.filename}")
            if any(":" in part for part in normalized.parts):
                raise ValueError(f"Drive-qualified ZIP member: {item.filename}")
            if not normalized.parts or normalized.parts[0] != expected_root:
                raise ValueError(f"Unexpected ZIP root: {item.filename}")
            canonical = normalized.as_posix()
            if canonical in names:
                raise ValueError(f"Duplicate ZIP member: {item.filename}")
            names.add(canonical)
            if item.file_size > 250 * 1024 * 1024:
                raise ValueError(f"Oversized ZIP member: {item.filename}")
            total_uncompressed += item.file_size
            if total_uncompressed > 1_000 * 1024 * 1024:
                raise ValueError("ZIP expansion exceeds package safety limit")
            if item.file_size and not item.compress_size:
                raise ValueError(f"Invalid ZIP compression size: {item.filename}")
            if item.compress_size and item.file_size / item.compress_size > 1_000:
                raise ValueError(f"Suspicious ZIP expansion ratio: {item.filename}")

