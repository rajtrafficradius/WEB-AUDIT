"""Build and finalize the Kakawa v19 professional acceptance package."""

from __future__ import annotations

import csv
import json
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .docx_reports import DOCXReportBuilder
from .html_outputs import (
    build_content_html,
    build_content_markdown,
    build_html_deck,
    content_filename,
)
from .manifest import CONTROL_FILES, PackageManifest, build_zip, verify_zip_members
from .pdf_reports import PDFReportBuilder, write_qa_json

PACKAGE_NAME = "Kakawa_Chocolates_Enterprise_SEO_Package_v19"
PACKAGE_DIRECTORIES = (
    "00_Executive",
    "01_Evidence_and_Audits",
    "02_Strategy",
    "03_Action_Plan",
    "04_Deployment_Assets",
    "05_Content",
    "06_QA_and_Manifest",
)


def _json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _csv_value(value: Any) -> str | int | float:
    if value is None:
        return "Unavailable"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return value
    if isinstance(value, list | tuple | set):
        value = ", ".join(str(item) for item in value)
    text = str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        text = "'" + text
    return text


def _csv(path: Path, headers: list[str], rows: Iterable[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: _csv_value(row.get(header)) for header in headers})
    return path


def _safe_reset(package_root: Path, exports_root: Path) -> None:
    resolved_exports = exports_root.resolve()
    resolved_package = package_root.resolve()
    if not resolved_package.is_relative_to(resolved_exports):
        raise ValueError("Package reset escaped the exports directory")
    if resolved_package.name != PACKAGE_NAME:
        raise ValueError("Refusing to reset an unexpected package name")
    if resolved_package.exists():
        shutil.rmtree(resolved_package)
    for directory in PACKAGE_DIRECTORIES:
        (resolved_package / directory).mkdir(parents=True, exist_ok=True)


def _write_evidence(data: dict[str, Any], root: Path) -> None:
    folder = root / "01_Evidence_and_Audits"
    _json(folder / "canonical_evidence_snapshot.json", data)
    _csv(
        folder / "source_coverage.csv",
        ["id", "label", "kind", "status", "captured_at", "scope", "coverage", "unavailable_reason"],
        data["sources"],
    )
    _csv(
        folder / "issue_register.csv",
        [
            "id",
            "priority",
            "priority_score",
            "category",
            "rule_id",
            "rule_version",
            "severity",
            "title",
            "description",
            "impact",
            "confidence",
            "reach",
            "effort",
            "implementation_risk",
            "approval_class",
            "as_of_date",
            "evidence_ids",
            "affected_urls",
        ],
        data["findings"],
    )
    _csv(
        folder / "evidence_index.csv",
        [
            "id",
            "source_id",
            "evidence_type",
            "observed_value",
            "original_url",
            "normalized_url",
            "captured_at",
            "locale",
            "scope",
            "confidence",
            "unavailable_reason",
        ],
        data["evidence"],
    )


def _write_strategy(data: dict[str, Any], root: Path, docx: DOCXReportBuilder, pdf: PDFReportBuilder) -> None:
    folder = root / "02_Strategy"
    docx.strategy_report(data, folder / "Kakawa_Enterprise_SEO_Strategy_v19.docx")
    pdf.strategy_report(data, folder / "Kakawa_Enterprise_SEO_Strategy_v19.pdf")
    keyword_rows = [
        {
            "id": item["id"],
            "topic": item["cluster"],
            "intent": item["intent"],
            "target_url": item["target_url"],
            "monthly_volume": item["keyword_volume"],
            "current_ranking": item["ranking"],
            "availability_note": item["unavailable_reason"],
            "evidence_ids": item["evidence_ids"],
        }
        for item in data["opportunities"]
    ]
    _csv(
        folder / "keyword_universe.csv",
        ["id", "topic", "intent", "target_url", "monthly_volume", "current_ranking", "availability_note", "evidence_ids"],
        keyword_rows,
    )
    _json(
        folder / "competitor_intelligence.json",
        {
            "status": "unavailable",
            "as_of_date": data["run"]["evidence_as_of"],
            "reason": "SEMrush credentials and an approved competitor set were not available.",
            "competitors": [],
            "deep_dives": [],
            "fabricated_substitutes": False,
        },
    )
    _csv(
        folder / "topical_map.csv",
        ["cluster", "intent", "target_url", "decision", "evidence_ids"],
        data["opportunities"],
    )
    _csv(
        folder / "url_architecture.csv",
        ["id", "normalized_url", "status_code", "title", "h1", "canonical_url", "indexability", "evidence_id"],
        data["pages"],
    )
    _csv(
        folder / "cannibalization_decisions.csv",
        ["id", "cluster", "intent", "target_url", "decision", "evidence_ids"],
        data["opportunities"],
    )
    roadmap = [
        {
            "content_id": item["id"],
            "title": item["title"],
            "target_url": item["target_url"],
            "intent": item["intent"],
            "approval_state": item["approval_state"],
            "generation_method": item["generation_method"],
            "evidence_ids": item["evidence_ids"],
        }
        for item in data["content_assets"]
    ]
    _csv(
        folder / "content_roadmap.csv",
        ["content_id", "title", "target_url", "intent", "approval_state", "generation_method", "evidence_ids"],
        roadmap,
    )


