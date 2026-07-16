from __future__ import annotations

import csv
import json
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from exporters.manifest import PackageManifest, build_zip
from scripts.verify_package import ROOT, Verifier

APPROVED_DOMAIN = "kakawachocolates.com.au"


def _json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def package_root() -> Iterator[Path]:
    """Keep all verifier scratch data inside the user-authorised project root."""
    with tempfile.TemporaryDirectory(prefix="verifier-", dir=ROOT / "tests") as scratch:
        package = Path(scratch) / "Kakawa_Chocolates_Enterprise_SEO_Package_v19"
        package.mkdir()
        yield package


def _snapshot(*, target_url: str, evidence_ids: list[str] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "run": {
            "id": "RUN-KAKAWA-V19-TEST",
            "evidence_as_of": "2026-07-15",
            "rule_version": "2026.07.1",
            "profile": "enterprise",
            "locale": "en-AU",
        },
        "evidence": [
            {
                "id": "EV-001",
                "source_id": "SRC-CRAWL",
                "captured_at": "2026-07-15T05:30:00Z",
                "scope": APPROVED_DOMAIN,
                "confidence": 1.0,
                "rule_version": "2026.07.1",
            }
        ],
        "content_assets": [
            {
                "id": "CNT-001",
                "title": "Chocolate gift guide",
                "target_url": target_url,
                "intent": "commercial investigation",
                "approval_state": "approved",
                "claims": [
                    {
                        "claim": "The approved-domain gifts collection was observed.",
                        "evidence_ids": evidence_ids or ["EV-001"],
                        "validation": "supported",
                        "confidence": 1.0,
                        "as_of_date": "2026-07-15",
                    }
                ],
            }
        ],
    }


def _qa() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "run_id": "RUN-KAKAWA-V19-TEST",
        "qa": {
            "release_status": "PASS_FOR_REVIEW",
            "critical_failures": 0,
            "high_failures": 0,
            "wrong_domain_urls": 0,
            "unsupported_claims": 0,
            "unapproved_risky_assets": 0,
            "duplicate_normalized_pages": 0,
            "release_statement": (
                "Deterministic package checks passed; final human approval remains required."
            ),
        },
    }


def _approval_ledger(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=(
                "asset_id",
                "asset_type",
                "included_in_deployment",
                "approval_status",
                "approver_role",
                "artifact_hash_scope",
            ),
        )
        writer.writeheader()
        writer.writerow(
            {
                "asset_id": "CAN-001",
                "asset_type": "canonical_proposal",
                "included_in_deployment": "true",
                "approval_status": "approved",
                "approver_role": "agency_admin",
                "artifact_hash_scope": "exact_version",
            }
        )
    return path


def _build_package(
    package: Path,
    *,
    target_url: str = f"https://{APPROVED_DOMAIN}/collections/gifts",
    evidence_ids: list[str] | None = None,
    strategy_text: str = (
        "# Evidence-led direction\n\n"
        "Improve the observed gifts collection only after the canonical decision is approved.\n"
    ),
    include_disavow: bool = False,
) -> tuple[PackageManifest, Path, Path]:
    files = [
        _json(
            package / "01_Evidence_and_Audits" / "canonical_evidence_snapshot.json",
            _snapshot(target_url=target_url, evidence_ids=evidence_ids),
        ),
        _text(package / "02_Strategy" / "evidence_led_direction.md", strategy_text),
        _approval_ledger(package / "04_Deployment_Assets" / "approval_ledger.csv"),
        _text(
            package / "05_Content" / "gift_guide.html",
            (
                '<!doctype html><html lang="en-AU"><head><meta charset="utf-8">'
                "<title>Chocolate gift guide</title></head><body><main>"
                f"<h1>Chocolate gifts</h1><p>Approved target: {target_url}</p>"
                "</main></body></html>\n"
            ),
        ),
        _json(package / "06_QA_and_Manifest" / "Kakawa_QA_v19.json", _qa()),
        _json(
            package / "06_QA_and_Manifest" / "render-verification.json",
            {
                "schema_version": "1.0",
                "critical_failures": 0,
                "high_failures": 0,
                "checked_at": "2026-07-15T06:00:00Z",
                "method": "structural and raster inspection",
            },
        ),
    ]
    if include_disavow:
        files.append(
            _text(
                package / "04_Deployment_Assets" / "disavow.txt",
                "# Presence is forbidden without the full evidence and approval gate.\n",
            )
        )

    manifest = PackageManifest(
        package_id="PKG-KAKAWA-V19-TEST",
        project_id="PROJECT-KAKAWA",
        run_id="RUN-KAKAWA-V19-TEST",
        rule_version="2026.07.1",
        generated_at="2026-07-15T06:00:00+00:00",
        approved_domains=(APPROVED_DOMAIN,),
        evidence_as_of="2026-07-15",
        reconciliation={"content_assets": 1, "evidence_records": 1},
        limitations=["Private analytics remain explicitly unavailable in this test fixture."],
    )
    for file in files:
        manifest.add_file(
            package,
            file,
            artifact_type=file.suffix.casefold().lstrip(".") or "payload",
            title=file.stem.replace("_", " ").replace("-", " ").title(),
            source_records=("EV-001",),
        )
    manifest_path = manifest.write(package)
    checksum_path = manifest.write_checksums(package, manifest_path)
    return manifest, manifest_path, checksum_path


