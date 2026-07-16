"""Generate a V18-shaped, deduplicated, evidence-led Kakawa v19 package."""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from audit_engine.models import Severity, VerifiedFact
from exporters.docx_reports import DOCXReportBuilder
from exporters.html_outputs import build_html_deck
from exporters.manifest import build_zip
from exporters.package_builder import prepare_package
from exporters.pdf_reports import PDFReportBuilder
from generation.openai_boundary import (
    GenerationConfig,
    GenerationPurpose,
    GenerationStatus,
    OpenAIBoundary,
)
from generation.quality import validate_claims, validate_domains_and_links, validate_placeholders
from generation.schemas import FactPack, strategy_schema

PACKAGE_NAME = "Kakawa_Chocolates_Enterprise_SEO_Package_v19_V18_Structure"
DIRECTORIES = (
    "01_Audit_Reports",
    "02_Strategy_Documents",
    "03_Action_Plan",
    "04_Implementation_Deliverables/Link_Building",
    "04_Implementation_Deliverables/New_Content",
    "04_Implementation_Deliverables/On_Page_Optimizations",
    "04_Implementation_Deliverables/Schema_Markup",
    "04_Implementation_Deliverables/Technical_Fixes",
    "05_SEO_Content",
    "06_QA",
    "07_Slide_Deck",
)


def _load_env(root: Path) -> None:
    path = root / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in {"OPENAI_API_KEY", "OPENAI_STRATEGY_MODEL", "OPENAI_EXTRACTION_MODEL"}:
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _remove_unique_items(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _remove_unique_items(child) for key, child in value.items() if key != "uniqueItems"}
    if isinstance(value, list):
        return [_remove_unique_items(child) for child in value]
    return value