def _write_action_plan(data: dict[str, Any], root: Path, pdf: PDFReportBuilder) -> None:
    folder = root / "03_Action_Plan"
    _csv(
        folder / "Kakawa_16_Week_Action_Plan_v19.csv",
        [
            "id",
            "phase",
            "week",
            "week_end",
            "priority",
            "action",
            "owner",
            "dependencies",
            "effort",
            "kpi",
            "approval_class",
            "status",
            "evidence_ids",
            "confidence",
            "implementation_risk",
            "notes",
        ],
        data["actions"],
    )
    pdf.action_plan(data, folder / "Kakawa_16_Week_Action_Plan_v19.pdf")


def _write_deployment(data: dict[str, Any], root: Path) -> None:
    folder = root / "04_Deployment_Assets"
    deployment = data["deployment"]
    _csv(
        folder / "redirect_candidates.csv",
        ["source_url", "target_url", "status_code", "evidence_id", "approval_status", "included_in_deployment"],
        deployment["redirect_candidates"],
    )
    _csv(
        folder / "canonical_recommendations.csv",
        ["page_id", "source_url", "observed_canonical", "proposed_canonical", "evidence_id", "approval_status", "included_in_deployment"],
        deployment["canonical_candidates"],
    )
    _csv(
        folder / "metadata_and_heading_review.csv",
        ["finding_id", "issue", "affected_url", "proposed_value", "unavailable_reason", "approval_status"],
        deployment["metadata_review"],
    )
    _csv(
        folder / "internal_link_recommendations.csv",
        ["source_url", "target_url", "anchor", "rationale", "evidence_ids", "approval_status"],
        deployment["internal_link_candidates"],
    )
    _json(folder / "page_specific_jsonld_recommendations.json", deployment["schema"])
    robots = deployment["robots"]
    (folder / "robots_recommendations.txt").write_text(
        "TRAFFIC RADIUS · ROBOTS REVIEW\n"
        f"Evidence as of: {data['run']['evidence_as_of']}\n"
        f"Deployable changes: {len(robots['deployable_changes'])}\n"
        f"Recommendation: {robots['recommendation']}\n"
        "No client website or robots file was changed.\n",
        encoding="utf-8",
        newline="\n",
    )
    (folder / "platform_implementation_notes.md").write_text(
        "# Platform implementation notes\n\n"
        f"Evidence as of: {data['run']['evidence_as_of']}\n\n"
        "The public fixture does not assert a CMS or deployment stack. Confirm the live platform, theme/version, deployment workflow, rollback path, cache behavior, and structured-data ownership before implementation. Apply changes in an isolated staging environment, run the canonical QA suite, then record the Gate 2 decision.\n\n"
        "No live CMS change was made by Traffic Radius Enterprise SEO Studio.\n",
        encoding="utf-8",
        newline="\n",
    )
    approval_rows: list[dict[str, Any]] = []
    for candidate in deployment["canonical_candidates"]:
        approval_rows.append(
            {
                "asset_type": "canonical",
                "asset_reference": candidate["page_id"],
                "approval_class": "agency_admin",
                "approval_status": candidate["approval_status"],
                "included_in_deployment": candidate["included_in_deployment"],
                "evidence_id": candidate["evidence_id"],
                "decision_note": "Withheld until graph validation and administrator approval",
            }
        )
    approval_rows.extend(
        [
            {
                "asset_type": "schema",
                "asset_reference": "page-specific JSON-LD",
                "approval_class": "agency_admin",
                "approval_status": "withheld_pending_agency_admin",
                "included_in_deployment": False,
                "evidence_id": "Unavailable",
                "decision_note": deployment["schema"]["withheld"][0]["reason"],
            },
            {
                "asset_type": "disavow",
                "asset_reference": "disavow.txt",
                "approval_class": "agency_admin",
                "approval_status": "disabled",
                "included_in_deployment": False,
                "evidence_id": "Unavailable",
                "decision_note": deployment["disavow"]["reason"],
            },
        ]
    )
    _csv(
        folder / "approval_ledger.csv",
        ["asset_type", "asset_reference", "approval_class", "approval_status", "included_in_deployment", "evidence_id", "decision_note"],
        approval_rows,
    )


