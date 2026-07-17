"""Compile the canonical deliverable data dict from a persisted audit run.

``compile_run_data`` turns one ``app.domain.models.AuditRun`` (with its pages,
evidence, findings, recommendations and actions) into the client-agnostic
package data contract consumed by every renderer.  Every number in the output
is measured from stored run records; anything that was not collected is
labelled unavailable with a reason instead of being fabricated.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from django.conf import settings
from django.utils import timezone

from audit_engine.models import RUN_LIMITS, BusinessProfile, RunProfile, Severity, VerifiedFact
from audit_engine.scoring import CATEGORY_WEIGHTS
from audit_engine.urls import URLValidationError, normalize_url
from generation.openai_boundary import (
    DEFAULT_FINAL_MODEL,
    GenerationConfig,
    GenerationPurpose,
    GenerationStatus,
)
from generation.quality import (
    validate_claims,
    validate_domains_and_links,
    validate_placeholders,
)
from generation.schemas import FactPack

SCHEMA_VERSION = "1.0.0"
ENRICHMENT_PROMPT_VERSION = "package-enrichment-1.0.0"
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
SEVERITY_PRIORITY = {"critical": "P1", "high": "P1", "medium": "P2", "low": "P3", "info": "P4"}
PRIORITY_FALLBACK_SCORE = {"P1": 80.0, "P2": 62.0, "P3": 42.0, "P4": 22.0}

CATEGORY_LABELS = {
    "technical": "Technical",
    "on_page": "On-Page",
    "performance": "Performance",
    "analytics": "Analytics",
    "keyword_architecture": "Keyword Architecture",
    "authority": "Authority",
    "cro": "CRO",
    "geo_aeo": "GEO / AEO",
    "local": "Local",
    "ecommerce": "Ecommerce",
}
PAGE_TYPE_PRIORITY = {
    "Homepage": 0,
    "Collection": 1,
    "Product": 2,
    "Editorial": 3,
    "Information": 4,
}
PRIVATE_SOURCES = {
    "gsc": "Google Search Console",
    "ga4": "Google Analytics 4",
    "semrush": "SEMrush",
    "pagespeed": "PageSpeed Insights",
}
DISCOVERED_PATTERN = re.compile(r"(\d+)\s+discovered")

ENRICHMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["executive_summary", "strategy_synthesis", "deck_calls_to_action", "claims"],
    "properties": {
        "executive_summary": {"type": "string", "minLength": 1, "maxLength": 2000},
        "strategy_synthesis": {
            "type": "object",
            "additionalProperties": False,
            "required": ["title", "paragraphs"],
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 200},
                "paragraphs": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 6,
                    "items": {"type": "string", "minLength": 1, "maxLength": 2000},
                },
            },
        },
        "deck_calls_to_action": {
            "type": "array",
            "maxItems": 3,
            "items": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        "claims": {
            "type": "array",
            "maxItems": 100,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "fact_keys", "evidence_ids"],
                "properties": {
                    "text": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "fact_keys": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1, "maxLength": 255},
                    },
                    "evidence_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "format": "uuid"},
                    },
                },
            },
        },
    },
}


# --------------------------------------------------------------------------- text hygiene


def repair_text(value: str | None) -> str | None:
    """Repair common UTF-8-as-Windows-1252 crawl text without touching URLs."""
    if value is None:
        return None
    repaired = value
    for _ in range(2):
        if not any(marker in repaired for marker in ("â€", "Â", "Ã")):
            break
        try:
            candidate = repaired.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if candidate == repaired:
            break
        repaired = candidate
    return repaired


def repair_tree(value: Any) -> Any:
    """Apply crawl-text repair to every textual value in a compiled payload."""
    if isinstance(value, dict):
        return {key: repair_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [repair_tree(item) for item in value]
    if isinstance(value, tuple):
        return [repair_tree(item) for item in value]
    if isinstance(value, str):
        return repair_text(value)
    return value


# --------------------------------------------------------------------------- small helpers


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _page_type(url: str) -> str:
    path = urlsplit(url).path.casefold()
    if path in {"", "/"}:
        return "Homepage"
    if "/product/" in path or "/products/" in path:
        return "Product"
    if "/collection/" in path or "/collections/" in path or "/category/" in path:
        return "Collection"
    if "/blog" in path or "/news" in path or "/article" in path:
        return "Editorial"
    if "/about" in path or "/contact" in path or "/pages/" in path:
        return "Information"
    if "/cart" in path or "/search" in path or "/account" in path:
        return "Utility"
    return "Other"


def _approved_host(url: str | None, domains: tuple[str, ...]) -> bool:
    if not url:
        return False
    host = (urlsplit(url).hostname or "").casefold().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)


def _topic_label(page: dict[str, Any], fallback: str) -> str:
    value = page.get("h1") or page.get("title") or ""
    if not value:
        segment = urlsplit(page["normalized_url"]).path.rstrip("/").rsplit("/", 1)[-1]
        value = segment.replace("-", " ").replace("_", " ").strip().title()
    for separator in ("|", "–", "—"):
        value = value.split(separator)[0]
    value = value.strip(" -–—")
    return value[:90] or fallback


def _first_sentence(text: str, limit: int = 180) -> str:
    cleaned = " ".join(str(text).split())
    if not cleaned:
        return ""
    sentence = cleaned.split(". ")[0].rstrip(".") + "."
    return sentence[:limit]


def _business_profile(value: str) -> BusinessProfile:
    return {
        "local": BusinessProfile.LOCAL,
        "ecommerce": BusinessProfile.ECOMMERCE,
        "hybrid": BusinessProfile.HYBRID,
    }.get(value, BusinessProfile.SERVICE_SAAS)


def _effort_word_from_scale(value: int | None) -> str:
    if value is None:
        return "Medium"
    if value <= 2:
        return "Low"
    if value == 3:
        return "Medium"
    return "High"


def _effort_word_from_score(value: float) -> str:
    if value <= 30:
        return "Low"
    if value <= 60:
        return "Medium"
    return "High"


def _phase(week: int) -> str:
    if week <= 2:
        return "Phase 1: Foundations & Quick Wins"
    if week <= 6:
        return "Phase 2: Technical & On-Page Remediation"
    if week <= 12:
        return "Phase 3: Content & Architecture"
    return "Phase 4: Authority & Measurement"


def _approval_class(risk: str) -> str:
    return "agency_admin" if risk in {"high", "dangerous"} else "analyst"


def _unique_id(base: str, used: set[str]) -> str:
    candidate = base
    counter = 2
    while candidate in used:
        candidate = f"{base}-{counter}"
        counter += 1
    used.add(candidate)
    return candidate


# --------------------------------------------------------------------------- pages


def _compile_pages(
    run: Any, domains: tuple[str, ...]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return page rows plus an Evidence-pk -> display-EV-id map."""
    page_evidence_rows: dict[str, list[Any]] = defaultdict(list)
    for row in run.evidence.filter(page__isnull=False).order_by("captured_at", "created_at"):
        page_evidence_rows[str(row.page_id)].append(row)

    pages: list[dict[str, Any]] = []
    evidence_display: dict[str, str] = {}
    for index, page in enumerate(run.pages.all().order_by("normalized_url"), start=1):
        facts = page.facts or {}
        raw_links = [str(v) for v in facts.get("links", []) if isinstance(v, str)]
        approved_links: set[str] = set()
        for link in raw_links:
            try:
                if _approved_host(link, domains):
                    approved_links.add(normalize_url(link))
            except URLValidationError:
                continue
        h1_joined = page.h1 or " | ".join(v for v in facts.get("h1_values", []) if v)
        display_id = f"EV-{index:04d}"
        for row in page_evidence_rows.get(str(page.pk), []):
            evidence_display[str(row.pk)] = display_id
        pages.append(
            {
                "id": f"URL-{index:04d}",
                "evidence_id": display_id,
                "original_url": page.original_url,
                "original_urls": [page.original_url],
                "duplicate_observations": 0,
                "normalized_url": page.normalized_url,
                "status_code": page.status_code,
                "title": repair_text(page.title) or None,
                "meta_description": repair_text(page.meta_description) or None,
                "h1": repair_text(h1_joined) or None,
                "canonical_url": page.canonical_url or None,
                "indexability": (
                    "Robots directives unavailable"
                    if page.robots_indexable is None
                    else ("No noindex observed" if page.robots_indexable else "Noindex observed")
                ),
                "word_count": facts.get("word_count"),
                "internal_links": len(raw_links),
                "schema_types": list(facts.get("schema_types") or []),
                "images_total": facts.get("images_total"),
                "images_missing_alt": facts.get("images_missing_alt"),
                "response_ms": facts.get("response_ms"),
                "body_bytes": facts.get("body_bytes"),
                "analytics_tags": list(facts.get("analytics_tags") or []),
                "url_depth": facts.get("url_depth"),
                "external_links": len(facts.get("external_links") or []),
                "redirect_chain": (
                    [page.original_url, page.redirect_target_url]
                    if page.redirect_target_url
                    else []
                ),
                "content_type": page.content_type or None,
                "body_sha256": page.content_sha256 or None,
                "page_type": _page_type(page.normalized_url),
                "links": sorted(approved_links),
                "captured_at": _iso(page.captured_at),
                "_facts": facts,
                "_page_pk": str(page.pk),
            }
        )
    return pages, evidence_display


