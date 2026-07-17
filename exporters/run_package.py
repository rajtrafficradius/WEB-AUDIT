"""Assemble, verify, and persist the client deliverable package for an audit run.

The assembler renders the shared package tree from canonical run data, verifies
every rendered file (encoding, format validity, content safety, reconciliation),
then persists one immutable ZIP artifact plus its manifest and QA records.
"""

from __future__ import annotations

import csv
import re
import tempfile
import zipfile
from collections import Counter
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from django.conf import settings
from django.utils.text import slugify
from pypdf import PdfReader

from app.domain.audit import record_event
from app.domain.constants import ReviewStatus, Severity
from app.domain.models import Artifact, AuditRun, QAResult, User
from app.domain.models import PackageManifest as PackageManifestModel
from app.domain.storage import save_artifact_bytes

from .docx_reports import DOCXReportBuilder
from .html_outputs import content_filename
from .manifest import CONTROL_FILES, PackageManifest, build_zip, verify_zip_members
from .package_builder import _csv as write_csv
from .package_builder import _json as write_json
from .pdf_reports import PDFReportBuilder, write_qa_json

ProgressHook = Callable[[str], None] | None

MACHINE_PATH_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\|/home/|/Users/")
MOJIBAKE_MARKERS = ("�", "â€", "Ã", "Â·")
TEXT_SUFFIXES = {".csv", ".json", ".md", ".txt"}
OOXML_MAIN_PARTS = {
    ".docx": "word/document.xml",
    ".pptx": "ppt/presentation.xml",
    ".xlsx": "xl/workbook.xml",
}
FOLDER_ARTIFACT_TYPES = {
    "01": ("audit_workbook", "approved"),
    "02": ("strategy_document", "approved"),
    "03": ("action_plan", "approved"),
    "04": ("deployment_asset", "withheld_pending_approval"),
    "05": ("content_brief", "withheld_pending_human_approval"),
    "06": ("qa_control", "approved"),
    "07": ("executive_deck", "approved"),
}
ACTION_CSV_HEADERS = [
    "id", "phase", "week", "priority", "action", "owner",
    "effort", "kpi", "approval_class", "status", "notes",
]
SCHEMA_REVIEW_NOTE = "Withheld pending admin approval — populate only verified fields"
PACKAGE_CHECK_VERSION = "1.0.0"


class PackageBuildError(RuntimeError):
    """The rendered package failed one or more verification checks."""

    def __init__(self, failures: list[str]) -> None:
        self.failures = list(failures)
        super().__init__("Package verification failed: " + "; ".join(self.failures))


def _notify(progress: ProgressHook, message: str) -> None:
    if progress is not None:
        progress(message)


def _package_name(data: dict[str, Any]) -> str:
    base = slugify(str(data["client"]["name"]))[:50] or "client"
    stem = "_".join(word.capitalize() for word in base.split("-") if word) or "Client"
    as_of = str(data["run"].get("evidence_as_of") or "").replace("-", "") or "undated"
    return f"{stem}_SEO_Audit_Package_{as_of}"


def _content_docx_name(asset: dict[str, Any]) -> str:
    slug = str(asset.get("slug") or "").strip()
    if slug:
        return f"{asset['id']}_{slug}.docx"
    return f"{content_filename(asset)}.docx"


def _write_markdown(data: dict[str, Any], root: Path) -> Path:
    from exporters.markdown_summary import render_markdown

    content = render_markdown(data)
    if not content.endswith("\n"):
        content += "\n"
    output = root / "AUDIT_RESULTS.md"
    output.write_text(content, encoding="utf-8", newline="\n")
    return output