def _write_content(data: dict[str, Any], root: Path, docx: DOCXReportBuilder) -> None:
    folder = root / "05_Content"
    for asset in data["content_assets"]:
        stem = content_filename(asset)
        docx.content_asset(data, asset, folder / f"{stem}.docx")
        build_content_html(data, asset, folder / f"{stem}.html")
        build_content_markdown(data, asset, folder / f"{stem}.md")


def _write_qa(data: dict[str, Any], root: Path, pdf: PDFReportBuilder) -> None:
    folder = root / "06_QA_and_Manifest"
    pdf.qa_report(data, folder / "Kakawa_QA_v19.pdf")
    write_qa_json(data, folder / "Kakawa_QA_v19.json")
    availability_rows = []
    for source in data["sources"]:
        available = source["status"] == "available"
        availability_rows.append(
            {
                **source,
                "qa_classification": "AVAILABLE" if available else "UNAVAILABLE_TRUTHFUL",
                "publication_effect": (
                    "Included in covered calculations and evidence-led claims"
                    if available
                    else "Excluded; related scores, claims and forecasts remain withheld"
                ),
            }
        )
    _csv(
        folder / "availability_matrix.csv",
        [
            "id", "label", "kind", "status", "captured_at", "scope", "coverage",
            "unavailable_reason", "qa_classification", "publication_effect",
        ],
        availability_rows,
    )
    _csv(
        folder / "generation_ledger.csv",
        ["id", "task", "configured_model", "returned_model", "prompt_version", "status", "request_hash", "response_hash", "tokens", "cost", "unavailable_reason"],
        data["generation_ledger"],
    )
    _csv(
        folder / "change_log.csv",
        ["version", "change", "control", "evidence_as_of"],
        [
            {"version": "v19", "change": item["failure_mode"], "control": item["v19_control"], "evidence_as_of": data["run"]["evidence_as_of"]}
            for item in data["comparison"]
        ],
    )
    _json(
        folder / "availability_and_release_notes.json",
        {
            "release_status": data["qa"]["release_status"],
            "state": data["run"]["state"],
            "evidence_as_of": data["run"]["evidence_as_of"],
            "limitations": data["limitations"],
            "production_promoted": False,
            "reason": "Gate 1, Gate 2, private-source baselines and deployment authorization remain outstanding.",
        },
    )


def prepare_package(data: dict[str, Any], project_root: Path) -> Path:
    exports_root = project_root / "exports"
    package_root = exports_root / PACKAGE_NAME
    _safe_reset(package_root, exports_root)
    pdf = PDFReportBuilder(project_root)
    docx = DOCXReportBuilder(project_root)
    pdf.executive_report(data, package_root / "00_Executive" / "Kakawa_Executive_SEO_Report_v19.pdf")
    build_html_deck(data, package_root / "00_Executive" / "Kakawa_Executive_Deck_v19.html")
    pdf.deck_pdf(data, package_root / "00_Executive" / "Kakawa_Executive_Deck_v19.pdf")
    _write_evidence(data, package_root)
    _write_strategy(data, package_root, docx, pdf)
    _write_action_plan(data, package_root, pdf)
    _write_deployment(data, package_root)
    _write_content(data, package_root, docx)
    _write_qa(data, package_root, pdf)
    pdf.comparison_report(data, exports_root / "Kakawa_v18_vs_v19_Quality_Comparison.pdf")
    return package_root