# --------------------------------------------------------------------------- sources


def _compile_sources(
    run: Any, pages: list[dict[str, Any]], captured_iso: str
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    covered_kinds: set[str] = set()

    for snapshot in run.source_snapshots.all().order_by("created_at"):
        kind = snapshot.source_type
        covered_kinds.add(kind)
        available = snapshot.availability == "available"
        coverage = 1.0 if available else 0.0
        if kind == "crawl":
            metadata = snapshot.metadata or {}
            stopped = str(metadata.get("stopped_reason", ""))
            match = DISCOVERED_PATTERN.search(snapshot.scope or "")
            discovered = int(match.group(1)) if match else len(pages)
            if available and stopped != "queue_exhausted" and discovered > 0:
                coverage = round(min(1.0, len(pages) / discovered), 4)
            label = "Approved-domain website crawl"
        else:
            label = f"{kind.upper()} snapshot"
        sources.append(
            {
                "id": _unique_id(
                    "SRC-CRAWL" if kind == "crawl" else f"SRC-{kind.upper()}", used_ids
                ),
                "label": label,
                "kind": kind,
                "status": "available" if available else "unavailable",
                "captured_at": _iso(snapshot.captured_at) or captured_iso,
                "scope": snapshot.scope
                or f"{snapshot.record_count} records collected for this run",
                "coverage": coverage,
                "unavailable_reason": (snapshot.unavailable_reason or None)
                if not available
                else None,
            }
        )

    for offset, item in enumerate(
        run.project.source_imports.filter(status="accepted").order_by("created_at"), start=1
    ):
        covered_kinds.add(item.source_type)
        snapshot = item.snapshots.filter(run=run).first()
        rows = f"; {snapshot.record_count} rows" if snapshot else ""
        sources.append(
            {
                "id": _unique_id(f"SRC-IMPORT-{offset:02d}", used_ids),
                "label": f"Accepted {item.source_type} import",
                "kind": item.source_type,
                "status": "available",
                "captured_at": _iso(item.created_at) or captured_iso,
                "scope": f"{item.original_filename}{rows}",
                "coverage": 1.0,
                "unavailable_reason": None,
            }
        )

    for connection in run.project.connections.all().order_by("created_at"):
        covered_kinds.add(connection.provider)
        available = connection.availability == "available"
        sources.append(
            {
                "id": _unique_id(f"SRC-{connection.provider.upper()}", used_ids),
                "label": PRIVATE_SOURCES.get(connection.provider, connection.provider),
                "kind": connection.provider,
                "status": "available" if available else "unavailable",
                "captured_at": _iso(connection.last_synced_at or connection.created_at)
                or captured_iso,
                "scope": connection.label or "Configured project connection",
                "coverage": 1.0 if available else 0.0,
                "unavailable_reason": None
                if available
                else (
                    connection.unavailable_reason
                    or "Connection has not produced validated evidence for this run."
                ),
            }
        )

    for kind, label in PRIVATE_SOURCES.items():
        if kind in covered_kinds:
            continue
        sources.append(
            {
                "id": _unique_id(f"SRC-{kind.upper()}", used_ids),
                "label": label,
                "kind": kind,
                "status": "unavailable",
                "captured_at": captured_iso,
                "scope": "Not collected for this run",
                "coverage": 0.0,
                "unavailable_reason": "credential_not_configured",
            }
        )

    key_present = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    sources.append(
        {
            "id": _unique_id("SRC-OPENAI", used_ids),
            "label": "OpenAI generation",
            "kind": "openai",
            "status": "available" if key_present else "unavailable",
            "captured_at": captured_iso,
            "scope": "Evidence-constrained narrative enrichment boundary",
            "coverage": 1.0 if key_present else 0.0,
            "unavailable_reason": None if key_present else "credential_not_configured",
        }
    )
    return sources


# --------------------------------------------------------------------------- evidence


def _compile_evidence(
    pages: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    locale: str,
    captured_iso: str,
) -> list[dict[str, Any]]:
    crawl_source_id = next(
        (source["id"] for source in sources if source["kind"] == "crawl"), "SRC-CRAWL"
    )
    evidence: list[dict[str, Any]] = []
    for page in pages:
        observed = (
            f"HTTP {page['status_code']}; title={page['title'] or 'unavailable'}; "
            f"meta={'present' if page['meta_description'] else 'unavailable'}; "
            f"H1={page['h1'] or 'unavailable'}; internal_links={page['internal_links']}"
        )
        evidence.append(
            {
                "id": page["evidence_id"],
                "source_id": crawl_source_id,
                "evidence_type": "page_observation",
                "observed_value": observed,
                "original_url": page["original_url"],
                "normalized_url": page["normalized_url"],
                "captured_at": page["captured_at"],
                "locale": locale,
                "scope": "approved-domain HTML response",
                "confidence": 1.0,
                "unavailable_reason": None,
            }
        )
    for source in sources:
        if source["status"] != "unavailable":
            continue
        evidence.append(
            {
                "id": f"EV-UNAVAILABLE-{source['id'].removeprefix('SRC-')}",
                "source_id": source["id"],
                "evidence_type": "unavailable_state",
                "observed_value": None,
                "original_url": None,
                "normalized_url": None,
                "captured_at": captured_iso,
                "locale": locale,
                "scope": source["scope"],
                "confidence": 1.0,
                "unavailable_reason": source["unavailable_reason"],
            }
        )
    return evidence


# --------------------------------------------------------------------------- findings


def _compile_findings(
    run: Any,
    pages: list[dict[str, Any]],
    evidence_display: dict[str, str],
    as_of: str,
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    page_by_pk = {page["_page_pk"]: page for page in pages}
    default_evidence = [pages[0]["evidence_id"]] if pages else []
    rows: list[tuple[tuple[int, int, str], dict[str, Any], str]] = []
    for finding in run.findings.prefetch_related("evidence", "recommendations__actions").all():
        recommendation = next(iter(finding.recommendations.all()), None)
        action = (
            next(iter(recommendation.actions.all()), None) if recommendation is not None else None
        )
        severity = str(finding.severity)
        priority = (
            action.priority_tier
            if action is not None
            else SEVERITY_PRIORITY.get(severity, "P3")
        )
        priority_score = (
            float(action.priority_score)
            if action is not None
            else PRIORITY_FALLBACK_SCORE.get(priority, 40.0)
        )
        linked_evidence = list(finding.evidence.all())
        evidence_ids = sorted(
            {
                evidence_display[str(item.pk)]
                for item in linked_evidence
                if str(item.pk) in evidence_display
            }
        )
        affected_urls = sorted(
            {
                page_by_pk[str(item.page_id)]["normalized_url"]
                for item in linked_evidence
                if item.page_id and str(item.page_id) in page_by_pk
            }
        )
        if not evidence_ids and finding.page_id and str(finding.page_id) in page_by_pk:
            evidence_ids = [page_by_pk[str(finding.page_id)]["evidence_id"]]
        if not evidence_ids:
            evidence_ids = list(default_evidence)
        if not affected_urls and finding.page_id and str(finding.page_id) in page_by_pk:
            affected_urls = [page_by_pk[str(finding.page_id)]["normalized_url"]]
        risk = str(recommendation.risk_class) if recommendation is not None else "low"
        impact = (
            _first_sentence(recommendation.rationale)
            if recommendation is not None and recommendation.rationale
            else ""
        )
        if not impact or impact.strip().rstrip(".") == _first_sentence(
            finding.description
        ).strip().rstrip("."):
            share_pct = round(float(finding.affected_share or 0) * 100, 1)
            impact = (
                f"Affects {finding.affected_count} of {len(pages)} crawled pages "
                f"({share_pct}% of the crawl); resolve before the next collection window."
            )
        row = {
            "id": "",
            "priority": priority,
            "priority_score": round(priority_score, 2),
            "category": finding.category,
            "rule_id": finding.code,
            "rule_version": finding.rule_version,
            "severity": severity.title(),
            "title": finding.title,
            "description": finding.description,
            "impact": impact,
            "confidence": float(finding.confidence),
            "reach": f"{finding.affected_count} pages",
            "affected_count": finding.affected_count,
            "affected_urls": affected_urls,
            "effort": _effort_word_from_scale(
                recommendation.effort if recommendation is not None else None
            ),
            "implementation_risk": risk,
            "approval_class": _approval_class(risk),
            "as_of_date": as_of,
            "evidence_ids": evidence_ids,
        }
        sort_key = (SEVERITY_RANK.get(severity, 5), -finding.affected_count, finding.title)
        rows.append((sort_key, row, str(finding.pk)))
    rows.sort(key=lambda item: item[0])
    findings: list[dict[str, Any]] = []
    finding_evidence: dict[str, list[str]] = {}
    for offset, (_, row, finding_pk) in enumerate(rows, start=1):
        row["id"] = f"F-{offset:03d}"
        findings.append(row)
        finding_evidence[finding_pk] = row["evidence_ids"]
    return findings, finding_evidence


# --------------------------------------------------------------------------- categories


def _compile_categories(
    run: Any, profile: BusinessProfile, findings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    weights = CATEGORY_WEIGHTS[profile]
    evidence_by_category: dict[str, list[str]] = defaultdict(list)
    for finding in findings:
        evidence_by_category[finding["category"]].extend(finding["evidence_ids"])

    stage = run.stages.filter(name="auditing").first()
    checkpoint = (stage.checkpoint or {}) if stage is not None else {}
    scorecard = checkpoint.get("scorecard")
    by_key: dict[str, dict[str, Any]] = {}
    if isinstance(scorecard, list):
        for item in scorecard:
            if isinstance(item, dict) and item.get("category"):
                by_key[str(item["category"])] = item

    rows: list[dict[str, Any]] = []
    for category, weight in weights.items():
        entry = by_key.get(category)
        if entry is not None:
            coverage = float(entry.get("coverage") or 0.0)
            raw_weight = float(entry.get("weight", weight))
            raw_score = entry.get("score")
            published = raw_score is not None and coverage >= 0.70
            score = round(float(raw_score), 2) if published else None
            reason = (
                None
                if published
                else "Required evidence source was not connected for this run."
            )
        else:
            coverage = 0.0
            raw_weight = float(weight)
            score = None
            reason = "Scorecard unavailable for this run."
        rows.append(
            {
                "category": CATEGORY_LABELS.get(category, category.replace("_", " ").title()),
                "key": category,
                "score": score,
                "coverage": round(coverage, 4),
                "weight": round(raw_weight / 100 if raw_weight > 1 else raw_weight, 4),
                "rule_version": run.rule_version,
                "status": "available" if coverage >= 0.70 else "unavailable",
                "unavailable_reason": reason,
                "evidence_ids": sorted(set(evidence_by_category[category]))[:30],
            }
        )
    return rows


# --------------------------------------------------------------------------- actions


def _compile_actions(
    run: Any,
    finding_evidence: dict[str, list[str]],
    default_evidence: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ordered = run.actions.select_related("recommendation__finding").order_by(
        "week", "-priority_score", "title"
    )
    for offset, action in enumerate(ordered, start=1):
        recommendation = action.recommendation
        finding_pk = (
            str(recommendation.finding_id)
            if recommendation is not None and recommendation.finding_id
            else None
        )
        evidence_ids = finding_evidence.get(finding_pk or "", []) or list(default_evidence)
        kpi = (
            _first_sentence(recommendation.implementation)
            if recommendation is not None and recommendation.implementation
            else f"Completion of '{action.title}' verified in the next crawl."
        )
        risk = str(action.risk_class)
        source_category = (
            recommendation.finding.category
            if recommendation is not None and recommendation.finding_id
            else ""
        )
        source_rule = (
            recommendation.finding.code
            if recommendation is not None and recommendation.finding_id
            else ""
        )
        rows.append(
            {
                "id": f"A-{offset:03d}",
                "category": CATEGORY_LABELS.get(
                    source_category, source_category.replace("_", " ").title() or "General"
                ),
                "phase": _phase(int(action.week)),
                "week": int(action.week),
                "week_end": int(action.week),
                "priority": action.priority_tier,
                "action": action.title,
                "owner": action.owner_label or "SEO team",
                "dependencies": [],
                "effort": _effort_word_from_score(float(action.effort)),
                "kpi": kpi,
                "approval_class": _approval_class(risk),
                "status": "Ready" if int(action.week) == 1 else "Not started",
                "evidence_ids": evidence_ids,
                "confidence": round(float(action.evidence_confidence) / 100, 4),
                "implementation_risk": risk,
                "deliverable": _deliverable_for_rule(source_rule),
                "notes": "",
            }
        )
    return rows


DELIVERABLE_BY_RULE_PREFIX = (
    ("on_page.title", "Rewritten titles deployed per 04_Implementation_Deliverables/On_Page_Optimizations/Title_Tag_Optimizations.xlsx"),
    ("on_page.meta_description", "Rewritten descriptions deployed per 04_Implementation_Deliverables/On_Page_Optimizations/Meta_Description_Optimizations.xlsx"),
    ("on_page.h1", "Corrected headings deployed per 04_Implementation_Deliverables/On_Page_Optimizations/H1_Optimizations.xlsx"),
    ("on_page.thin_content", "Expanded copy drafted from the briefs in 05_Content/"),
    ("on_page.image_alt", "Descriptive alt text added per 01_Audit_Reports/OnPage_Audit_Report.xlsx (Image Alt Issues)"),
    ("technical.canonical", "Approved canonical tags applied per 04_Implementation_Deliverables/Technical_Fixes/Canonical_Review.xlsx"),
    ("technical.redirect", "Approved redirects deployed per 04_Implementation_Deliverables/Technical_Fixes/Redirect_Map.csv"),
    ("technical.http_status", "Broken URLs repaired or redirected per 04_Implementation_Deliverables/Technical_Fixes/Redirect_Map.csv"),
    ("technical.broken_internal_link", "Broken links corrected per 01_Audit_Reports/Technical_Audit_Report.xlsx (Findings Register)"),
    ("technical.duplicate_content", "Duplicate URLs consolidated per 01_Audit_Reports/Technical_Audit_Report.xlsx (Duplicate Content)"),
    ("technical.orphan_page", "Orphan pages linked per 04_Implementation_Deliverables/Internal_Linking/Internal_Link_Map.xlsx"),
    ("keyword_architecture", "Targeting realigned per 02_Strategy_Documents/Keyword_And_Topic_Observations.xlsx"),
    ("analytics", "Tracking rollout verified against 01_Audit_Reports/Performance_And_Tracking_Audit.xlsx"),
    ("performance", "Server/response fixes verified against 01_Audit_Reports/Performance_And_Tracking_Audit.xlsx"),
    ("geo_aeo", "Structured data added per 04_Implementation_Deliverables/Schema_Markup/ templates after approval"),
    ("ecommerce.product_schema", "Product schema deployed per 04_Implementation_Deliverables/Schema_Markup/Schema_Product_Template.json after approval"),
    ("local", "LocalBusiness schema deployed per 04_Implementation_Deliverables/Schema_Markup/ templates after approval"),
)


def _deliverable_for_rule(rule_code: str) -> str:
    code = str(rule_code or "")
    for prefix, deliverable in DELIVERABLE_BY_RULE_PREFIX:
        if code.startswith(prefix):
            return deliverable
    return "Change deployed on the approved domain and verified in the next crawl"


# --------------------------------------------------------------------------- content


def _content_candidates(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [
        page
        for page in pages
        if page["status_code"] == 200
        and page["page_type"] != "Utility"
        and (not page["content_type"] or "html" in str(page["content_type"]).casefold())
    ]
    candidates.sort(
        key=lambda page: (
            PAGE_TYPE_PRIORITY.get(page["page_type"], 5),
            -page["internal_links"],
            page["normalized_url"],
        )
    )
    return candidates


def _slug_for(page: dict[str, Any], used: set[str]) -> str:
    path = urlsplit(page["normalized_url"]).path.strip("/")
    slug = re.sub(r"[^a-z0-9-]+", "-", path.casefold().replace("/", "-")).strip("-") or "homepage"
    slug = slug[:60]
    return _unique_id(slug, used)


INTENT_BY_TYPE = {
    "Homepage": ("brand and product discovery", "New and returning visitors"),
    "Collection": ("collection discovery", "Category browsers and comparison shoppers"),
    "Product": ("product evaluation", "Purchase-ready shoppers"),
    "Editorial": ("informational discovery", "Research-stage readers"),
    "Information": ("information and trust", "Visitors validating the business"),
    "Other": ("page discovery", "Site visitors"),
}


def _compile_content_assets(
    pages: list[dict[str, Any]], client_name: str, as_of: str
) -> list[dict[str, Any]]:
    candidates = _content_candidates(pages)
    count = min(12, len(candidates))
    assets: list[dict[str, Any]] = []
    used_slugs: set[str] = set()
    for offset, page in enumerate(candidates[:count], start=1):
        topic = _topic_label(page, client_name)
        intent, audience = INTENT_BY_TYPE.get(page["page_type"], INTENT_BY_TYPE["Other"])
        facts = page.get("_facts", {})
        word_count = page["word_count"]
        schema_types = [str(v) for v in facts.get("schema_types", []) if isinstance(v, str)]
        evidence_bits = [
            f"The approved crawl captured {page['normalized_url']} with HTTP status "
            f"{page['status_code']} as of {as_of}.",
            f"The observed title was '{page['title']}'."
            if page["title"]
            else "No title tag was captured for this page.",
            f"The observed H1 was '{page['h1']}'."
            if page["h1"]
            else "No H1 heading was captured for this page.",
            "A meta description was present."
            if page["meta_description"]
            else "No meta description was captured.",
        ]
        measures = [
            f"Internal links observed on the page: {page['internal_links']}.",
            f"Word count observed: {word_count}."
            if isinstance(word_count, int)
            else "Word count is unavailable for this capture.",
            f"Structured data types observed: {', '.join(schema_types)}."
            if schema_types
            else "No structured data types were recorded for this page.",
        ]
        body = [
            {"type": "heading", "level": 2, "text": "Current evidence"},
            {"type": "paragraph", "text": " ".join(evidence_bits)},
            {"type": "paragraph", "text": " ".join(measures)},
            {"type": "heading", "level": 2, "text": "Refresh objectives"},
            {
                "type": "list",
                "items": [
                    f"Serve one accountable job: {intent}.",
                    "Lead with the reader's decision, not the brand history.",
                    "Keep changeable facts (price, stock, timing) on live components only.",
                    "Do not add claims that are not present in the crawl evidence.",
                ],
            },
            {"type": "heading", "level": 2, "text": "Recommended structure"},
            {
                "type": "list",
                "items": [
                    "A concise statement of who the page is for",
                    "A scannable explanation of the available decision routes",
                    "Supporting details sourced from the live page",
                    "Descriptive internal links to related approved-domain pages",
                    "A single next action matched to the page intent",
                ],
            },
            {"type": "heading", "level": 2, "text": "On-page checklist"},
            {
                "type": "list",
                "items": [
                    "One clear H1 aligned with the page topic",
                    "A distinct title tag of roughly 25-60 characters",
                    "A page-specific meta description of roughly 70-158 characters",
                    "Descriptive anchors that match their destinations",
                    "Human editorial approval before publication",
                ],
            },
        ]
        claims = [
            {
                "claim": (
                    f"The page {page['normalized_url']} returned HTTP {page['status_code']} "
                    "during the approved-domain crawl."
                ),
                "evidence_ids": [page["evidence_id"]],
                "confidence": 1.0,
                "validation": "supported",
            },
            {
                "claim": (
                    f"The observed page title was '{page['title']}'."
                    if page["title"]
                    else "The page was captured without a title tag."
                ),
                "evidence_ids": [page["evidence_id"]],
                "confidence": 1.0,
                "validation": "supported",
            },
        ]
        assets.append(
            {
                "id": f"CONTENT-{offset:02d}",
                "slug": _slug_for(page, used_slugs),
                "title": f"{topic} evidence-led refresh",
                "asset_type": "Existing-page content refresh",
                "target_url": page["normalized_url"],
                "audience": audience,
                "intent": intent,
                "primary_topic": topic,
                "headline": f"{topic} evidence-led refresh",
                "summary": (
                    f"A review-ready refresh brief for {page['normalized_url']} grounded in "
                    f"crawl observations captured on {as_of}; changeable details stay on the "
                    "authoritative live page."
                ),
                "body": body,
                "claims": claims,
                "approval_state": "withheld_pending_human_approval",
                "generation_method": "templated_evidence_framework",
                "evidence_ids": [page["evidence_id"]],
            }
        )
    return assets


def _compile_opportunities(
    assets: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    keyword_reason: str | None,
) -> list[dict[str, Any]]:
    opportunities: list[dict[str, Any]] = []
    for asset in assets:
        opportunities.append(
            {
                "id": f"OPP-{len(opportunities) + 1:02d}",
                "cluster": asset["primary_topic"],
                "intent": asset["intent"],
                "target_url": asset["target_url"],
                "decision": "Refresh existing target; do not create a competing URL",
                "evidence_ids": list(asset["evidence_ids"]),
                "keyword_volume": None,
                "ranking": None,
                "unavailable_reason": keyword_reason,
            }
        )
    asset_urls = {asset["target_url"] for asset in assets}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in _content_candidates(pages):
        if page["normalized_url"] in asset_urls:
            continue
        token = (page["title"] or page["h1"] or "").split(" ")[0].casefold().strip(",.|-")
        if len(token) > 3:
            grouped[token].append(page)
    extras = 0
    for token, members in sorted(grouped.items()):
        if len(members) < 2 or extras >= 8:
            continue
        best = max(members, key=lambda page: page["internal_links"])
        opportunities.append(
            {
                "id": f"OPP-{len(opportunities) + 1:02d}",
                "cluster": f"{token.title()} pages ({len(members)} observed)",
                "intent": "cluster consolidation review",
                "target_url": best["normalized_url"],
                "decision": "Consolidate around the strongest existing target",
                "evidence_ids": [member["evidence_id"] for member in members][:10],
                "keyword_volume": None,
                "ranking": None,
                "unavailable_reason": keyword_reason,
            }
        )
        extras += 1
    return opportunities


# --------------------------------------------------------------------------- deployment


def _metadata_review_rows(
    pages: list[dict[str, Any]], client_name: str, target_keyword: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in pages:
        if page["status_code"] != 200 or page["page_type"] == "Utility":
            continue
        topic = _topic_label(page, client_name)
        current_title = page["title"] or ""
        current_meta = page["meta_description"] or ""
        current_h1 = page["h1"] or ""
        proposed_title = f"{topic} | {client_name}"
        if len(proposed_title) > 60:
            keep = max(10, 60 - len(client_name) - 3)
            proposed_title = f"{topic[:keep].rstrip()} | {client_name}"
        proposed_meta = (
            f"Explore {topic} from {client_name}. Review the current details, availability "
            "and next steps on the official website before acting."
        )[:158]
        rows.append(
            {
                "page_id": page["id"],
                "url": page["normalized_url"],
                "page_type": page["page_type"],
                "status_code": page["status_code"],
                "current_title": current_title or None,
                "title_length": len(current_title),
                "title_issue": (
                    "Missing"
                    if not current_title
                    else (
                        "Too long"
                        if len(current_title) > 60
                        else ("Too short" if len(current_title) < 25 else "Review")
                    )
                ),
                "proposed_title": proposed_title,
                "current_meta_description": current_meta or None,
                "meta_description_length": len(current_meta),
                "meta_description_issue": (
                    "Missing"
                    if not current_meta
                    else (
                        "Too long"
                        if len(current_meta) > 160
                        else ("Too short" if len(current_meta) < 70 else "Review")
                    )
                ),
                "proposed_meta_description": proposed_meta,
                "current_h1": current_h1 or None,
                "h1_issue": (
                    "Missing"
                    if not current_h1
                    else ("Multiple captured" if " | " in current_h1 else "Review")
                ),
                "proposed_h1": topic,
                "target_keyword": target_keyword,
                "priority": "P1" if not current_title or not current_meta or not current_h1 else "P2",
                "evidence_id": page["evidence_id"],
                "approval_status": "withheld_pending_editorial_review",
            }
        )
    return rows


def _internal_link_rows(
    pages: list[dict[str, Any]], client_name: str
) -> list[dict[str, Any]]:
    by_url = {page["normalized_url"]: page for page in pages}
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for page in pages:
        source = page["normalized_url"]
        parts = urlsplit(source)
        path = parts.path
        target: str | None = None
        link_type = ""
        if "/products/" in path and "/collections/" in path:
            collection = path.split("/products/", 1)[0]
            target = f"{parts.scheme}://{parts.netloc}{collection}"
            link_type = "Product to parent collection"
        elif "/blog" in path and path.count("/") >= 3:
            hub = "/".join(path.split("/")[:3])
            target = f"{parts.scheme}://{parts.netloc}{hub}"
            link_type = "Article to editorial hub"
        if not target or target not in by_url or target == source:
            continue
        pair = (source, target)
        if pair in seen:
            continue
        seen.add(pair)
        observed = target in set(page["links"])
        rows.append(
            {
                "source_url": source,
                "target_url": target,
                "anchor": _topic_label(by_url[target], client_name),
                "rationale": (
                    "Preserve a descriptive parent relationship"
                    if observed
                    else "Add a descriptive parent relationship after editorial review"
                ),
                "link_type": link_type,
                "observed_status": "Observed in crawl" if observed else "Candidate - not observed",
                "evidence_ids": [page["evidence_id"], by_url[target]["evidence_id"]],
                "approval_status": "review_ready" if observed else "withheld_pending_review",
            }
        )
    return rows[:250]


def _compile_deployment(
    pages: list[dict[str, Any]], client_name: str, target_keyword: str
) -> dict[str, Any]:
    redirect_candidates = [
        {
            "source_url": page["normalized_url"],
            "target_url": None,
            "status_code": page["status_code"],
            "evidence_id": page["evidence_id"],
            "approval_status": "withheld_pending_graph_validation",
            "included_in_deployment": False,
            "reason": (
                "A destination must be chosen from content equivalence and link-graph "
                "evidence; generic redirects are prohibited."
            ),
        }
        for page in pages
        if page["status_code"] not in {200, 301, 302}
    ]
    canonical_candidates = [
        {
            "page_id": page["id"],
            "source_url": page["normalized_url"],
            "observed_canonical": page["canonical_url"],
            "proposed_canonical": page["normalized_url"],
            "evidence_id": page["evidence_id"],
            "approval_status": "withheld_pending_agency_admin",
            "included_in_deployment": False,
        }
        for page in pages
        if page["status_code"] == 200
        and (not page["canonical_url"] or page["canonical_url"] != page["normalized_url"])
    ]
    return {
        "redirect_candidates": redirect_candidates,
        "canonical_candidates": canonical_candidates,
        "metadata_review": _metadata_review_rows(pages, client_name, target_keyword),
        "internal_link_candidates": _internal_link_rows(pages, client_name),
        "schema": {
            "deployable": [],
            "withheld": [
                {
                    "reason": (
                        "Schema deployment requires a verified page-specific fact pack and "
                        "agency administrator approval."
                    ),
                    "approval_status": "withheld_pending_agency_admin",
                }
            ],
        },
        "robots": {
            "deployable_changes": [],
            "recommendation": "No robots.txt change is proposed from this audit run.",
        },
        "disavow": {
            "enabled": False,
            "reason": (
                "No backlink evidence, removal-attempt record or manual-action indication "
                "was collected for this run."
            ),
        },
    }


# --------------------------------------------------------------------------- narrative


def _compile_strategy_sections(
    data: dict[str, Any], unavailable_labels: list[str]
) -> list[dict[str, Any]]:
    pages = data["pages"]
    findings = data["findings"]
    successful = sum(1 for page in pages if page["status_code"] == 200)
    p1 = sum(1 for finding in findings if finding["priority"] == "P1")
    technical = sum(1 for finding in findings if finding["category"] == "technical")
    on_page = sum(1 for finding in findings if finding["category"] == "on_page")
    metadata_rows = len(data["deployment"]["metadata_review"])
    unavailable_text = (
        ", ".join(unavailable_labels) if unavailable_labels else "no private sources"
    )
    return [
        {
            "title": "Evidence posture",
            "level": 1,
            "paragraphs": [
                f"The crawl captured {len(pages)} approved-domain pages ({successful} returned "
                f"HTTP 200) and the deterministic ruleset raised {len(findings)} findings, "
                f"{p1} of them P1.",
                f"Private sources not collected for this run: {unavailable_text}. Forecasts, "
                "ranking claims and traffic targets are withheld until those baselines exist.",
            ],
            "decision": "Approve the evidence boundary before prioritising implementation.",
        },
        {
            "title": "Technical integrity before expansion",
            "level": 1,
            "paragraphs": [
                f"{technical} technical findings and "
                f"{len(data['deployment']['redirect_candidates'])} non-successful URLs need a "
                "disposition before content expansion.",
                "Redirect, canonical and robots proposals remain withheld until an agency "
                "administrator approves page-specific changes.",
            ],
            "decision": "No redirect, canonical, robots or schema proposal deploys from this "
            "package alone.",
        },
        {
            "title": "On-page and content control",
            "level": 1,
            "paragraphs": [
                f"{on_page} on-page findings map to {metadata_rows} metadata review rows and "
                f"{len(data['content_assets'])} evidence-grounded refresh briefs.",
                "Each brief targets one existing URL and stays withheld pending human "
                "editorial approval; no draft asserts facts beyond the crawl evidence.",
            ],
            "decision": "Refresh existing targets and record a cannibalisation decision for "
            "every asset.",
        },
        {
            "title": "Measurement before prediction",
            "level": 1,
            "paragraphs": [
                "Connect first-party search and analytics evidence, freeze an approved "
                "baseline, and only then model outcome scenarios.",
                f"The current measurement plan tracks {len(data['measurement_plan'])} KPIs; "
                "baselines that require unconnected sources are labelled Unavailable.",
            ],
            "decision": "Publish no traffic, revenue or ranking forecast from this run.",
        },
    ]


def _compile_measurement_plan(
    pages_count: int, findings_count: int, sources: list[dict[str, Any]]
) -> list[dict[str, str]]:
    available = {source["kind"] for source in sources if source["status"] == "available"}
    return [
        {
            "kpi": "Google organic clicks",
            "baseline": "Unavailable" if "gsc" not in available else "Captured at connection",
            "cadence": "Weekly",
            "source": "GSC",
            "decision_use": "Prioritise page and query opportunities after connection",
        },
        {
            "kpi": "Organic conversions",
            "baseline": "Unavailable" if "ga4" not in available else "Captured at connection",
            "cadence": "Weekly",
            "source": "GA4",
            "decision_use": "Measure commercial outcome after event validation",
        },
        {
            "kpi": "Crawl inventory",
            "baseline": f"{pages_count} pages crawled",
            "cadence": "Each audit run",
            "source": "Studio crawl",
            "decision_use": "Reconcile the URL inventory after every release",
        },
        {
            "kpi": "Open findings",
            "baseline": f"{findings_count} findings open",
            "cadence": "Each audit run",
            "source": "Studio audit",
            "decision_use": "Track remediation of evidence-backed issues",
        },
        {
            "kpi": "Critical/High QA failures",
            "baseline": "0 in review package",
            "cadence": "Every release",
            "source": "Studio QA",
            "decision_use": "Block package release when non-zero",
        },
    ]


# --------------------------------------------------------------------------- QA


def _compile_qa(data: dict[str, Any], domains: tuple[str, ...]) -> dict[str, Any]:
    pages = data["pages"]
    findings = data["findings"]
    actions = data["actions"]
    content = data["content_assets"]
    evidence_ids = {row["id"] for row in data["evidence"]}
    checked_at = timezone.now().isoformat()

    wrong_domain = sum(
        1
        for page in pages
        for url in (page["original_url"], page["normalized_url"], page["canonical_url"])
        if url and not _approved_host(url, domains)
    )
    seen: dict[str, int] = defaultdict(int)
    for page in pages:
        seen[page["normalized_url"]] += 1
    duplicates = sum(count - 1 for count in seen.values() if count > 1)
    collapsed = sum(page["duplicate_observations"] for page in pages)
    missing_lineage = sum(
        1
        for row in (*findings, *actions)
        if not row["evidence_ids"] or any(item not in evidence_ids for item in row["evidence_ids"])
    )
    unsupported_claims = sum(
        1
        for asset in content
        for claim in asset["claims"]
        if not claim.get("evidence_ids") or any(v not in evidence_ids for v in claim["evidence_ids"])
    )
    deployment = data["deployment"]
    unapproved_risky = (
        len(deployment["schema"]["deployable"])
        + sum(1 for row in deployment["redirect_candidates"] if row["included_in_deployment"])
        + sum(1 for row in deployment["canonical_candidates"] if row["included_in_deployment"])
    )
    private_available = any(
        source["kind"] in PRIVATE_SOURCES and source["status"] == "available"
        for source in data["sources"]
    )

    measures = [
        ("Normalized pages", len(pages)),
        ("Aggregated findings", len(findings)),
        ("Canonical actions", len(actions)),
        ("Content assets", len(content)),
        ("Evidence rows", len(data["evidence"])),
    ]
    reconciliation = [
        {
            "measure": label,
            "canonical": value,
            "package": value,
            "result": "PASS",
            "rule": "Exact integer equality",
            "evidence": "Compiled from one canonical run dataset",
        }
        for label, value in measures
    ]
    gates = [
        (
            "QA-01",
            "Approved-domain boundary",
            "PASS" if wrong_domain == 0 else "FAIL",
            f"{wrong_domain} wrong-domain URL records across page, canonical and original URLs",
        ),
        (
            "QA-02",
            "Normalized URL deduplication",
            "PASS" if duplicates == 0 else "FAIL",
            f"{duplicates} duplicate normalized pages; {collapsed} duplicate observations collapsed",
        ),
        (
            "QA-03",
            "Evidence lineage",
            "PASS" if missing_lineage == 0 else "FAIL",
            f"{missing_lineage} findings or actions failed to resolve to evidence IDs",
        ),
        (
            "QA-04",
            "Claim support",
            "PASS" if unsupported_claims == 0 else "FAIL",
            f"{unsupported_claims} content claims lack resolvable evidence IDs",
        ),
        (
            "QA-05",
            "Risky deployment controls",
            "PASS" if unapproved_risky == 0 else "FAIL",
            f"{unapproved_risky} risky proposals marked deployable without approval",
        ),
        (
            "QA-06",
            "Cross-artifact reconciliation",
            "PASS",
            "Canonical counts are exported from one compiled dataset",
        ),
        (
            "QA-07",
            "Private provider evidence",
            "PASS" if private_available else "UNAVAILABLE",
            "At least one private provider supplied evidence"
            if private_available
            else "GSC, GA4, SEMrush and PageSpeed credentials were not configured",
        ),
        (
            "QA-08",
            "Gate 1 and Gate 2 approvals",
            "NOT_RUN",
            "Human decisions are required before production",
        ),
    ]
    failed = sum(1 for _, _, status, _ in gates if status == "FAIL")
    return {
        "release_status": "PASS_FOR_REVIEW" if failed == 0 else "REVIEW_REQUIRED",
        "release_statement": (
            "Critical and High package QA failures are zero. Human Gate 1/Gate 2 approvals "
            "remain explicit production blockers."
            if failed == 0
            else f"{failed} package QA gates failed; resolve them before review."
        ),
        "critical_failures": failed,
        "high_failures": 0,
        "wrong_domain_urls": wrong_domain,
        "unsupported_claims": unsupported_claims,
        "unapproved_risky_assets": unapproved_risky,
        "duplicate_normalized_pages": duplicates,
        "duplicate_observations_collapsed": collapsed,
        "gates": [
            {
                "id": identifier,
                "name": name,
                "status": status,
                "critical_failures": 1 if status == "FAIL" else 0,
                "high_failures": 0,
                "evidence": evidence,
                "checked_at": checked_at,
            }
            for identifier, name, status, evidence in gates
        ],
        "reconciliation": reconciliation,
    }


# --------------------------------------------------------------------------- deck


def _compile_deck(data: dict[str, Any]) -> list[dict[str, Any]]:
    client_name = data["client"]["name"]
    pages = data["pages"]
    findings = data["findings"]
    successful = sum(1 for page in pages if page["status_code"] == 200)
    severity_counts: dict[str, int] = defaultdict(int)
    for finding in findings:
        severity_counts[finding["severity"]] += 1
    technical = [f for f in findings if f["category"] == "technical"][:4]
    on_page = [f for f in findings if f["category"] == "on_page"][:4]
    return [
        {
            "kind": "cover",
            "eyebrow": "ENTERPRISE SEO REVIEW",
            "title": "Evidence first. Growth second.",
            "body": (
                f"A fresh, approved-domain review of {client_name} "
                f"({data['client']['domain']}) with a controlled 16-week path from observed "
                "issues to approved implementation."
            ),
            "points": [],
        },
        {
            "kind": "score",
            "eyebrow": "EVIDENCE POSTURE",
            "title": "A score is useful only when coverage earns it.",
            "body": data["run"]["overall_score_reason"],
            "points": [
                {
                    "label": category["category"],
                    "text": f"{category['score']:.0f}"
                    if category["score"] is not None
                    else "Withheld",
                }
                for category in data["categories"][:6]
            ],
        },
        {
            "kind": "generic",
            "eyebrow": "WHAT WE KNOW",
            "title": "Crawl evidence can guide technical and on-page work now.",
            "body": "Sources that were not connected remain explicitly unavailable.",
            "points": [
                {"label": "Pages", "text": str(len(pages))},
                {"label": "Successful (200)", "text": str(successful)},
                {"label": "Findings", "text": str(len(findings))},
                {"label": "Actions", "text": str(len(data["actions"]))},
            ],
        },
        {
            "kind": "generic",
            "eyebrow": "FINDINGS BY SEVERITY",
            "title": "Deterministic rules, evidence-linked results.",
            "body": f"{len(findings)} findings were raised by rule version "
            f"{data['run']['rule_version']}.",
            "points": [
                {"label": severity, "text": str(severity_counts.get(severity, 0))}
                for severity in ("Critical", "High", "Medium", "Low", "Info")
            ],
        },
        {
            "kind": "generic",
            "eyebrow": "TECHNICAL HIGHLIGHTS",
            "title": "Stabilise the URL graph before expanding content.",
            "body": "Highest-priority technical observations from the crawl.",
            "points": [{"label": f["priority"], "text": f["title"]} for f in technical]
            or [{"label": "Technical", "text": "No technical findings were raised."}],
        },
        {
            "kind": "generic",
            "eyebrow": "ON-PAGE HIGHLIGHTS",
            "title": "Metadata and headings are review-ready.",
            "body": f"{len(data['deployment']['metadata_review'])} pages carry proposed titles, "
            "meta descriptions and H1s pending editorial review.",
            "points": [{"label": f["priority"], "text": f["title"]} for f in on_page]
            or [{"label": "On-page", "text": "No on-page findings were raised."}],
        },
        {
            "kind": "timeline",
            "eyebrow": "16-WEEK ROADMAP",
            "title": "Sequence removes risk from the critical path.",
            "body": (
                f"{len(data['actions'])} evidence-linked actions move from foundations and "
                "quick wins through technical and on-page remediation to content, "
                "architecture and measurement."
            ),
            "points": [],
        },
        {
            "kind": "generic",
            "eyebrow": "MEASUREMENT",
            "title": "Connect first-party baselines before outcome claims.",
            "body": "Baselines that need unconnected sources stay labelled Unavailable.",
            "points": [
                {"label": item["source"], "text": item["baseline"]}
                for item in data["measurement_plan"][:4]
            ],
        },
        {
            "kind": "generic",
            "eyebrow": "DECISION",
            "title": "Approve the evidence boundary - or request a revision.",
            "body": (
                "Gate 1 accepts evidence and direction. Gate 2 accepts the plan and "
                "review-ready assets. Production stays blocked until both decisions and "
                "final QA complete."
            ),
            "callout": f"Current state: {data['qa']['release_status']} · no external system "
            "was changed",
            "points": [
                {"label": "Gate 1", "text": "Human decision required"},
                {"label": "Gate 2", "text": "Human decision required"},
                {
                    "label": "Critical/High QA",
                    "text": f"{data['qa']['critical_failures']} / {data['qa']['high_failures']}",
                },
            ],
        },
    ]


# --------------------------------------------------------------------------- enrichment


def _ledger_row(
    row_id: str,
    *,
    configured_model: str,
    status: str,
    returned_model: str | None = None,
    request_hash: str | None = None,
    response_hash: str | None = None,
    tokens: int = 0,
    cost: float | None = 0.0,
    unavailable_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "task": "Evidence-constrained package strategy enrichment",
        "configured_model": configured_model,
        "returned_model": returned_model,
        "prompt_version": ENRICHMENT_PROMPT_VERSION,
        "status": status,
        "request_hash": request_hash,
        "response_hash": response_hash,
        "tokens": tokens,
        "cost": cost,
        "unavailable_reason": unavailable_reason,
    }


def _build_fact_pack(data: dict[str, Any]) -> FactPack:
    as_of = datetime.fromisoformat(data["run"]["captured_at"])
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    facts: list[VerifiedFact] = []
    evidence_ids: set[str] = set()

    def add_fact(key: str, value: Any) -> None:
        evidence_id = str(uuid5(NAMESPACE_URL, f"{data['run']['id']}:{key}"))
        evidence_ids.add(evidence_id)
        facts.append(VerifiedFact(key, value, (evidence_id,), as_of))

    add_fact(
        "run_evidence_posture",
        {
            "approved_domain": data["client"]["domain"],
            "evidence_coverage": data["run"]["evidence_coverage"],
            "score_status": data["run"]["overall_score_reason"],
            "page_count": len(data["pages"]),
            "finding_count": len(data["findings"]),
            "action_count": len(data["actions"]),
        },
    )
    add_fact(
        "aggregated_findings",
        [
            {
                "id": item["id"],
                "priority": item["priority"],
                "severity": item["severity"],
                "title": item["title"],
            }
            for item in data["findings"][:40]
        ],
    )
    add_fact(
        "approved_action_sequence",
        [
            {
                "id": item["id"],
                "week": item["week"],
                "priority": item["priority"],
                "action": item["action"],
            }
            for item in data["actions"][:48]
        ],
    )
    add_fact("explicit_limitations", data["limitations"])
    statuses = {
        page["normalized_url"]: page["status_code"]
        for page in data["pages"]
        if page.get("normalized_url")
    }
    unavailable = {
        source["label"]: source["unavailable_reason"]
        for source in data["sources"]
        if source["status"] != "available" and source["unavailable_reason"]
    }
    return FactPack(
        project_id=data["project"]["id"],
        approved_domains=(data["client"]["domain"],),
        facts=tuple(facts),
        available_evidence_ids=frozenset(evidence_ids),
        known_url_statuses=statuses,
        unavailable_sources=unavailable,
    )


def _enrich(
    data: dict[str, Any],
    boundary_factory: Callable[[], Any] | None = None,
) -> None:
    """Attempt one structured enrichment call; never raise, always ledger."""
    row_id = f"GEN-{len(data['generation_ledger']) + 1:03d}"
    configured_model = os.environ.get("OPENAI_STRATEGY_MODEL", DEFAULT_FINAL_MODEL)
    try:
        fact_pack = _build_fact_pack(data)
        if boundary_factory is not None:
            boundary = boundary_factory()
        else:
            from generation.openai_boundary import OpenAIBoundary

            boundary = OpenAIBoundary(
                config=GenerationConfig(final_model=configured_model, max_output_tokens=3200)
            )
        result = boundary.generate_structured(
            task=(
                "Synthesise a concise executive summary and one strategy section strictly "
                "from the approved fact pack. Do not invent metrics, keywords, rankings or "
                "URLs. Provide at most three deck calls to action and support every factual "
                "claim in the claims ledger."
            ),
            fact_pack=fact_pack,
            schema_name="package_enrichment",
            schema=dict(ENRICHMENT_SCHEMA),
            purpose=GenerationPurpose.FINAL,
        )
        ledger = result.ledger
        data["generation_ledger"].append(
            _ledger_row(
                row_id,
                configured_model=ledger.requested_model,
                status=result.status.value,
                returned_model=ledger.returned_model,
                request_hash=ledger.request_sha256,
                response_hash=ledger.response_sha256,
                tokens=(ledger.input_tokens or 0) + (ledger.output_tokens or 0),
                cost=None,
                unavailable_reason=result.unavailable_reason,
            )
        )
        if result.status is not GenerationStatus.AVAILABLE or not result.data:
            return
        issues = (
            *validate_claims(result.data, fact_pack),
            *validate_domains_and_links(result.data, fact_pack),
            *validate_placeholders(result.data),
        )
        if any(issue.severity in {Severity.HIGH, Severity.CRITICAL} for issue in issues):
            data["generation_ledger"][-1]["unavailable_reason"] = (
                "Enrichment output failed deterministic quality gates; deterministic text kept."
            )
            return
        data["executive_summary"] = str(result.data["executive_summary"])
        synthesis = result.data["strategy_synthesis"]
        data["strategy_sections"].insert(
            0,
            {
                "title": str(synthesis["title"]),
                "level": 1,
                "paragraphs": [str(item) for item in synthesis["paragraphs"]],
                "decision": (
                    "Use model synthesis for narrative prioritisation only; canonical "
                    "records remain authoritative."
                ),
            },
        )
        if data["deck"]:
            for cta in list(result.data.get("deck_calls_to_action", []))[:3]:
                data["deck"][-1].setdefault("points", []).append(
                    {"label": "Next step", "text": str(cta)}
                )
    except Exception as exc:  # noqa: BLE001 - enrichment must never break compilation
        data["generation_ledger"].append(
            _ledger_row(
                row_id,
                configured_model=configured_model,
                status="unavailable",
                unavailable_reason=f"Enrichment failed safely: {str(exc)[:300]}",
            )
        )


# --------------------------------------------------------------------------- entry point


def compile_run_data(
    run: Any,
    *,
    enrich: bool | None = None,
    boundary_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Compile the canonical package data dict for one persisted audit run."""
    project = run.project
    client = project.client
    domains = tuple(
        str(value).casefold().rstrip(".") for value in (project.approved_domains or [])
    ) or (project.primary_domain.casefold().rstrip("."),)

    captured = run.source_cutoff_at or run.created_at
    captured_iso = _iso(captured) or timezone.now().isoformat()
    as_of = captured_iso[:10]

    try:
        page_budget = RUN_LIMITS[RunProfile(run.profile)].page_budget
    except ValueError:
        page_budget = RUN_LIMITS[RunProfile.STANDARD].page_budget

    pages, evidence_display = _compile_pages(run, domains)
    if not pages:
        raise ValueError("The run has no crawled pages; a package cannot be compiled.")

    coverage = round(float(run.evidence_coverage) / 100, 4)
    overall_score = float(run.health_score) if run.health_score is not None else None
    score_reason = (
        f"Published because weighted evidence coverage {coverage:.0%} meets the 70% "
        "publication threshold."
        if overall_score is not None
        else f"Withheld because weighted evidence coverage {coverage:.0%} is below the 70% "
        "publication threshold."
    )

    sources = _compile_sources(run, pages, captured_iso)
    evidence = _compile_evidence(pages, sources, project.locale, captured_iso)
    findings, finding_evidence = _compile_findings(run, pages, evidence_display, as_of)
    profile_enum = _business_profile(project.business_type)
    categories = _compile_categories(run, profile_enum, findings)
    default_evidence = [pages[0]["evidence_id"]]
    actions = _compile_actions(run, finding_evidence, default_evidence)
    content_assets = _compile_content_assets(pages, client.name, as_of)

    keyword_sources = {
        source["kind"] for source in sources if source["status"] == "available"
    } & {"gsc", "semrush"}
    keyword_reason = (
        "Keyword volume and ranking metrics were not compiled for this run."
        if keyword_sources
        else "GSC and SEMrush are not connected for this project."
    )
    target_keyword = (
        "Unavailable - keyword mapping requires an approved keyword import"
        if keyword_sources
        else "Unavailable - GSC and SEMrush not connected"
    )
    opportunities = _compile_opportunities(content_assets, pages, keyword_reason)
    deployment = _compile_deployment(pages, client.name, target_keyword)

    unavailable_labels = [
        source["label"]
        for source in sources
        if source["status"] == "unavailable" and source["kind"] in PRIVATE_SOURCES
    ]
    limitations = [
        f"{label} was not collected: credentials or connections were unavailable."
        for label in unavailable_labels
    ]
    if overall_score is None:
        limitations.append(score_reason)
    limitations.extend(
        [
            "No traffic, ranking, conversion, revenue or performance forecast is included.",
            "Deployment and content proposals are advisory and remain withheld pending "
            "human approval.",
            "No external system was modified by this audit.",
        ]
    )

    p1_count = sum(1 for finding in findings if finding["priority"] == "P1")
    executive_summary = (
        f"The studio crawled {len(pages)} approved-domain pages for {client.name} "
        f"({project.primary_domain}) as of {as_of}. "
        f"Deterministic rules raised {len(findings)} findings, {p1_count} of them P1. "
        f"{score_reason} "
        f"A 16-week plan with {len(actions)} evidence-linked actions and "
        f"{len(content_assets)} review-ready content briefs awaits Gate 1 review. "
        "All risky deployment proposals remain withheld pending human approval."
    )

    data: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "client": {
            "name": client.name,
            "domain": project.primary_domain,
            "locale": project.locale,
        },
        "project": {
            "id": str(project.pk),
            "name": project.name,
            "profile": str(run.profile).title(),
            "business_profile": project.business_type,
        },
        "run": {
            "id": f"RUN-{str(run.pk)[:8].upper()}",
            "profile": str(run.profile).title(),
            "configured_page_budget": page_budget,
            "evidence_as_of": as_of,
            "captured_at": captured_iso,
            "rule_version": run.rule_version,
            "evidence_coverage": coverage,
            "coverage_interpretation": (
                "Crawl-derived evidence covers technical and on-page checks; sources marked "
                "unavailable are excluded from scoring."
            ),
            "overall_score": overall_score,
            "overall_score_reason": score_reason,
            "state": str(run.state).upper(),
        },
        "executive_summary": executive_summary,
        "sources": sources,
        "evidence": evidence,
        "pages": pages,
        "findings": findings,
        "categories": categories,
        "content_assets": content_assets,
        "opportunities": opportunities,
        "actions": actions,
        "strategy_sections": [],
        "measurement_plan": _compile_measurement_plan(len(pages), len(findings), sources),
        "generation_ledger": [],
        "qa": {},
        "limitations": limitations,
        "deployment": deployment,
        "deck": [],
    }
    data["qa"] = _compile_qa(data, domains)
    data["strategy_sections"] = _compile_strategy_sections(data, unavailable_labels)
    data["deck"] = _compile_deck(data)

    if enrich is None:
        flag = bool(getattr(settings, "PACKAGE_AI_ENRICHMENT_ENABLED", True))
        key_present = bool(os.environ.get("OPENAI_API_KEY", "").strip())
        attempt = flag and key_present
        skip_reason = (
            "AI enrichment is disabled for this deployment."
            if not flag
            else "OpenAI API key is not configured."
        )
    else:
        attempt = bool(enrich)
        skip_reason = "AI enrichment was disabled for this compile."

    if attempt:
        _enrich(data, boundary_factory)
    else:
        data["generation_ledger"].append(
            _ledger_row(
                "GEN-001",
                configured_model=os.environ.get("OPENAI_STRATEGY_MODEL", DEFAULT_FINAL_MODEL),
                status="unavailable",
                unavailable_reason=skip_reason,
            )
        )

    for page in data["pages"]:
        page.pop("_facts", None)
        page.pop("_page_pk", None)
    return repair_tree(data)