def _write_csvs(data: dict[str, Any], root: Path) -> None:
    deployment = data.get("deployment") or {}
    write_csv(
        root / "03_Action_Plan" / "16_Week_Action_Plan.csv",
        ACTION_CSV_HEADERS,
        data.get("actions", []),
    )
    write_csv(
        root / "04_Implementation_Deliverables" / "Technical_Fixes" / "Redirect_Map.csv",
        [
            "source_url", "target_url", "status_code", "evidence_id",
            "approval_status", "included_in_deployment", "reason",
        ],
        deployment.get("redirect_candidates") or [],
    )
    qa_folder = root / "06_QA_and_Manifest"
    write_csv(
        qa_folder / "availability_matrix.csv",
        ["id", "label", "kind", "status", "captured_at", "scope", "coverage", "unavailable_reason"],
        data.get("sources", []),
    )
    write_csv(
        qa_folder / "generation_ledger.csv",
        [
            "id", "task", "configured_model", "returned_model", "prompt_version",
            "status", "request_hash", "response_hash", "tokens", "cost", "unavailable_reason",
        ],
        data.get("generation_ledger", []),
    )
    write_csv(
        qa_folder / "evidence_index.csv",
        [
            "id", "source_id", "evidence_type", "observed_value", "original_url",
            "normalized_url", "captured_at", "locale", "scope", "confidence",
            "unavailable_reason",
        ],
        data.get("evidence", []),
    )
    write_csv(
        qa_folder / "issue_register.csv",
        [
            "id", "priority", "priority_score", "category", "rule_id", "rule_version",
            "severity", "title", "description", "impact", "confidence", "reach",
            "effort", "implementation_risk", "approval_class", "as_of_date",
            "evidence_ids", "affected_urls",
        ],
        data.get("findings", []),
    )


def _write_robots_notes(data: dict[str, Any], root: Path) -> Path:
    robots = (data.get("deployment") or {}).get("robots") or {}
    recommendation = str(
        robots.get("recommendation")
        or "Unavailable — no robots recommendation was produced for this run."
    )
    changes = robots.get("deployable_changes") or []
    tally = Counter(
        str(page.get("indexability") or "Unknown") for page in data.get("pages", [])
    )
    lines = [
        "Robots and indexation notes",
        "===========================",
        "",
        f"Run: {data['run']['id']}",
        f"Evidence as of: {data['run'].get('evidence_as_of') or 'Unavailable'}",
        "",
        "Recommendation",
        "--------------",
        recommendation,
        "",
        "Deployable robots.txt changes",
        "-----------------------------",
    ]
    if changes:
        lines.extend(f"- {change}" for change in changes)
    else:
        lines.append("- None proposed for this run.")
    lines += ["", "Observed indexability (crawled pages)", "-------------------------------------"]
    if tally:
        lines.extend(f"- {label}: {count}" for label, count in sorted(tally.items()))
    else:
        lines.append("- Unavailable — no crawled pages were recorded for this run.")
    lines.append("")
    output = (
        root / "04_Implementation_Deliverables" / "Technical_Fixes"
        / "Robots_And_Indexation_Notes.txt"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return output


def _write_schema_files(data: dict[str, Any], root: Path, brand_facts: dict[str, Any]) -> None:
    client = data["client"]
    folder = root / "04_Implementation_Deliverables" / "Schema_Markup"
    url = f"https://{client['domain']}/" if client.get("domain") else None
    organization: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": client["name"],
        "url": url,
        "_review_note": SCHEMA_REVIEW_NOTE,
    }
    summary = str(brand_facts.get("business_summary") or "").strip()
    if summary:
        organization["description"] = summary
    write_json(folder / "Schema_Organization.json", organization)

    profile = str((data.get("project") or {}).get("business_profile") or "").casefold()
    if profile in {"local", "hybrid"}:
        write_json(
            folder / "Schema_LocalBusiness.json",
            {
                "@context": "https://schema.org",
                "@type": "LocalBusiness",
                "name": client["name"],
                "url": url,
                "address": "TEMPLATE — replace with the verified street address before deployment",
                "telephone": "TEMPLATE — replace with the verified phone number before deployment",
                "_template": "Requires verified NAP (name, address, phone); no values were fabricated.",
                "_review_note": SCHEMA_REVIEW_NOTE,
            },
        )
    if profile in {"ecommerce", "hybrid"}:
        write_json(
            folder / "Schema_Product_Template.json",
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "TEMPLATE — verified product name",
                "brand": {"@type": "Brand", "name": client["name"]},
                "offers": {
                    "@type": "Offer",
                    "price": "TEMPLATE — verified price",
                    "priceCurrency": "TEMPLATE — verified currency",
                    "availability": "TEMPLATE — verified availability state",
                },
                "_template": "Populate every field from the verified product catalogue before use.",
                "_review_note": SCHEMA_REVIEW_NOTE,
            },
        )


