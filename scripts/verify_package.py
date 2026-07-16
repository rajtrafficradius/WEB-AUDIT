"""Fail-closed verifier for the finalized v19 package and archive."""

from __future__ import annotations

import argparse
import csv
import json
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from xml.etree.ElementTree import ParseError

from defusedxml.ElementTree import fromstring
from pypdf import PdfReader

from exporters.manifest import CONTROL_FILES, PackageManifest, verify_zip_members

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE = ROOT / "exports" / "Kakawa_Chocolates_Enterprise_SEO_Package_v19"
TEXT_SUFFIXES = {".csv", ".html", ".htm", ".json", ".md", ".txt", ".xml"}
OFFICE_PART = {
    ".docx": "word/document.xml",
    ".xlsx": "xl/workbook.xml",
    ".pptx": "ppt/presentation.xml",
}
URL_RE = re.compile(r"https?://[^\s\"'<>\]\[(){}]+", re.I)
PATH_RE = re.compile(r"(?:file:/+|(?<![A-Za-z0-9])[A-Za-z]:[\\/]|/(?:Users|home)/[^/\s]+/)", re.I)
PLACEHOLDER_RE = re.compile(
    r"(?:\bTODO\b|\bTBD\b|\bFIXME\b|lorem\s+ipsum|example\.com|insert\s+[^\n]{0,30}\s+here)",
    re.I,
)