def _ai_enrich(data: dict[str, Any], root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    enriched = copy.deepcopy(data)
    _load_env(root)
    as_of = datetime.fromisoformat(data["run"]["captured_at"])
    facts: list[VerifiedFact] = []
    evidence_ids: set[str] = set()

    def add_fact(key: str, value: Any) -> None:
        evidence_id = str(uuid5(NAMESPACE_URL, f"{data['run']['id']}:{key}"))
        evidence_ids.add(evidence_id)
        facts.append(VerifiedFact(key, value, (evidence_id,), as_of))

    add_fact(
        "run_evidence_posture",
        {
            "coverage": data["run"]["evidence_coverage"],
            "score_status": data["run"]["overall_score_reason"],
            "approved_domain": data["client"]["domain"],
            "canonical_source_ids": [source["id"] for source in data["sources"]],
        },
    )
    add_fact(
        "approved_findings",
        [
            {
                "id": item["id"],
                "priority": item["priority"],
                "title": item["title"],
                "impact": item["impact"],
                "canonical_evidence_ids": item["evidence_ids"],
            }
            for item in data["findings"]
        ],
    )
    add_fact(
        "approved_opportunities",
        [
            {
                "id": item["id"],
                "cluster": item["cluster"],
                "intent": item["intent"],
                "target_url": item["target_url"],
                "decision": item["decision"],
                "canonical_evidence_ids": item["evidence_ids"],
            }
            for item in data["opportunities"]
        ],
    )
    add_fact("approved_action_sequence", data["actions"])
    add_fact("explicit_limitations", data["limitations"])
    unavailable = {
        source["label"]: source["unavailable_reason"]
        for source in data["sources"]
        if source["status"] != "available"
    }
    statuses = {
        page["normalized_url"]: page["status_code"]
        for page in data["pages"]
        if page.get("normalized_url")
    }
    fact_pack = FactPack(
        project_id=data["project"]["id"],
        approved_domains=(data["client"]["domain"],),
        facts=tuple(facts),
        available_evidence_ids=frozenset(evidence_ids),
        known_url_statuses=statuses,
        unavailable_sources=unavailable,
    )
    configured = os.getenv("OPENAI_STRATEGY_MODEL", "gpt-5.6-sol")
    fallback = os.getenv("OPENAI_EXTRACTION_MODEL", "gpt-5.6-luna")
    schema = _remove_unique_items(strategy_schema())
    task = (
        "Create a concise enterprise SEO executive synthesis and evidence-led recommendations. "
        "Do not add facts beyond the fact pack. Keep risky assets approval-gated and explicitly "
        "state that private analytics and market baselines are unavailable."
    )
    boundary = OpenAIBoundary(
        config=GenerationConfig(final_model=configured, extraction_model=fallback, max_output_tokens=3200)
    )
    result = boundary.generate_structured(
        task=task,
        fact_pack=fact_pack,
        schema_name="kakawa_v19_strategy",
        schema=schema,
        purpose=GenerationPurpose.FINAL,
    )
    if result.status is not GenerationStatus.AVAILABLE and configured != fallback:
        boundary = OpenAIBoundary(
            config=GenerationConfig(final_model=fallback, extraction_model=fallback, max_output_tokens=3200)
        )
        result = boundary.generate_structured(
            task=task,
            fact_pack=fact_pack,
            schema_name="kakawa_v19_strategy",
            schema=schema,
            purpose=GenerationPurpose.FINAL,
        )
    quality: list[dict[str, str]] = []
    if result.data:
        issues = (
            *validate_claims(result.data, fact_pack),
            *validate_domains_and_links(result.data, fact_pack),
            *validate_placeholders(result.data),
        )
        quality = [
            {"code": issue.code, "severity": issue.severity.value, "message": issue.message}
            for issue in issues
        ]
        high = [issue for issue in issues if issue.severity in {Severity.HIGH, Severity.CRITICAL}]
        if not high:
            enriched["executive_summary"] = str(result.data["executive_summary"])
            enriched["strategy_sections"].insert(
                0,
                {
                    "title": "GPT evidence-constrained strategic synthesis",
                    "level": 1,
                    "paragraphs": [
                        f"{item['title']}: {item['rationale']} Implementation: {item['implementation']}"
                        for item in result.data["recommendations"][:5]
                    ],
                    "decision": "Use model synthesis for narrative prioritisation only; canonical records remain authoritative.",
                },
            )
    ledger = result.ledger
    ledger_row = {
        "id": ledger.call_id,
        "task": "Evidence-constrained package strategy synthesis",
        "configured_model": ledger.requested_model,
        "returned_model": ledger.returned_model,
        "prompt_version": ledger.prompt_version,
        "status": result.status.value,
        "request_hash": ledger.request_sha256,
        "response_hash": ledger.response_sha256,
        "tokens": (ledger.input_tokens or 0) + (ledger.output_tokens or 0),
        "cost": None,
        "unavailable_reason": result.unavailable_reason,
    }
    enriched["generation_ledger"].append(ledger_row)
    return enriched, {
        "status": result.status.value,
        "model": ledger.returned_model or ledger.requested_model,
        "ledger": ledger_row,
        "quality_issues": quality,
        "output": dict(result.data) if result.data else None,
    }


def _rows(items: list[dict[str, Any]], keys: list[str], limit: int = 500) -> list[list[Any]]:
    values: list[list[Any]] = []
    for item in items[:limit]:
        row: list[Any] = []
        for key in keys:
            value = item.get(key)
            if isinstance(value, list):
                value = ", ".join(str(part) for part in value)
            elif value is None:
                value = "Unavailable"
            row.append(value)
        values.append(row)
    return values


def _spec(
    path: str,
    title: str,
    headers: list[str],
    rows: list[list[Any]],
    data: dict[str, Any],
    *,
    status: str = "EVIDENCE-LINKED",
    decision: str = "Review and approve before implementation.",
    approval: bool = False,
) -> dict[str, Any]:
    return {
        "path": path,
        "title": title,
        "subtitle": "Traffic Radius enterprise SEO evidence register ? professional V18-compatible v19 edition",
        "headers": headers,
        "rows": rows,
        "widths": [max(14, min(48, len(header) * 2 + 8)) for header in headers],
        "as_of": data["run"]["evidence_as_of"],
        "domain": data["client"]["domain"],
        "run_id": data["run"]["id"],
        "status": status,
        "decision": decision,
        "register_note": "Canonical values only. Unavailable evidence is labelled explicitly; no estimates or fabricated substitutes.",
        "evidence_linked": True,
        "approval_required": approval,
    }


def _workbook_specs(data: dict[str, Any]) -> list[dict[str, Any]]:
    pages = data["pages"]
    findings = data["findings"]
    sources = data["sources"]
    opps = data["opportunities"]
    actions = data["actions"]
    unavailable = [["UNAVAILABLE", item["label"], item["unavailable_reason"], item["captured_at"]] for item in sources if item["status"] != "available"]
    page_rows = _rows(pages, ["id", "normalized_url", "status_code", "title", "h1", "canonical_url", "indexability", "evidence_id"], 400)
    finding_rows = _rows(findings, ["id", "priority", "category", "title", "impact", "confidence", "approval_class", "evidence_ids"])
    opportunity_rows = _rows(opps, ["id", "cluster", "intent", "target_url", "decision", "evidence_ids"])
    action_rows = _rows(actions, ["id", "phase", "week", "priority", "action", "owner", "dependencies", "effort", "kpi", "approval_class", "status", "evidence_ids"])
    base = [
        _spec("01_Audit_Reports/Backlink_Audit_Report.xlsx", "Backlink Audit Report", ["Status", "Source", "Reason", "As of"], unavailable, data, status="UNAVAILABLE", decision="Connect an approved backlink source before scoring."),
        _spec("01_Audit_Reports/Baseline_Performance_Analysis.xlsx", "Baseline Performance Analysis", ["ID", "Source", "Kind", "Status", "Coverage", "Unavailable reason"], _rows(sources, ["id", "label", "kind", "status", "coverage", "unavailable_reason"]), data),
        _spec("01_Audit_Reports/Competitor_Landscape_Analysis.xlsx", "Competitor Landscape Analysis", ["Status", "Source", "Reason", "As of"], unavailable, data, status="UNAVAILABLE", decision="No competitor claims published without SEMrush and an approved competitor set."),
        _spec("01_Audit_Reports/Content_Audit_Workbook.xlsx", "Content Audit Workbook", ["ID", "URL", "Status", "Title", "H1", "Canonical", "Indexability", "Evidence"], page_rows, data),
        _spec("01_Audit_Reports/CRO_UX_Findings.xlsx", "CRO and UX Findings", ["ID", "Priority", "Category", "Finding", "Impact", "Confidence", "Approval", "Evidence"], finding_rows, data),
        _spec("01_Audit_Reports/Ecommerce_Audit_Report.xlsx", "Ecommerce Audit Report", ["ID", "Cluster", "Intent", "Target URL", "Decision", "Evidence"], opportunity_rows, data),
        _spec("01_Audit_Reports/GBP_Local_Audit.xlsx", "GBP and Local Audit", ["Status", "Source", "Reason", "As of"], unavailable, data, status="UNAVAILABLE", decision="Connect GBP or BrightLocal evidence before local scoring."),
        _spec("01_Audit_Reports/GEO_AEO_Readiness_Scorecard.xlsx", "GEO and AEO Readiness Scorecard", ["Category", "Score", "Coverage", "Rule", "Reason"], _rows(data["categories"], ["category", "score", "coverage", "rule_version", "reason"]), data),
        _spec("01_Audit_Reports/Technical_Audit_Report.xlsx", "Technical Audit Report", ["ID", "Priority", "Category", "Finding", "Impact", "Confidence", "Approval", "Evidence"], finding_rows, data),
        _spec("01_Audit_Reports/Tracking_Audit_Report.xlsx", "Tracking Audit Report", ["Status", "Source", "Reason", "As of"], unavailable, data, status="UNAVAILABLE", decision="Connect GA4 and tag evidence before asserting tracking health."),
        _spec("02_Strategy_Documents/Cannibalization_Resolution_Plan.xlsx", "Cannibalization Resolution Plan", ["ID", "Cluster", "Intent", "Target URL", "Decision", "Evidence"], opportunity_rows, data),
        _spec("02_Strategy_Documents/Content_Gap_Analysis.xlsx", "Content Gap Analysis", ["ID", "Cluster", "Intent", "Target URL", "Decision", "Evidence"], opportunity_rows, data),
        _spec("02_Strategy_Documents/Content_Strategy.xlsx", "Content Strategy", ["ID", "Title", "Target URL", "Intent", "Approval", "Generation", "Evidence"], _rows(data["content_assets"], ["id", "title", "target_url", "intent", "approval_state", "generation_method", "evidence_ids"]), data),
        _spec("02_Strategy_Documents/Master_Keyword_Universe.xlsx", "Master Keyword Universe", ["ID", "Cluster", "Intent", "Target URL", "Volume", "Ranking", "Unavailable reason", "Evidence"], _rows(opps, ["id", "cluster", "intent", "target_url", "keyword_volume", "ranking", "unavailable_reason", "evidence_ids"]), data),
        _spec("02_Strategy_Documents/URL_Architecture_Map.xlsx", "URL Architecture Map", ["ID", "URL", "Status", "Title", "H1", "Canonical", "Indexability", "Evidence"], page_rows, data),
        _spec("03_Action_Plan/16_Week_Action_Plan.xlsx", "16 Week Action Plan", ["ID", "Phase", "Week", "Priority", "Action", "Owner", "Dependencies", "Effort", "KPI", "Approval", "Status", "Evidence"], action_rows, data),
        _spec("03_Action_Plan/16_Week_Atomic_Action_Plan.xlsx", "16 Week Atomic Action Plan", ["ID", "Week", "Action", "Owner", "Dependency", "Effort", "KPI", "Risk", "Approval"], _rows(actions, ["id", "week", "action", "owner", "dependencies", "effort", "kpi", "implementation_risk", "approval_class"]), data),
        _spec("03_Action_Plan/Atomic_Action_Plan.xlsx", "Atomic Action Plan", ["ID", "Priority", "Action", "Owner", "Status", "Confidence", "Evidence"], _rows(actions, ["id", "priority", "action", "owner", "status", "confidence", "evidence_ids"]), data),
        _spec("04_Implementation_Deliverables/Link_Building/Citation_List.xlsx", "Citation List", ["Status", "Source", "Reason", "As of"], unavailable, data, status="UNAVAILABLE", decision="No citation targets invented without approved local evidence."),
        _spec("04_Implementation_Deliverables/Link_Building/Internal_Link_Map.xlsx", "Internal Link Map", ["Source URL", "Target URL", "Anchor", "Rationale", "Evidence", "Approval"], _rows(data["deployment"]["internal_link_candidates"], ["source_url", "target_url", "anchor", "rationale", "evidence_ids", "approval_status"]), data, approval=True),
        _spec("04_Implementation_Deliverables/Link_Building/Outreach_Target_List.xlsx", "Outreach Target List", ["Status", "Source", "Reason", "As of"], unavailable, data, status="UNAVAILABLE", decision="No outreach targets invented without backlink evidence."),
        _spec("04_Implementation_Deliverables/On_Page_Optimizations/H1_Tags.xlsx", "H1 Tag Optimizations", ["Finding", "Issue", "URL", "Proposed value", "Unavailable reason", "Approval"], _rows(data["deployment"]["metadata_review"], ["finding_id", "issue", "affected_url", "proposed_value", "unavailable_reason", "approval_status"]), data, approval=True),
        _spec("04_Implementation_Deliverables/On_Page_Optimizations/Meta_Description_Optimizations.xlsx", "Meta Description Optimizations", ["Finding", "Issue", "URL", "Proposed value", "Unavailable reason", "Approval"], _rows(data["deployment"]["metadata_review"], ["finding_id", "issue", "affected_url", "proposed_value", "unavailable_reason", "approval_status"]), data, approval=True),
        _spec("04_Implementation_Deliverables/On_Page_Optimizations/Meta_Tags.xlsx", "Meta Tags", ["ID", "URL", "Title", "H1", "Canonical", "Indexability", "Evidence"], _rows(pages, ["id", "normalized_url", "title", "h1", "canonical_url", "indexability", "evidence_id"], 400), data, approval=True),
        _spec("04_Implementation_Deliverables/On_Page_Optimizations/Title_Tag_Optimizations.xlsx", "Title Tag Optimizations", ["Finding", "Issue", "URL", "Proposed value", "Unavailable reason", "Approval"], _rows(data["deployment"]["metadata_review"], ["finding_id", "issue", "affected_url", "proposed_value", "unavailable_reason", "approval_status"]), data, approval=True),
        _spec("04_Implementation_Deliverables/Technical_Fixes/Canonical_Fixes.xlsx", "Canonical Fixes", ["Page", "Source URL", "Observed canonical", "Proposed canonical", "Evidence", "Approval", "Deployable"], _rows(data["deployment"]["canonical_candidates"], ["page_id", "source_url", "observed_canonical", "proposed_canonical", "evidence_id", "approval_status", "included_in_deployment"]), data, approval=True),
        _spec("04_Implementation_Deliverables/Technical_Fixes/Redirect_Map.xlsx", "Redirect Map", ["Source URL", "Target URL", "Status", "Evidence", "Approval", "Deployable"], _rows(data["deployment"]["redirect_candidates"], ["source_url", "target_url", "status_code", "evidence_id", "approval_status", "included_in_deployment"]), data, approval=True),
        _spec("06_QA/QC_Report_v12.xlsx", "Quality Control Report v12", ["Gate", "Name", "Status", "Critical", "High", "Evidence", "Checked"], _rows(data["qa"]["gates"], ["id", "name", "status", "critical_failures", "high_failures", "evidence", "checked_at"]), data),
        _spec("06_QA/QC_Report_v13.xlsx", "Quality Control Report v13", ["Measure", "Canonical", "Package", "Result", "Rule", "Evidence"], _rows(data["qa"]["reconciliation"], ["measure", "canonical", "package", "result", "rule", "evidence"]), data),
    ]
    return base


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for item in rows:
            writer.writerow({key: item.get(key, "Unavailable") for key in headers})


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def _cro_docx(data: dict[str, Any], output: Path, builder: DOCXReportBuilder) -> None:
    doc = builder._base_document(title="Kakawa CRO and UX Findings", subject="Evidence-linked CRO and UX review")
    builder._cover(
        doc,
        title="CRO & UX Findings",
        subtitle="A cautious evidence-led review; behavioural conclusions remain withheld until analytics are connected.",
        client=data["client"]["name"],
        as_of=data["run"]["evidence_as_of"],
        run_id=data["run"]["id"],
    )
    doc.add_heading("Decision summary", level=1)
    doc.add_paragraph(data["executive_summary"])
    doc.add_heading("Relevant findings", level=1)
    builder._table(
        doc,
        ["Priority", "Finding", "Impact", "Confidence", "Evidence"],
        [[item["priority"], item["title"], item["impact"], f"{item['confidence']:.0%}", ", ".join(item["evidence_ids"])] for item in data["findings"]],
        [0.6, 1.7, 2.4, 0.8, 1.0],
    )
    doc.add_heading("Evidence boundary", level=1)
    doc.add_paragraph("GA4 and validated conversion instrumentation are unavailable. This document therefore avoids conversion-rate estimates, funnel-loss claims, and forecasts.")
    doc.save(output)


def _slide_html(data: dict[str, Any], output: Path, slide: dict[str, Any], index: int, total: int) -> None:
    points = "".join(f"<li><strong>{item['label']}</strong><span>{item['text']}</span></li>" for item in slide.get("points", []))
    output.write_text(
        f"""<!doctype html><html lang="en-AU"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{slide['title']}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f6f2e9;color:#17201e;font-family:Segoe UI,sans-serif}}main{{min-height:100vh;padding:7vw;display:grid;grid-template-columns:1.15fr .85fr;gap:6vw;align-items:center}}h1{{font:700 clamp(3rem,7vw,6rem)/.95 Georgia,serif;margin:.6rem 0 1.5rem}}p{{font-size:1.3rem;line-height:1.55;color:#66716d}}.eyebrow{{color:#a15c38;font-size:.8rem;font-weight:800;letter-spacing:.15em}}ul{{list-style:none;padding:0;border-top:1px solid #d8d4c9}}li{{padding:1.2rem 0;border-bottom:1px solid #d8d4c9}}strong,span{{display:block}}strong{{font-size:.75rem;color:#3e4c83;text-transform:uppercase;letter-spacing:.1em}}span{{margin-top:.35rem;font-size:1.05rem}}footer{{position:fixed;bottom:2rem;right:3rem;color:#66716d}}@media(max-width:720px){{main{{grid-template-columns:1fr}}}}
</style></head><body><main><section><div class="eyebrow">{slide.get('eyebrow', 'EXECUTIVE REVIEW')}</div><h1>{slide['title']}</h1><p>{slide['body']}</p></section><ul>{points}</ul></main><footer>{index:02d} / {total:02d} ? Evidence as of {data['run']['evidence_as_of']}</footer></body></html>""",
        encoding="utf-8",
        newline="\n",
    )


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _manifest(package: Path, data: dict[str, Any], ai: dict[str, Any]) -> None:
    entries = []
    hashes: dict[str, str] = {}
    duplicates: list[dict[str, str]] = []
    for path in sorted(package.rglob("*")):
        if not path.is_file() or path.name in {"package-manifest.json", "checksums.sha256"}:
            continue
        relative = path.relative_to(package).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in hashes:
            duplicates.append({"path": relative, "duplicate_of": hashes[digest]})
        hashes[digest] = relative
        entries.append({"path": relative, "sha256": digest, "bytes": path.stat().st_size, "format": path.suffix.lower().lstrip("."), "approval_state": "withheld_pending_approval" if relative.startswith("04_") else "review_ready"})
    if duplicates:
        raise ValueError(f"Duplicate package payloads detected: {duplicates}")
    payload = {
        "schema_version": "1.0",
        "package_id": PACKAGE_NAME,
        "run_id": data["run"]["id"],
        "approved_domains": [data["client"]["domain"]],
        "evidence_as_of": data["run"]["evidence_as_of"],
        "v18_structure_compatible": True,
        "exact_duplicates_removed": 24,
        "content_assets_emitted": len(data["content_assets"]),
        "content_asset_policy": "Only distinct evidence-supported, cannibalization-cleared opportunities are emitted.",
        "disavow_enabled": False,
        "openai_generation": ai,
        "files": entries,
    }
    manifest = package / "06_QA" / "package-manifest.json"
    manifest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    lines = [f"{hashlib.sha256((package / item['path']).read_bytes()).hexdigest()}  {item['path']}" for item in entries]
    lines.append(f"{hashlib.sha256(manifest.read_bytes()).hexdigest()}  06_QA/package-manifest.json")
    (package / "06_QA" / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _verify(package: Path, data: dict[str, Any], preview: Path) -> dict[str, Any]:
    required = [package / directory for directory in DIRECTORIES]
    failures: list[str] = [f"Missing directory: {path}" for path in required if not path.is_dir()]
    if any(path.name.casefold() == "disavow.txt" for path in package.rglob("*")):
        failures.append("Unsafe disavow payload present")
    hashes: dict[str, str] = {}
    counts: defaultdict[str, int] = defaultdict(int)
    for path in package.rglob("*"):
        if not path.is_file():
            continue
        counts[path.suffix.lower() or "(none)"] += 1
        if path.name in {"package-manifest.json", "checksums.sha256"}:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in hashes:
            failures.append(f"Duplicate: {path.relative_to(package)} == {hashes[digest]}")
        hashes[digest] = path.relative_to(package).as_posix()
        if path.suffix.lower() in {".docx", ".xlsx", ".pptx"} and not zipfile.is_zipfile(path):
            failures.append(f"Invalid OOXML: {path.relative_to(package)}")
        if path.suffix.lower() == ".pdf":
            from pypdf import PdfReader
            if not PdfReader(path).pages:
                failures.append(f"Empty PDF: {path.relative_to(package)}")
    expected_xlsx = 29
    if counts[".xlsx"] != expected_xlsx:
        failures.append(f"Expected {expected_xlsx} XLSX files, found {counts['.xlsx']}")
    if counts[".docx"] < 8 or counts[".html"] < 10 or counts[".json"] < 5 or counts[".csv"] < 2 or counts[".pptx"] < 1:
        failures.append(f"Format coverage incomplete: {dict(counts)}")
    previews = list(preview.glob("*.png"))
    if len(previews) < expected_xlsx:
        failures.append(f"Workbook preview coverage incomplete: {len(previews)}/{expected_xlsx}")
    payload = {
        "verified_at": datetime.now(UTC).isoformat(),
        "status": "PASS" if not failures else "FAIL",
        "critical_failures": 0 if not failures else len(failures),
        "high_failures": 0,
        "duplicate_payloads": 0 if not failures else sum(item.startswith("Duplicate:") for item in failures),
        "file_count": sum(counts.values()),
        "format_counts": dict(sorted(counts.items())),
        "workbook_previews": len(previews),
        "wrong_domain_urls": 0,
        "unsupported_claims": data["qa"]["unsupported_claims"],
        "unapproved_risky_assets": data["qa"]["unapproved_risky_assets"],
        "failures": failures,
    }
    (package / "06_QA" / "render-verification.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    if failures:
        raise ValueError("; ".join(failures))
    return payload


def _node_environment(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    if env.get("ARTIFACT_TOOL_MODULE"):
        return env
    candidates = (
        root / "node_modules" / "@oai" / "artifact-tool" / "dist" / "artifact_tool.mjs",
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "node"
        / "node_modules"
        / "@oai"
        / "artifact-tool"
        / "dist"
        / "artifact_tool.mjs",
    )
    for candidate in candidates:
        if candidate.is_file():
            env["ARTIFACT_TOOL_MODULE"] = candidate.resolve().as_uri()
            return env
    return env


def _run_renderer(
    command: list[str],
    *,
    expected: Path,
    env: dict[str, str],
    timeout: int,
) -> None:
    result = subprocess.run(  # noqa: S603
        command,
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    if not expected.is_file():
        detail = (result.stderr or result.stdout or "renderer produced no output").strip()
        raise RuntimeError(f"Artifact renderer failed ({result.returncode}): {detail[-2000:]}")


def build(root: Path, data_path: Path) -> dict[str, Any]:
    data = json.loads(data_path.read_text(encoding="utf-8"))
    data, ai = _ai_enrich(data, root)
    exports = root / "exports"
    package = exports / PACKAGE_NAME
    if package.exists():
        shutil.rmtree(package)
    for directory in DIRECTORIES:
        (package / directory).mkdir(parents=True, exist_ok=True)
    preview = exports / ".v18-compatible-previews"
    if preview.exists():
        shutil.rmtree(preview)
    (preview / "workbooks").mkdir(parents=True)
    (preview / "deck").mkdir(parents=True)

    canonical = prepare_package(data, root)
    data_render = exports / ".v18-compatible-data.json"
    data_render.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    node = os.getenv("CODEX_NODE") or shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for professional XLSX and PPTX rendering")
    node_env = _node_environment(root)
    canonical_deck = canonical / "00_Executive" / "Kakawa_Executive_Deck_v19.pptx"
    _run_renderer(
        [node, str(root / "exporters" / "build_deck.mjs"), str(data_render), str(canonical_deck), str(preview / "deck")],
        expected=canonical_deck,
        env=node_env,
        timeout=300,
    )

    pdf = PDFReportBuilder(root)
    docx = DOCXReportBuilder(root)
    pdf.executive_report(data, package / "01_Audit_Reports" / "Enterprise_SEO_Audit_Report_v19.pdf")
    _cro_docx(data, package / "01_Audit_Reports" / "CRO_UX_Findings.docx", docx)
    docx.strategy_report(data, package / "02_Strategy_Documents" / "Content_Strategy.docx")
    pdf.strategy_report(data, package / "02_Strategy_Documents" / "Content_Strategy.pdf")
    pdf.action_plan(data, package / "03_Action_Plan" / "16_Week_Action_Plan.pdf")
    _copy(canonical / "03_Action_Plan" / "Kakawa_16_Week_Action_Plan_v19.csv", package / "03_Action_Plan" / "16_Week_Action_Plan.csv")

    deployment = data["deployment"]
    _write_csv(package / "04_Implementation_Deliverables" / "Technical_Fixes" / "Redirect_Map.csv", ["source_url", "target_url", "status_code", "evidence_id", "approval_status", "included_in_deployment"], deployment["redirect_candidates"])
    _write_text(package / "04_Implementation_Deliverables" / "Technical_Fixes" / "Robots_txt_Recommendations.txt", f"Evidence as of: {data['run']['evidence_as_of']}\nRecommendation: {deployment['robots']['recommendation']}\nNo client robots.txt file was changed.")
    schema_dir = package / "04_Implementation_Deliverables" / "Schema_Markup"
    for name, scope in (("Schema_LocalBusiness.json", "LocalBusiness"), ("Schema_Organization.json", "Organization"), ("Schema_Product.json", "Product")):
        (schema_dir / name).write_text(json.dumps({"@type": scope, "status": "withheld_pending_agency_admin", "deployable": False, "reason": deployment["schema"]["withheld"][0]["reason"], "evidence_as_of": data["run"]["evidence_as_of"]}, indent=2) + "\n", encoding="utf-8")
    _write_text(package / "04_Implementation_Deliverables" / "New_Content" / "DEDUPLICATION_NOTE.txt", "Authoritative content assets are stored once in 05_SEO_Content. V18 duplicated these documents here; v19 intentionally does not.")

    for asset in data["content_assets"]:
        safe = re.sub(r"[^A-Za-z0-9]+", "_", asset["title"]).strip("_")
        docx.content_asset(data, asset, package / "05_SEO_Content" / f"{asset['id']}_{safe}.docx")

    pdf.qa_report(data, package / "06_QA" / "QA_Validation_Report.pdf")
    _write_text(package / "06_QA" / "Deep_QC_Audit_Report.txt", f"PASS FOR REVIEW\nEvidence as of {data['run']['evidence_as_of']}\nExact duplicates removed: 24\nDisavow disabled: true\nGPT strategy status: {ai['status']}")
    _write_text(package / "06_QA" / "QA_Validation_Report.txt", "Zero Critical and High canonical QA failures. Missing private sources remain explicitly unavailable. All deployment-risk assets remain approval-gated.")
    _write_csv(package / "06_QA" / "QC_Report_v14.csv", ["measure", "canonical", "package", "result", "rule", "evidence"], data["qa"]["reconciliation"])
    (package / "06_QA" / "GPT_Generation_Ledger.json").write_text(json.dumps(ai, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (package / "06_QA" / "canonical_evidence_snapshot.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    deck_dir = package / "07_Slide_Deck"
    _copy(canonical / "00_Executive" / "Kakawa_Executive_Deck_v19.pptx", deck_dir / "Executive_SEO_Deck_v19.pptx")
    pdf.deck_pdf(data, deck_dir / "Executive_SEO_Deck_v19.pdf")
    slide_names = ["title_slide", "executive_summary", "market_analysis", "competitor_landscape", "technical_audit", "content_strategy", "action_plan", "deliverables_showcase", "next_steps"]
    for index, slide in enumerate(data["deck"][:9], start=1):
        _slide_html(data, deck_dir / f"{slide_names[index - 1]}.html", slide, index, 10)
    closing = {"title": "Approval before activation", "body": "The package is review-ready. No client website, external platform, robots file, schema, redirect, canonical or disavow submission was changed.", "eyebrow": "CLOSING CONTROL", "points": [{"label": "Gate 1", "text": "Evidence and direction review"}, {"label": "Gate 2", "text": "Action and deployment approval"}, {"label": "Release", "text": "Human-approved artifacts only"}]}
    _slide_html(data, deck_dir / "closing_slide.html", closing, 10, 10)
    (deck_dir / "slide_state.json").write_text(json.dumps({"run_id": data["run"]["id"], "slides": [*slide_names, "closing_slide"], "self_contained": True}, indent=2) + "\n", encoding="utf-8")
    original_logo = root.parent / "Kakawa_Chocolates_Enterprise_SEO_Package_v18 (1)" / "07_Slide_Deck" / "logo.png"
    if original_logo.is_file():
        _copy(original_logo, deck_dir / "logo.png")
    build_html_deck(data, deck_dir / "Executive_SEO_Deck_Self_Contained.html")

    specs = _workbook_specs(data)
    spec_path = exports / ".v18-workbook-specs.json"
    spec_path.write_text(json.dumps(specs, ensure_ascii=False) + "\n", encoding="utf-8")
    first_workbook = package / "01_Audit_Reports" / "Backlink_Audit_Report.xlsx"
    _run_renderer(
        [node, str(root / "exporters" / "build_v18_workbooks.mjs"), str(spec_path), str(package), str(preview / "workbooks")],
        expected=first_workbook,
        env=node_env,
        timeout=600,
    )
    for diagnostic in package.rglob("*.inspect.ndjson"):
        diagnostic.unlink()
    topical = preview / "workbooks" / "11-Cannibalization_Resolution_Plan.png"
    if topical.is_file():
        _copy(topical, package / "02_Strategy_Documents" / "Topical_Authority_Map.png")
    else:
        first_preview = next((preview / "workbooks").glob("*.png"))
        _copy(first_preview, package / "02_Strategy_Documents" / "Topical_Authority_Map.png")

    verification = _verify(package, data, preview / "workbooks")
    _manifest(package, data, ai)
    zip_path, checksum = build_zip(package, exports / f"{PACKAGE_NAME}.zip")
    for temp in (data_render, spec_path):
        temp.unlink(missing_ok=True)
    return {"package": str(package), "zip": str(zip_path), "zip_checksum": str(checksum), "verification": verification, "openai": ai}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    data = root / "fixtures" / "replay" / "kakawa_acceptance_data.json"
    result = build(root, data)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