def _derivatives(root: Path) -> dict[str, str]:
    mapping = {
        "00_Executive/Kakawa_Executive_Deck_v19.pdf": "00_Executive/Kakawa_Executive_Deck_v19.pptx",
        "02_Strategy/Kakawa_Enterprise_SEO_Strategy_v19.pdf": "02_Strategy/Kakawa_Enterprise_SEO_Strategy_v19.docx",
        "03_Action_Plan/Kakawa_16_Week_Action_Plan_v19.csv": "03_Action_Plan/Kakawa_16_Week_Action_Plan_v19.xlsx",
        "03_Action_Plan/Kakawa_16_Week_Action_Plan_v19.pdf": "03_Action_Plan/Kakawa_16_Week_Action_Plan_v19.xlsx",
        "06_QA_and_Manifest/Kakawa_QA_v19.pdf": "06_QA_and_Manifest/Kakawa_QA_v19.json",
        "06_QA_and_Manifest/Kakawa_QA_v19.xlsx": "06_QA_and_Manifest/Kakawa_QA_v19.json",
    }
    content = root / "05_Content"
    for docx in content.glob("*.docx"):
        base = f"05_Content/{docx.stem}"
        mapping[f"{base}.html"] = f"{base}.docx"
        mapping[f"{base}.md"] = f"{base}.docx"
    return mapping


def _artifact_type(relative: str) -> str:
    directory = relative.split("/", 1)[0]
    return {
        "00_Executive": "executive",
        "01_Evidence_and_Audits": "evidence_audit",
        "02_Strategy": "strategy",
        "03_Action_Plan": "action_plan",
        "04_Deployment_Assets": "deployment_proposal",
        "05_Content": "content_review",
        "06_QA_and_Manifest": "quality_assurance",
    }[directory]


def finalize_package(data: dict[str, Any], project_root: Path) -> tuple[Path, Path, Path]:
    exports_root = project_root / "exports"
    package_root = exports_root / PACKAGE_NAME
    expected = [
        package_root / "00_Executive" / "Kakawa_Executive_Deck_v19.pptx",
        package_root / "01_Evidence_and_Audits" / "Kakawa_Enterprise_SEO_Audit_v19.xlsx",
        package_root / "03_Action_Plan" / "Kakawa_16_Week_Action_Plan_v19.xlsx",
        package_root / "06_QA_and_Manifest" / "Kakawa_QA_v19.xlsx",
        package_root / "06_QA_and_Manifest" / "render-verification.json",
    ]
    missing = [str(path) for path in expected if not path.is_file()]
    if missing:
        raise ValueError(f"Renderer phase is incomplete: {missing}")
    source_ids = tuple(source["id"] for source in data["sources"])
    manifest = PackageManifest(
        package_id=PACKAGE_NAME,
        project_id=data["project"]["id"],
        run_id=data["run"]["id"],
        rule_version=data["run"]["rule_version"],
        approved_domains=(data["client"]["domain"],),
        evidence_as_of=data["run"]["evidence_as_of"],
        reconciliation={item["measure"]: item["canonical"] for item in data["qa"]["reconciliation"]},
        limitations=list(data["limitations"]),
    )
    derivative_map = _derivatives(package_root)
    for path in sorted(package_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(package_root).as_posix()
        if relative in CONTROL_FILES:
            continue
        artifact_type = _artifact_type(relative)
        approval = "review_ready"
        if relative.startswith("04_Deployment_Assets/"):
            approval = "withheld_pending_approval"
        elif relative.startswith("05_Content/"):
            approval = "withheld_pending_human_approval"
        manifest.add_file(
            package_root,
            path,
            artifact_type=artifact_type,
            title=path.stem.replace("_", " "),
            source_records=source_ids,
            derivative_of=derivative_map.get(relative),
            approval_state=approval,
        )
    manifest_path = manifest.write(package_root)
    checksum_path = manifest.write_checksums(package_root, manifest_path)
    zip_path = exports_root / f"{PACKAGE_NAME}.zip"
    zip_path, zip_checksum = build_zip(package_root, zip_path)
    verify_zip_members(zip_path, PACKAGE_NAME)
    return zip_path, zip_checksum, checksum_path