def _codes(result: dict[str, Any]) -> set[str]:
    return {issue["code"] for issue in result["issues"]}


def _assert_blocked(result: dict[str, Any], code: str) -> None:
    assert result["result"] == "FAIL"
    assert result["issue_count"] > 0
    assert code in _codes(result)


def test_realistic_package_and_adjacent_archive_pass(package_root: Path) -> None:
    manifest, _, _ = _build_package(package_root)
    archive, archive_checksum = build_zip(
        package_root, package_root.parent / f"{package_root.name}.zip"
    )

    result = Verifier(package_root, archive).run()

    assert result == {
        "schema_version": "1.0",
        "verified_at": result["verified_at"],
        "package": package_root.name,
        "result": "PASS",
        "critical_failures": 0,
        "high_failures": 0,
        "issue_count": 0,
        "issues": [],
        "file_count": len(manifest.entries),
        "zip_verified": True,
    }
    assert archive.is_file()
    assert archive_checksum.is_file()


def test_wrong_domain_url_is_a_critical_failure(package_root: Path) -> None:
    _build_package(package_root, target_url="https://kakawachocolates.com/collections/gifts")

    result = Verifier(package_root, None).run()

    _assert_blocked(result, "wrong_domain")
    issues = [issue for issue in result["issues"] if issue["code"] == "wrong_domain"]
    assert issues
    assert all(issue["severity"] == "Critical" for issue in issues)
    assert any("kakawachocolates.com/collections/gifts" in issue["message"] for issue in issues)


def test_placeholder_blocks_release(package_root: Path) -> None:
    _build_package(
        package_root,
        strategy_text="# Evidence-led direction\n\nTODO: insert final recommendation here.\n",
    )

    result = Verifier(package_root, None).run()

    _assert_blocked(result, "placeholder")


def test_checksum_and_manifest_drift_are_both_detected(package_root: Path) -> None:
    _build_package(package_root)
    strategy = package_root / "02_Strategy" / "evidence_led_direction.md"
    strategy.write_text(
        strategy.read_text(encoding="utf-8") + "\nChanged after package approval.\n",
        encoding="utf-8",
        newline="\n",
    )

    result = Verifier(package_root, None).run()

    _assert_blocked(result, "manifest_drift")
    assert "checksum_drift" in _codes(result)
    assert result["critical_failures"] >= 2


def test_claim_with_missing_evidence_blocks_release(package_root: Path) -> None:
    _build_package(package_root, evidence_ids=["EV-DOES-NOT-EXIST"])

    result = Verifier(package_root, None).run()

    _assert_blocked(result, "claim_evidence")
    issue = next(issue for issue in result["issues"] if issue["code"] == "claim_evidence")
    assert "EV-DOES-NOT-EXIST" in issue["message"]


def test_unsafe_archive_member_is_rejected(package_root: Path) -> None:
    _build_package(package_root)
    archive, _ = build_zip(package_root, package_root.parent / f"{package_root.name}.zip")
    with zipfile.ZipFile(archive, "a") as package_zip:
        package_zip.writestr("../outside.txt", b"unsafe")

    result = Verifier(package_root, archive).run()

    _assert_blocked(result, "zip_unsafe")
    assert any("Unsafe ZIP member" in issue["message"] for issue in result["issues"])


def test_duplicate_archive_member_is_rejected(package_root: Path) -> None:
    _build_package(package_root)
    archive, _ = build_zip(package_root, package_root.parent / f"{package_root.name}.zip")
    member = f"{package_root.name}/02_Strategy/evidence_led_direction.md"
    with (
        pytest.warns(UserWarning, match="Duplicate name"),
        zipfile.ZipFile(archive, "a") as package_zip,
    ):
        package_zip.writestr(member, b"duplicate")

    result = Verifier(package_root, archive).run()

    _assert_blocked(result, "zip_unsafe")
    assert any("Duplicate ZIP member" in issue["message"] for issue in result["issues"])


def test_untracked_file_blocks_release(package_root: Path) -> None:
    _build_package(package_root)
    _text(package_root / "02_Strategy" / "untracked.md", "Unmanifested package payload.\n")

    result = Verifier(package_root, None).run()

    _assert_blocked(result, "manifest_coverage")
    issue = next(issue for issue in result["issues"] if issue["code"] == "manifest_coverage")
    assert "02_Strategy/untracked.md" in issue["message"]


def test_disavow_payload_is_forbidden_even_when_manifested(package_root: Path) -> None:
    _build_package(package_root, include_disavow=True)

    result = Verifier(package_root, None).run()

    _assert_blocked(result, "disavow")
    issue = next(issue for issue in result["issues"] if issue["code"] == "disavow")
    assert issue["severity"] == "Critical"