class Verifier:
    def __init__(self, package: Path, archive: Path | None) -> None:
        self.package = package.resolve()
        self.archive = archive.resolve() if archive else None
        self.issues: list[dict[str, str]] = []
        self.manifest: dict[str, Any] = {}
        self.zip_verified = False

    def fail(self, code: str, path: str, message: str, severity: str = "High") -> None:
        self.issues.append(
            {"severity": severity, "code": code, "path": path, "message": message}
        )

    def load_manifest(self) -> None:
        path = self.package / "06_QA_and_Manifest" / "package-manifest.json"
        try:
            self.manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.fail("manifest_invalid", path.name, type(exc).__name__, "Critical")
            return
        if self.manifest.get("schema_version") != "1.0":
            self.fail("manifest_schema", path.name, "Expected schema 1.0", "Critical")
        if not self.manifest.get("approved_domains"):
            self.fail("domain_boundary", path.name, "Approved domains are absent", "Critical")

    def manifest_and_checksums(self) -> None:
        if not self.manifest:
            return
        entries = self.manifest.get("files") or []
        by_path = {entry.get("path"): entry for entry in entries}
        if None in by_path or len(by_path) != len(entries):
            self.fail("manifest_paths", "package-manifest.json", "Duplicate or empty path", "Critical")
            return
        actual = {
            path.relative_to(self.package).as_posix()
            for path in self.package.rglob("*")
            if path.is_file() and path.relative_to(self.package).as_posix() not in CONTROL_FILES
        }
        if actual != set(by_path):
            self.fail(
                "manifest_coverage",
                "package-manifest.json",
                f"untracked={sorted(actual - set(by_path))}; missing={sorted(set(by_path) - actual)}",
                "Critical",
            )
        hashes: dict[str, str] = {}
        for relative, entry in by_path.items():
            candidate = (self.package / Path(relative)).resolve()
            if not candidate.is_relative_to(self.package) or candidate.is_symlink():
                self.fail("path_escape", str(relative), "Unsafe manifest path", "Critical")
                continue
            if not candidate.is_file():
                continue
            digest = PackageManifest.sha256(candidate)
            if digest != entry.get("sha256") or candidate.stat().st_size != entry.get("bytes"):
                self.fail("manifest_drift", str(relative), "Size or hash mismatch", "Critical")
            if digest in hashes:
                self.fail("duplicate_file", str(relative), f"Byte-identical to {hashes[digest]}")
            hashes[digest] = str(relative)
            source = entry.get("derivative_of")
            if source and source not in by_path:
                self.fail("derivative_source", str(relative), f"Unknown source {source}")
            elif source and Path(source).suffix.casefold() == Path(relative).suffix.casefold():
                self.fail("derivative_format", str(relative), "Derivative kept the same format")

        checksum_path = self.package / "06_QA_and_Manifest" / "checksums.sha256"
        recorded: dict[str, str] = {}
        try:
            lines = checksum_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            self.fail("checksums_missing", checksum_path.name, type(exc).__name__, "Critical")
            return
        for line in lines:
            match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
            if not match:
                self.fail("checksum_line", checksum_path.name, repr(line))
                continue
            digest, relative = match.groups()
            if relative in recorded:
                self.fail("checksum_duplicate", checksum_path.name, relative)
            recorded[relative] = digest
        expected = set(by_path) | {"06_QA_and_Manifest/package-manifest.json"}
        if set(recorded) != expected:
            self.fail("checksum_coverage", checksum_path.name, "Paths differ from manifest", "Critical")
        for relative, digest in recorded.items():
            candidate = (self.package / relative).resolve()
            if not candidate.is_file() or PackageManifest.sha256(candidate) != digest:
                self.fail("checksum_drift", relative, "Checksum mismatch", "Critical")

    def extract_text(self, path: Path) -> str:
        suffix = path.suffix.casefold()
        if suffix in TEXT_SUFFIXES:
            try:
                return path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError) as exc:
                self.fail("text_decode", path.name, type(exc).__name__)
                return ""
        if suffix in OFFICE_PART:
            if not zipfile.is_zipfile(path):
                self.fail("ooxml_invalid", path.name, "Not a ZIP", "Critical")
                return ""
            parts: list[str] = []
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if OFFICE_PART[suffix] not in names:
                    self.fail("ooxml_part", path.name, f"Missing {OFFICE_PART[suffix]}", "Critical")
                if any(name.casefold().endswith("vbaproject.bin") for name in names):
                    self.fail("macro", path.name, "Macro payload found", "Critical")
                for name in names:
                    if not name.endswith((".xml", ".rels")):
                        continue
                    try:
                        element = fromstring(archive.read(name))
                    except ParseError:
                        continue
                    parts.extend(text for text in element.itertext() if text)
                    for node in element.iter():
                        for key, value in node.attrib.items():
                            if key.casefold().endswith("target") and value.startswith("http"):
                                parts.append(value)
            return "\n".join(parts)
        if suffix == ".pdf":
            try:
                reader = PdfReader(path)
                if not reader.pages:
                    raise ValueError("no pages")
                return "\n".join(page.extract_text() or "" for page in reader.pages)
            except Exception as exc:
                self.fail("pdf_invalid", path.name, f"{type(exc).__name__}: {exc}", "Critical")
        return ""

    def scan_payloads(self) -> None:
        allowed = {value.casefold().rstrip(".") for value in self.manifest.get("approved_domains", [])}
        for path in sorted(self.package.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.package).as_posix()
            text = self.extract_text(path)
            if not text:
                continue
            if "\ufffd" in text or any(marker in text for marker in ("Â·", "â€", "Ã")):
                self.fail("mojibake", relative, "Encoding corruption marker")
            if PATH_RE.search(text):
                self.fail("machine_path", relative, "Machine-specific path")
            match = PLACEHOLDER_RE.search(text)
            if match:
                self.fail("placeholder", relative, repr(match.group(0)))
            for value in URL_RE.findall(text):
                value = value.rstrip(".,;:")
                host = (urlsplit(value).hostname or "").casefold().rstrip(".")
                if not any(host == domain or host.endswith("." + domain) for domain in allowed):
                    self.fail("wrong_domain", relative, value, "Critical")
            if path.suffix.casefold() in {".html", ".htm"}:
                if re.search(r"<(?:script|img)[^>]+src\s*=\s*[\"']https?://", text, re.I):
                    self.fail("external_asset", relative, "External script or image")
                if re.search(r"<link[^>]+href\s*=\s*[\"']https?://", text, re.I):
                    self.fail("external_stylesheet", relative, "External stylesheet")

    def qa_claims_approvals(self) -> None:
        qa_path = self.package / "06_QA_and_Manifest" / "Kakawa_QA_v19.json"
        try:
            qa = json.loads(qa_path.read_text(encoding="utf-8"))["qa"]
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            self.fail("qa_invalid", qa_path.name, type(exc).__name__, "Critical")
            return
        zero_fields = (
            "critical_failures",
            "high_failures",
            "wrong_domain_urls",
            "unsupported_claims",
            "unapproved_risky_assets",
            "duplicate_normalized_pages",
        )
        for field in zero_fields:
            if qa.get(field) != 0:
                self.fail("qa_nonzero", qa_path.name, f"{field}={qa.get(field)}", "Critical")
        if qa.get("release_status") != "PASS_FOR_REVIEW":
            self.fail("qa_status", qa_path.name, str(qa.get("release_status")))
        render_path = self.package / "06_QA_and_Manifest" / "render-verification.json"
        try:
            render = json.loads(render_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.fail("render_qa", render_path.name, type(exc).__name__, "Critical")
        else:
            if render.get("critical_failures") or render.get("high_failures"):
                self.fail("render_qa", render_path.name, "Render failures are non-zero", "Critical")

        snapshot_path = self.package / "01_Evidence_and_Audits" / "canonical_evidence_snapshot.json"
        try:
            data = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.fail("snapshot_invalid", snapshot_path.name, type(exc).__name__, "Critical")
            return
        if not isinstance(data, dict):
            self.fail("snapshot_invalid", snapshot_path.name, "Expected an object", "Critical")
            return
        evidence = {
            item.get("id")
            for item in data.get("evidence", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        targets: set[str] = set()
        for asset in data.get("content_assets", []):
            if not isinstance(asset, dict):
                self.fail("content_asset_invalid", snapshot_path.name, "Expected an object", "Critical")
                continue
            asset_id = str(asset.get("id") or "unknown-content-asset")
            target_url = asset.get("target_url")
            if not isinstance(target_url, str) or not target_url:
                self.fail("content_target_invalid", asset_id, "Missing target URL", "Critical")
                continue
            if target_url in targets:
                self.fail("content_overlap", asset_id, target_url, "Critical")
            targets.add(target_url)
            for claim in asset.get("claims", []):
                if not isinstance(claim, dict):
                    self.fail("claim_invalid", asset_id, "Expected an object", "Critical")
                    continue
                if claim.get("validation") != "supported":
                    self.fail("unsupported_claim", asset_id, str(claim.get("claim", "")), "Critical")
                claim_evidence = claim.get("evidence_ids", [])
                if not isinstance(claim_evidence, list):
                    self.fail("claim_evidence", asset_id, "Evidence IDs must be a list", "Critical")
                    continue
                missing = set(claim_evidence) - evidence
                if missing:
                    self.fail("claim_evidence", asset_id, str(sorted(missing)), "Critical")
        ledger = self.package / "04_Deployment_Assets" / "approval_ledger.csv"
        try:
            with ledger.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    included = row.get("included_in_deployment", "").casefold() == "true"
                    if included and row.get("approval_status") != "approved":
                        self.fail("unapproved_risky_asset", ledger.name, str(row), "Critical")
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            self.fail("approval_ledger_invalid", ledger.name, type(exc).__name__, "Critical")
        if any(path.name.casefold() == "disavow.txt" for path in self.package.rglob("*")):
            self.fail("disavow", "disavow.txt", "Disavow payload present", "Critical")

    def verify_archive(self) -> None:
        if not self.archive:
            return
        starting_issue_count = len(self.issues)
        try:
            verify_zip_members(self.archive, self.package.name)
        except (ValueError, zipfile.BadZipFile, OSError) as exc:
            self.fail("zip_unsafe", self.archive.name, str(exc), "Critical")
            return
        checksum = self.archive.with_suffix(self.archive.suffix + ".sha256")
        try:
            digest, name = checksum.read_text(encoding="utf-8").strip().split("  ", 1)
        except (OSError, ValueError) as exc:
            self.fail("zip_checksum", checksum.name, type(exc).__name__, "Critical")
            return
        if name != self.archive.name or digest != PackageManifest.sha256(self.archive):
            self.fail("zip_checksum", checksum.name, "Hash or name mismatch", "Critical")
        with zipfile.ZipFile(self.archive) as archive:
            expected = {
                f"{self.package.name}/{path.relative_to(self.package).as_posix()}"
                for path in self.package.rglob("*")
                if path.is_file()
            }
            if set(archive.namelist()) != expected:
                self.fail("zip_coverage", self.archive.name, "Members differ from unpacked package", "Critical")
        if len(self.issues) == starting_issue_count:
            self.zip_verified = True

    def run(self) -> dict[str, Any]:
        if not self.package.is_dir() or not self.package.is_relative_to(ROOT):
            self.fail("package_root", str(self.package), "Missing or outside project", "Critical")
        else:
            self.load_manifest()
            self.manifest_and_checksums()
            self.scan_payloads()
            self.qa_claims_approvals()
            self.verify_archive()
        critical = sum(item["severity"] == "Critical" for item in self.issues)
        high = sum(item["severity"] == "High" for item in self.issues)
        return {
            "schema_version": "1.0",
            "verified_at": datetime.now(UTC).isoformat(),
            "package": self.package.name,
            "result": "PASS" if not self.issues else "FAIL",
            "critical_failures": critical,
            "high_failures": high,
            "issue_count": len(self.issues),
            "issues": self.issues,
            "file_count": len(self.manifest.get("files", [])),
            "zip_verified": self.zip_verified,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", nargs="?", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--zip", dest="archive", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = Verifier(args.package, args.archive).run()
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    print(rendered)
    if args.report:
        if not args.report.resolve().parent.is_relative_to(ROOT):
            parser.error("Report must remain inside the project root")
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8", newline="\n")
    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
