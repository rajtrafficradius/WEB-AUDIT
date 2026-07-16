from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from exporters.manifest import PackageManifest, build_zip, verify_zip_members


def _manifest() -> PackageManifest:
    return PackageManifest(
        package_id="PKG-KAKAWA-V19",
        project_id="PROJECT-KAKAWA",
        run_id="RUN-KAKAWA-V19-TEST",
        rule_version="2026.07.1",
        generated_at="2026-07-15T06:00:00+00:00",
        approved_domains=("kakawachocolates.com.au",),
        evidence_as_of="2026-07-15",
        reconciliation={"findings": 1, "actions": 1},
        limitations=["Private analytics unavailable without approved credentials."],
    )


def _write(root: Path, relative: str, content: bytes) -> Path:
    output = root / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    return output


def test_manifest_covers_every_payload_and_checksums_are_reproducible(tmp_path: Path) -> None:
    package = tmp_path / "Kakawa_Chocolates_Enterprise_SEO_Package_v19"
    pdf = _write(package, "00_Executive/Kakawa_Executive_Report.pdf", b"pdf-v19")
    csv = _write(package, "03_Action_Plan/Kakawa_Action_Plan.csv", b"id,action\nA1,Review\n")
    manifest = _manifest()
    manifest.add_file(
        package,
        csv,
        artifact_type="action_plan",
        title="Canonical action plan",
        source_records=("ACT-001",),
    )
    manifest.add_file(
        package,
        pdf,
        artifact_type="executive_report",
        title="Executive report",
        source_records=("EV-001",),
    )

    manifest_path = manifest.write(package)
    checksum_path = manifest.write_checksums(package, manifest_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert [item["path"] for item in payload["files"]] == [
        "00_Executive/Kakawa_Executive_Report.pdf",
        "03_Action_Plan/Kakawa_Action_Plan.csv",
    ]
    assert payload["approved_domains"] == ["kakawachocolates.com.au"]
    assert payload["evidence_as_of"] == "2026-07-15"
    assert all(item["as_of_date"] == "2026-07-15" for item in payload["files"])

    checksum_lines = checksum_path.read_text(encoding="utf-8").splitlines()
    assert len(checksum_lines) == 3
    assert checksum_lines == sorted(checksum_lines, key=lambda line: line.split("  ", 1)[1])
    for line in checksum_lines:
        expected, relative = line.split("  ", 1)
        assert expected == hashlib.sha256((package / relative).read_bytes()).hexdigest()


def test_manifest_rejects_untracked_payloads(tmp_path: Path) -> None:
    package = tmp_path / "package"
    tracked = _write(package, "00_Executive/tracked.pdf", b"tracked")
    _write(package, "02_Strategy/orphan.docx", b"not in manifest")
    manifest = _manifest()
    manifest.add_file(package, tracked, artifact_type="report", title="Tracked")

    with pytest.raises(ValueError, match="[Uu]ntracked|[Uu]nmanifested"):
        manifest.assert_integrity(package)


def test_manifest_rejects_duplicate_paths_identical_bytes_and_checksum_drift(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    first = _write(package, "00_Executive/report.pdf", b"same bytes")
    second = _write(package, "02_Strategy/strategy.pdf", b"same bytes")

    duplicate_paths = _manifest()
    duplicate_paths.add_file(package, first, artifact_type="report", title="First")
    duplicate_paths.add_file(package, first, artifact_type="report", title="First again")
    with pytest.raises(ValueError, match="Duplicate manifest path"):
        duplicate_paths.assert_integrity(package)

    identical = _manifest()
    identical.add_file(package, first, artifact_type="report", title="First")
    identical.add_file(package, second, artifact_type="report", title="Second")
    with pytest.raises(ValueError, match="byte-identical"):
        identical.assert_integrity(package)

    drift = _manifest()
    drift.add_file(package, first, artifact_type="report", title="First")
    first.write_bytes(b"changed after enumeration")
    with pytest.raises(ValueError, match="Checksum drift"):
        drift.assert_integrity(package)


def test_manifest_rejects_outside_control_and_missing_files(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    outside = _write(tmp_path, "outside.txt", b"outside")
    manifest = _manifest()

    with pytest.raises(ValueError, match="inside the package"):
        manifest.add_file(package, outside, artifact_type="unsafe", title="Unsafe")

    control = _write(package, "06_QA_and_Manifest/package-manifest.json", b"{}")
    with pytest.raises(ValueError, match="Control files"):
        manifest.add_file(package, control, artifact_type="control", title="Control")

    missing = package / "00_Executive/missing.pdf"
    with pytest.raises(ValueError, match="inside the package"):
        manifest.add_file(package, missing, artifact_type="report", title="Missing")


def test_manifest_validates_derivative_references(tmp_path: Path) -> None:
    package = tmp_path / "package"
    source = _write(package, "02_Strategy/strategy.docx", b"docx")
    derivative = _write(package, "02_Strategy/strategy.pdf", b"pdf")
    manifest = _manifest()
    manifest.add_file(
        package,
        source,
        artifact_type="strategy_source",
        title="Strategy source",
    )
    manifest.add_file(
        package,
        derivative,
        artifact_type="strategy_pdf",
        title="Strategy PDF",
        derivative_of="02_Strategy/strategy.docx",
    )
    manifest.assert_integrity(package)

    broken = _manifest()
    broken.add_file(
        package,
        derivative,
        artifact_type="strategy_pdf",
        title="Strategy PDF",
        derivative_of="02_Strategy/missing.docx",
    )
    with pytest.raises(ValueError, match="[Dd]erivative"):
        broken.assert_integrity(package)


def test_deterministic_zip_has_fixed_order_metadata_and_adjacent_checksum(tmp_path: Path) -> None:
    package = tmp_path / "Kakawa_Chocolates_Enterprise_SEO_Package_v19"
    _write(package, "06_QA_and_Manifest/checksums.sha256", b"abc  one\n")
    _write(package, "00_Executive/deck.html", b"<!doctype html>")
    _write(package, "03_Action_Plan/plan.csv", b"id\nACT-001\n")

    first, first_checksum = build_zip(package, tmp_path / "first.zip")
    second, second_checksum = build_zip(package, tmp_path / "second.zip")

    assert first.read_bytes() == second.read_bytes()
    expected_digest = hashlib.sha256(first.read_bytes()).hexdigest()
    assert first_checksum.read_text(encoding="utf-8") == f"{expected_digest}  first.zip\n"
    assert second_checksum.read_text(encoding="utf-8") == f"{expected_digest}  second.zip\n"

    with zipfile.ZipFile(first) as archive:
        infos = archive.infolist()
        expected_names = sorted(
            f"{package.name}/{path.relative_to(package).as_posix()}"
            for path in package.rglob("*")
            if path.is_file()
        )
        assert [info.filename for info in infos] == expected_names
        assert all(info.date_time == (2026, 1, 1, 0, 0, 0) for info in infos)
        assert all((info.external_attr >> 16) & 0o777 == 0o644 for info in infos)
        assert len({info.filename for info in infos}) == len(infos)

    verify_zip_members(first, package.name)


@pytest.mark.parametrize(
    "member",
    [
        "../outside.txt",
        "/absolute.txt",
        "wrong-root/report.pdf",
        "Kakawa_Package/../../outside.txt",
        "Kakawa_Package/safe\\..\\outside.txt",
        "Kakawa_Package\\..\\outside.txt",
    ],
)
def test_zip_verifier_rejects_unsafe_or_unexpected_members(tmp_path: Path, member: str) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(member, b"unsafe")

    with pytest.raises(ValueError, match="Unsafe|Unexpected"):
        verify_zip_members(archive_path, "Kakawa_Package")


def test_zip_verifier_rejects_duplicate_member_names(tmp_path: Path) -> None:
    archive_path = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Kakawa_Package/report.pdf", b"first")
        archive.writestr("Kakawa_Package/report.pdf", b"second")

    with pytest.raises(ValueError, match="[Dd]uplicate"):
        verify_zip_members(archive_path, "Kakawa_Package")