def _csv_row_count(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return max(0, sum(1 for _ in csv.reader(stream)) - 1)


def _reconcile_package_counts(data: dict[str, Any], root: Path) -> list[str]:
    """Recompute package-side reconciliation numbers from what was actually rendered."""
    qa_folder = root / "06_QA_and_Manifest"
    findings_rows = _csv_row_count(qa_folder / "issue_register.csv")
    recomputed = {
        "content": len(list((root / "05_Content").glob("*.docx"))),
        "evidence": _csv_row_count(qa_folder / "evidence_index.csv"),
        "finding": findings_rows,
        "issue": findings_rows,
        "action": _csv_row_count(root / "03_Action_Plan" / "16_Week_Action_Plan.csv"),
        "source": _csv_row_count(qa_folder / "availability_matrix.csv"),
    }
    failures: list[str] = []
    for entry in (data.get("qa") or {}).get("reconciliation") or []:
        measure = str(entry.get("measure") or "").casefold()
        key = next((word for word in recomputed if word in measure), None)
        if key is None:
            continue
        package_value = recomputed[key]
        canonical = entry.get("canonical")
        entry["package"] = package_value
        if canonical == package_value:
            entry["result"] = "PASS"
        else:
            entry["result"] = "FAIL"
            failures.append(
                f"Reconciliation mismatch for {entry.get('measure')}: "
                f"canonical={canonical} package={package_value}"
            )
    return failures


def _verify_tree(root: Path, data: dict[str, Any]) -> tuple[list[str], dict[str, int], int]:
    """Verify every rendered file; failures are collected, never guessed away."""
    failures: list[str] = []
    counts: Counter[str] = Counter()
    total = 0
    client_words = str((data.get("client") or {}).get("name") or "").split()
    kakawa_is_the_client = bool(client_words) and client_words[0].casefold() == "kakawa"

    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        total += 1
        relative = path.relative_to(root).as_posix()
        suffix = path.suffix.casefold()
        counts[suffix or "(none)"] += 1
        if path.stat().st_size == 0:
            failures.append(f"Empty file: {relative}")
            continue

        text_chunks: list[str] = []
        scan_mojibake = False
        if suffix in TEXT_SUFFIXES:
            scan_mojibake = True
            try:
                text_chunks.append(path.read_bytes().decode("utf-8", errors="strict"))
            except UnicodeDecodeError:
                failures.append(f"Not valid UTF-8: {relative}")
        elif suffix in OOXML_MAIN_PARTS:
            scan_mojibake = True
            if not zipfile.is_zipfile(path):
                failures.append(f"Invalid OOXML container: {relative}")
            else:
                with zipfile.ZipFile(path) as archive:
                    members = archive.namelist()
                    if OOXML_MAIN_PARTS[suffix] not in members:
                        failures.append(f"OOXML main part missing: {relative}")
                    text_chunks.extend(
                        archive.read(member).decode("utf-8", errors="replace")
                        for member in members
                        if member.endswith(".xml")
                    )
        elif suffix == ".pdf":
            try:
                reader = PdfReader(path)
                if not reader.pages:
                    failures.append(f"Empty PDF: {relative}")
                text_chunks.extend((page.extract_text() or "") for page in reader.pages)
            except Exception as exc:  # noqa: BLE001 - a broken PDF must fail verification
                failures.append(f"Unreadable PDF: {relative} ({type(exc).__name__})")

        combined = "\n".join(text_chunks)
        if scan_mojibake and any(marker in combined for marker in MOJIBAKE_MARKERS):
            failures.append(f"Mojibake or replacement characters: {relative}")
        if MACHINE_PATH_RE.search(combined):
            failures.append(f"Literal machine path leaked: {relative}")
        if not kakawa_is_the_client and "kakawa" in combined.casefold():
            failures.append(f"Foreign client name 'Kakawa' leaked: {relative}")
    return failures, dict(sorted(counts.items())), total


def _entry_profile(relative: str) -> tuple[str, str]:
    parts = PurePosixPath(relative).parts
    if len(parts) == 1:
        return "summary_markdown", "approved"
    return FOLDER_ARTIFACT_TYPES.get(parts[0][:2], ("package_file", "approved"))


def _build_manifest(
    run: AuditRun, data: dict[str, Any], root: Path, package_name: str
) -> PackageManifest:
    manifest = PackageManifest(
        package_id=package_name,
        project_id=str(run.project_id),
        run_id=str(run.pk),
        rule_version=run.rule_version,
        approved_domains=tuple(run.project.approved_domains),
        evidence_as_of=str(data["run"].get("evidence_as_of") or "") or None,
        reconciliation={
            str(item.get("measure")): item.get("canonical")
            for item in (data.get("qa") or {}).get("reconciliation") or []
        },
        limitations=[str(item) for item in data.get("limitations", [])],
    )
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        relative = PurePosixPath(path.relative_to(root)).as_posix()
        if relative in CONTROL_FILES:
            continue
        artifact_type, approval_state = _entry_profile(relative)
        manifest.add_file(
            root,
            path,
            artifact_type=artifact_type,
            title=path.stem.replace("_", " ").strip() or path.name,
            approval_state=approval_state,
        )
    return manifest


def _persist_qa_results(
    run: AuditRun,
    artifact: Artifact,
    data: dict[str, Any],
    *,
    format_counts: dict[str, int],
    file_total: int,
    entry_count: int,
) -> None:
    reconciliation = {
        str(item.get("measure")): item.get("result")
        for item in (data.get("qa") or {}).get("reconciliation") or []
    }
    checks = (
        (
            "package.render_verification",
            "Every rendered file passed non-empty, encoding, format, and content-safety checks.",
            {"files": file_total, "formats": format_counts},
        ),
        (
            "package.reconciliation",
            "Package-side counts reconcile with canonical run data.",
            {"measures": reconciliation},
        ),
        (
            "package.zip_integrity",
            "Manifest coverage, checksums, and ZIP member safety were verified.",
            {"manifest_entries": entry_count, "package_sha256": artifact.sha256},
        ),
    )
    for check_code, message, details in checks:
        QAResult.objects.update_or_create(
            run=run,
            artifact=artifact,
            check_code=check_code,
            check_version=PACKAGE_CHECK_VERSION,
            defaults={
                "severity": Severity.INFO,
                "status": QAResult.Status.PASS,
                "message": message,
                "details": details,
            },
        )


def build_package_for_run(
    run: AuditRun,
    *,
    actor: User | None = None,
    progress: ProgressHook = None,
) -> tuple[Artifact, PackageManifestModel]:
    """Render, verify, and persist the full deliverable package for one run."""
    from exporters.run_data import compile_run_data

    _notify(progress, "Compiling canonical run data")
    data = compile_run_data(run)
    package_name = _package_name(data)

    with tempfile.TemporaryDirectory(prefix="seo-package-") as tmp:
        tmp_dir = Path(tmp)
        root = tmp_dir / package_name
        root.mkdir(parents=True, exist_ok=True)

        _notify(progress, "Rendering the package summary")
        _write_markdown(data, root)

        _notify(progress, "Rendering audit workbooks")
        from exporters.xlsx_workbooks import render_workbooks

        render_workbooks(data, root)

        _notify(progress, "Rendering strategy, action-plan, and content documents")
        base_dir = Path(settings.BASE_DIR)
        pdf = PDFReportBuilder(base_dir)
        docx = DOCXReportBuilder(base_dir)
        pdf.executive_report(data, root / "01_Audit_Reports" / "Enterprise_SEO_Audit_Report.pdf")
        docx.strategy_report(data, root / "02_Strategy_Documents" / "SEO_Strategy.docx")
        pdf.strategy_report(data, root / "02_Strategy_Documents" / "SEO_Strategy.pdf")
        pdf.action_plan(data, root / "03_Action_Plan" / "16_Week_Action_Plan.pdf")
        for asset in data.get("content_assets", []):
            docx.content_asset(data, asset, root / "05_Content" / _content_docx_name(asset))

        _notify(progress, "Rendering the executive deck")
        from exporters.pptx_deck import render_deck

        render_deck(data, root / "07_Executive_Deck" / "Executive_Deck.pptx")
        pdf.deck_pdf(data, root / "07_Executive_Deck" / "Executive_Deck.pdf")

        _notify(progress, "Writing data files and schema templates")
        _write_csvs(data, root)
        _write_robots_notes(data, root)
        brand_facts = run.project.brand_facts if isinstance(run.project.brand_facts, dict) else {}
        _write_schema_files(data, root, brand_facts)

        _notify(progress, "Verifying the package")
        failures = _reconcile_package_counts(data, root)
        write_qa_json(data, root / "06_QA_and_Manifest" / "QA_Report.json")
        pdf.qa_report(data, root / "06_QA_and_Manifest" / "QA_Report.pdf")
        tree_failures, format_counts, file_total = _verify_tree(root, data)
        failures.extend(tree_failures)
        if failures:
            raise PackageBuildError(failures)

        _notify(progress, "Publishing the package")
        manifest = _build_manifest(run, data, root, package_name)
        try:
            manifest_path = manifest.write(root)
        except ValueError as exc:
            raise PackageBuildError([str(exc)]) from exc
        manifest.write_checksums(root, manifest_path)
        zip_path, _checksum_path = build_zip(root, tmp_dir / f"{package_name}.zip")
        verify_zip_members(zip_path, package_name)
        manifest_sha256 = PackageManifest.sha256(manifest_path)
        zip_bytes = zip_path.read_bytes()
        manifest_summary = {
            "schema_version": "1.0",
            "package_id": package_name,
            "project_id": str(run.project_id),
            "run_id": str(run.pk),
            "rule_version": run.rule_version,
            "generated_at": manifest.generated_at,
            "evidence_as_of": manifest.evidence_as_of,
            "reconciliation": manifest.reconciliation,
            "limitations": manifest.limitations,
            "files": [
                {
                    "path": entry.path,
                    "sha256": entry.sha256,
                    "bytes": entry.bytes,
                    "artifact_type": entry.artifact_type,
                    "approval_state": entry.approval_state,
                }
                for entry in sorted(manifest.entries, key=lambda item: item.path)
            ],
        }
        entry_count = len(manifest.entries)

    artifact, _created = save_artifact_bytes(
        run=run,
        payload=zip_bytes,
        filename=f"{package_name}.zip",
        title=f"{data['client']['name']} SEO audit package",
        artifact_type="package",
        media_type="application/zip",
        created_by=actor,
        metadata={
            "package_name": package_name,
            "file_count": entry_count,
            "run_version": run.version,
            "manifest_sha256": manifest_sha256,
        },
    )
    manifest_row, _row_created = PackageManifestModel.objects.get_or_create(
        run=run,
        version=run.version,
        defaults={
            "manifest": manifest_summary,
            "manifest_sha256": manifest_sha256,
            "package_sha256": artifact.sha256,
            "package_artifact": artifact,
            "status": ReviewStatus.IN_REVIEW,
            "generated_by": actor,
        },
    )
    _persist_qa_results(
        run,
        artifact,
        data,
        format_counts=format_counts,
        file_total=file_total,
        entry_count=entry_count,
    )
    record_event(
        event_type="package.generated",
        actor=actor,
        run=run,
        object_instance=artifact,
        payload={
            "artifact_id": str(artifact.pk),
            "package_name": package_name,
            "file_count": entry_count,
            "sha256": artifact.sha256,
        },
    )
    return artifact, manifest_row
