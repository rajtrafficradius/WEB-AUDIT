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
from exporters import paths as tree
from generation.openai_boundary import (
    DEFAULT_FINAL_MODEL,
    GenerationConfig,
    GenerationPurpose,
    GenerationStatus,
)
from generation.package_prompts import (
    CONTENT_OUTLINE_TASK,
    H1_MAX_CHARS,
    META_MAX_CHARS,
    META_MIN_CHARS,
    ONPAGE_PROPOSAL_TASK,
    PROMPT_VERSION_ONPAGE,
    PROMPT_VERSION_OUTLINES,
    TITLE_MAX_CHARS,
    content_outline_schema,
    onpage_proposal_schema,
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
    # Replacement characters come from lossy charset decodes during the crawl;
    # they cannot be repaired and would fail the package mojibake gate.
    if "�" in repaired:
        repaired = " ".join(repaired.replace("�", " ").split())
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


CONTENT_ASSET_CAP = 20

DELIVERABLE_BY_RULE_PREFIX = (
    ("on_page.title", f"Rewritten titles deployed per {tree.TITLE_TAG_XLSX}"),
    ("on_page.meta_description", f"Rewritten descriptions deployed per {tree.META_DESCRIPTION_XLSX}"),
    ("on_page.h1", f"Corrected headings deployed per {tree.H1_TAGS_XLSX}"),
    ("on_page.thin_content", f"Expanded copy drafted from the briefs in {tree.SEO_CONTENT}/"),
    ("on_page.image_alt", f"Descriptive alt text added per {tree.TECHNICAL_AUDIT_XLSX} (Image Alt Issues)"),
    ("technical.canonical", f"Approved canonical tags applied per {tree.CANONICAL_FIXES_XLSX}"),
    ("technical.redirect", f"Approved redirects deployed per {tree.REDIRECT_MAP_CSV}"),
    ("technical.http_status", f"Broken URLs repaired or redirected per {tree.REDIRECT_MAP_CSV}"),
    ("technical.broken_internal_link", f"Broken links corrected per {tree.TECHNICAL_AUDIT_XLSX} (Findings Register)"),
    ("technical.duplicate_content", f"Duplicate URLs consolidated per {tree.CONTENT_AUDIT_XLSX} (Duplicate Content)"),
    ("technical.orphan_page", f"Orphan pages linked per {tree.INTERNAL_LINK_MAP_XLSX}"),
    ("keyword_architecture", f"Targeting realigned per {tree.MASTER_KEYWORD_UNIVERSE_XLSX}"),
    ("analytics", f"Tracking rollout verified against {tree.TRACKING_AUDIT_XLSX}"),
    ("performance", f"Server/response fixes verified against {tree.BASELINE_PERFORMANCE_XLSX}"),
    ("geo_aeo", f"Structured data added per {tree.SCHEMA_MARKUP}/ templates after approval"),
    ("ecommerce.product_schema", f"Product schema deployed per {tree.SCHEMA_PRODUCT_TEMPLATE_JSON} after approval"),
    ("local", f"LocalBusiness schema deployed per {tree.SCHEMA_MARKUP}/ templates after approval"),
)


def _deliverable_for_rule(rule_code: str) -> str:
    code = str(rule_code or "")
    for prefix, deliverable in DELIVERABLE_BY_RULE_PREFIX:
        if code.startswith(prefix):
            return deliverable
    return "Change deployed on the approved domain and verified in the next crawl"


# --------------------------------------------------------------------------- provider data

PROVIDER_SOURCE_TYPES = ("semrush",)
PROVIDER_MISSING_REASON = "SEMrush API key is not configured for this project"
COMPETITOR_MISSING_REASON = (
    "No competitor rows were returned by a configured market-data provider for this run."
)
BACKLINK_MISSING_REASON = (
    "No backlink rows were returned by a configured market-data provider for this run."
)

MARKET_FIELDS: tuple[str, ...] = (
    "organic_keywords",
    "organic_traffic",
    "organic_cost",
    "adwords_keywords",
    "rank",
    "authority_score",
    "backlinks_total",
    "referring_domains",
    "referring_ips",
    "follow_links",
    "nofollow_links",
)
MARKET_FLOAT_FIELDS = frozenset({"organic_cost"})
MARKET_METRIC_ALIASES = {
    "organic_keywords": "organic_keywords",
    "organic": "organic_traffic",
    "organic_traffic": "organic_traffic",
    "organic_cost": "organic_cost",
    "adwords_keywords": "adwords_keywords",
    "rank": "rank",
    "domain_rank": "rank",
    "authority_score": "authority_score",
    "ascore": "authority_score",
    "backlinks": "backlinks_total",
    "backlinks_total": "backlinks_total",
    "domains_num": "referring_domains",
    "referring_domains": "referring_domains",
    "ips_num": "referring_ips",
    "referring_ips": "referring_ips",
    "follows_num": "follow_links",
    "follow_links": "follow_links",
    "nofollows_num": "nofollow_links",
    "nofollow_links": "nofollow_links",
}
UNITS_METRIC_KEYS = frozenset({"units_spent", "api_units", "api_units_spent"})

STOPWORDS = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "this",
        "to",
        "with",
        "you",
        "your",
    }
)
QUESTION_MARKERS = ("how", "what", "why", "when", "who", "which", "where", "guide", "ideas", "tips")
COMPARISON_MARKERS = ("vs", "versus", "compare", "comparison", "review", "reviews", "alternatives", "types", "best")
TRANSACTIONAL_MARKERS = (
    "buy",
    "price",
    "prices",
    "pricing",
    "cost",
    "cheap",
    "quote",
    "book",
    "hire",
    "order",
    "shop",
    "for sale",
    "near me",
    "discount",
    "delivery",
)

KEYWORD_METHODOLOGY = (
    "Funnel stage is a derived label, not a provider metric. A phrase containing a "
    "transactional marker (buy, price, cost, quote, book, hire, order, shop, for sale, "
    "near me, discount, delivery) is labelled BOFU; a phrase containing a comparison marker "
    "(vs, compare, review, alternatives, types, best) is labelled MOFU; a phrase opening with "
    "a question or guidance marker (how, what, why, when, who, which, where, guide, ideas, "
    "tips) is labelled TOFU; a phrase containing the approved brand token is labelled BOFU as "
    "navigational demand. Phrases with no signal are left unlabelled rather than guessed."
)
CLUSTER_METHODOLOGY = (
    "Clusters are formed deterministically: keywords are ordered by search volume descending "
    "then alphabetically, and each keyword joins the first existing cluster whose seed tokens "
    "share a Jaccard overlap of at least 0.34 with its own non-stopword tokens, otherwise it "
    "seeds a new cluster. Each cluster is mapped to the crawled URL with the highest combined "
    "score of path-token overlap (weight 0.6) and title-token overlap (weight 0.4); a cluster "
    "with no positive-scoring URL is labelled a gap."
)
REDIRECT_METHODOLOGY = (
    "Redirect destinations are proposed by scoring every successful crawled URL against the "
    "failing URL: path-token Jaccard (weight 0.55), title/topic-token Jaccard (weight 0.35) "
    "and a parent-collection bonus (0.10) when the candidate is an ancestor path. Proposals "
    "below a 0.35 confidence floor are recorded as 'no confident match' instead of being "
    "pointed at a catch-all page."
)


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _as_int(value: Any) -> int | None:
    number = _num(value)
    return int(round(number)) if number is not None else None


def _as_float(value: Any, digits: int = 2) -> float | None:
    number = _num(value)
    return round(number, digits) if number is not None else None


def _tokens(value: str | None) -> list[str]:
    if not value:
        return []
    return [
        token
        for token in re.split(r"[^a-z0-9]+", str(value).casefold())
        if token and token not in STOPWORDS and len(token) > 1
    ]


def _token_set(value: str | None) -> set[str]:
    return set(_tokens(value))


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _metric_field(metric_key: str) -> str | None:
    key = str(metric_key or "").casefold()
    if key in MARKET_METRIC_ALIASES:
        return MARKET_METRIC_ALIASES[key]
    tail = key.rsplit(".", 1)[-1]
    return MARKET_METRIC_ALIASES.get(tail)


def _provider_snapshot(run: Any) -> Any | None:
    for snapshot in run.source_snapshots.all():
        if snapshot.source_type in PROVIDER_SOURCE_TYPES:
            return snapshot
    return None


def _provider_reason(run: Any, snapshot: Any | None) -> str:
    if snapshot is not None and snapshot.availability != "available":
        return str(snapshot.unavailable_reason or PROVIDER_MISSING_REASON)
    for connection in run.project.connections.all():
        if connection.provider in PROVIDER_SOURCE_TYPES and connection.availability != "available":
            return str(connection.unavailable_reason or PROVIDER_MISSING_REASON)
    return PROVIDER_MISSING_REASON


def _compile_market(run: Any, metrics: list[Any], locale: str) -> dict[str, Any]:
    """Build the market contract block from persisted provider metric rows."""
    snapshot = _provider_snapshot(run)
    domain_values: dict[str, Any] = dict.fromkeys(MARKET_FIELDS)
    units_spent = 0
    for row in metrics:
        field = _metric_field(row.metric_key)
        raw = row.numeric_value if row.numeric_value is not None else row.json_value
        if field is not None and domain_values.get(field) is None:
            domain_values[field] = (
                _as_float(raw) if field in MARKET_FLOAT_FIELDS else _as_int(raw)
            )
        tail = str(row.metric_key or "").casefold().rsplit(".", 1)[-1]
        if tail in UNITS_METRIC_KEYS:
            units_spent += _as_int(raw) or 0
    metadata = (snapshot.metadata or {}) if snapshot is not None else {}
    if not units_spent:
        units_spent = _as_int(metadata.get("units_spent")) or 0
    available = snapshot is not None and snapshot.availability == "available"
    has_values = any(value is not None for value in domain_values.values())
    status = "available" if available and has_values else "unavailable"
    reason = None
    if status == "unavailable":
        reason = (
            "The market-data provider returned no domain overview rows for this run."
            if available
            else _provider_reason(run, snapshot)
        )
    database = str(metadata.get("database") or "").casefold() or (locale.split("-")[-1].casefold())
    return {
        "status": status,
        "provider": PROVIDER_SOURCE_TYPES[0] if snapshot is not None else None,
        "database": database,
        "unavailable_reason": reason,
        "fetched_at": _iso(snapshot.captured_at) if snapshot is not None else None,
        "units_spent": units_spent,
        "domain": domain_values,
    }


def _keyword_extras(metrics: list[Any]) -> dict[str, dict[str, Any]]:
    """Per-phrase provider detail that has no dedicated Keyword column."""
    extras: dict[str, dict[str, Any]] = {}
    for row in metrics:
        payload = row.json_value
        if not isinstance(payload, dict):
            continue
        phrase = payload.get("phrase") or payload.get("keyword")
        if not isinstance(phrase, str) or not phrase.strip():
            continue
        extras.setdefault(_normalize_phrase(phrase), {}).update(payload)
    return extras


def _normalize_phrase(value: str) -> str:
    return " ".join(str(value).casefold().split())


def _funnel_stage(phrase: str, brand_tokens: set[str]) -> str | None:
    folded = f" {_normalize_phrase(phrase)} "
    if any(f" {marker} " in folded or marker in folded for marker in TRANSACTIONAL_MARKERS):
        return "BOFU"
    tokens = _tokens(phrase)
    if any(marker in tokens for marker in COMPARISON_MARKERS):
        return "MOFU"
    if tokens and tokens[0] in QUESTION_MARKERS:
        return "TOFU"
    if any(marker in tokens for marker in QUESTION_MARKERS):
        return "TOFU"
    if brand_tokens and brand_tokens & set(tokens):
        return "BOFU"
    return None


def _derived_intent(phrase: str, stage: str | None, brand_tokens: set[str]) -> str | None:
    if brand_tokens and brand_tokens & _token_set(phrase):
        return "navigational"
    if stage == "BOFU":
        return "transactional"
    if stage == "MOFU":
        return "commercial"
    if stage == "TOFU":
        return "informational"
    return None


def _keyword_opportunity(position: int | None, volume: int | None) -> str:
    if position is None:
        return "No tracked position; treat as a coverage gap and confirm the target URL first."
    if position <= 3:
        return f"Ranking at position {position}; defend and monitor."
    if position <= 10:
        return f"Ranking at position {position}; on-page and internal-link work can lift it."
    if position <= 20:
        return f"Ranking at position {position}; strongest page-one candidate for this run."
    if volume is None:
        return f"Ranking at position {position}; requires content depth before it competes."
    return f"Ranking at position {position} with {volume} monthly searches; long-tail depth required."


def _compile_keywords(
    run: Any,
    keyword_rows: list[Any],
    metrics: list[Any],
    pages: list[dict[str, Any]],
    brand_tokens: set[str],
    reason: str,
) -> list[dict[str, Any]]:
    """Emit contract keyword rows from persisted provider rows only."""
    if not keyword_rows:
        return []
    extras = _keyword_extras(metrics)
    page_by_url = {page["normalized_url"]: page for page in pages}
    ordered = sorted(
        keyword_rows,
        key=lambda row: (-(row.search_volume or 0), _normalize_phrase(row.phrase)),
    )
    rows: list[dict[str, Any]] = []
    for index, record in enumerate(ordered, start=1):
        phrase = repair_text(record.phrase) or record.phrase
        normalized = record.normalized_phrase or _normalize_phrase(phrase)
        detail = extras.get(_normalize_phrase(normalized), {})
        position = _as_int(record.position)
        volume = _as_int(record.search_volume)
        landing = detail.get("landing_url") or detail.get("url")
        landing_url = landing if isinstance(landing, str) and landing in page_by_url else None
        stage = _funnel_stage(phrase, brand_tokens)
        intent = (record.intent or "").strip().casefold() or _derived_intent(
            phrase, stage, brand_tokens
        )
        snapshot = record.source_snapshot
        rows.append(
            {
                "id": f"KW-{index:04d}",
                "phrase": phrase,
                "position": position,
                "previous_position": _as_int(detail.get("previous_position")),
                "search_volume": volume,
                "cpc": _as_float(record.cpc, 4),
                "competition": _as_float(detail.get("competition"), 4),
                "results_count": _as_int(detail.get("results_count") or detail.get("results")),
                "traffic_share": _as_float(detail.get("traffic_share"), 4),
                "traffic_cost_share": _as_float(detail.get("traffic_cost_share"), 4),
                "trend": str(detail["trend"]) if isinstance(detail.get("trend"), str) else None,
                "landing_url": landing_url,
                "intent": intent,
                "funnel_stage": stage,
                "cluster": None,
                "page_type": _page_type(landing_url) if landing_url else None,
                "opportunity": _keyword_opportunity(position, volume),
                "evidence_ids": [f"EV-KW-{index:04d}"],
                "source": (snapshot.source_type if snapshot is not None else "semrush"),
                "unavailable_reason": None if volume is not None else reason,
            }
        )
    return rows


def _best_page_for_tokens(
    tokens: set[str], pages: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, float]:
    best: dict[str, Any] | None = None
    best_score = 0.0
    for page in pages:
        if page["status_code"] != 200 or page["page_type"] == "Utility":
            continue
        path_score = _jaccard(tokens, _token_set(urlsplit(page["normalized_url"]).path))
        title_score = _jaccard(tokens, _token_set(page["title"] or page["h1"]))
        score = round(path_score * 0.6 + title_score * 0.4, 4)
        if score > best_score:
            best, best_score = page, score
    return best, best_score


def _compile_keyword_clusters(
    keywords: list[dict[str, Any]], pages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not keywords:
        return []
    seeds: list[dict[str, Any]] = []
    for keyword in keywords:
        tokens = _token_set(keyword["phrase"])
        if not tokens:
            tokens = {_normalize_phrase(keyword["phrase"])}
        placed = False
        for seed in seeds:
            if _jaccard(tokens, seed["tokens"]) >= 0.34:
                seed["members"].append(keyword)
                seed["tokens"] |= tokens
                placed = True
                break
        if not placed:
            seeds.append({"tokens": set(tokens), "members": [keyword], "seed": keyword})
    clusters: list[dict[str, Any]] = []
    for index, seed in enumerate(seeds, start=1):
        members = seed["members"]
        cluster_id = f"CL-{index:02d}"
        page, score = _best_page_for_tokens(seed["tokens"], pages)
        volumes = [item["search_volume"] for item in members if item["search_volume"] is not None]
        positions = [item["position"] for item in members if item["position"] is not None]
        if page is None or score <= 0:
            coverage = "gap"
        elif positions and min(positions) <= 20:
            coverage = "covered"
        else:
            coverage = "partial"
        intents = [item["intent"] for item in members if item["intent"]]
        evidence = [eid for item in members for eid in item["evidence_ids"]][:20]
        if page is not None and score > 0:
            evidence.append(page["evidence_id"])
        for item in members:
            item["cluster"] = cluster_id
        clusters.append(
            {
                "id": cluster_id,
                "name": str(seed["seed"]["phrase"])[:120],
                "keyword_count": len(members),
                "total_volume": sum(volumes) if volumes else None,
                "primary_url": page["normalized_url"] if page is not None and score > 0 else None,
                "intent": intents[0] if intents else "unclassified",
                "coverage": coverage,
                "evidence_ids": sorted(set(evidence)),
                "match_score": score,
            }
        )
    return clusters


def _competitor_payloads(metrics: list[Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for row in metrics:
        if "competitor" not in str(row.metric_key or "").casefold():
            continue
        value = row.json_value
        if isinstance(value, dict):
            payloads.append(value)
        elif isinstance(value, list):
            payloads.extend(item for item in value if isinstance(item, dict))
    return payloads


def _compile_competitors(metrics: list[Any], reason: str) -> list[dict[str, Any]]:
    payloads = _competitor_payloads(metrics)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in payloads:
        raw_domain = payload.get("domain") or payload.get("competitor_domain") or payload.get("competitor")
        if not isinstance(raw_domain, str) or not raw_domain.strip():
            continue
        domain = raw_domain.strip().casefold().rstrip(".")
        if domain in seen:
            continue
        seen.add(domain)
        index = len(rows) + 1
        rows.append(
            {
                "id": f"CMP-{index}",
                "domain": domain,
                "relevance": _as_float(payload.get("relevance"), 4),
                "common_keywords": _as_int(payload.get("common_keywords") or payload.get("common")),
                "organic_keywords": _as_int(payload.get("organic_keywords")),
                "organic_traffic": _as_int(payload.get("organic_traffic") or payload.get("organic")),
                "organic_cost": _as_float(payload.get("organic_cost")),
                "adwords_keywords": _as_int(payload.get("adwords_keywords")),
                "gap_keywords": _as_int(payload.get("gap_keywords")),
                "evidence_ids": [f"EV-CMP-{index}"],
                "unavailable_reason": None,
            }
        )
    if not rows:
        return []
    for row in rows:
        if all(row[field] is None for field in ("organic_keywords", "organic_traffic", "common_keywords")):
            row["unavailable_reason"] = reason
    return rows


def _compile_backlinks(
    run: Any, backlink_rows: list[Any], market: dict[str, Any], reason: str
) -> dict[str, Any]:
    overview = {field: market["domain"].get(field) for field in MARKET_FIELDS}
    if not backlink_rows:
        return {
            "status": "unavailable",
            "unavailable_reason": reason,
            "overview": overview,
            "referring_domains": [],
        }
    grouped: dict[str, dict[str, Any]] = {}
    for record in backlink_rows:
        domain = str(record.referring_domain or "").casefold().rstrip(".")
        if not domain:
            continue
        entry = grouped.setdefault(
            domain,
            {
                "domain": domain,
                "authority_score": None,
                "backlinks": 0,
                "country": None,
                "first_seen": None,
                "last_seen": None,
            },
        )
        entry["backlinks"] += 1
        score = _as_int(record.authority_score)
        if score is not None and (entry["authority_score"] is None or score > entry["authority_score"]):
            entry["authority_score"] = score
        if record.first_seen is not None:
            value = record.first_seen.isoformat()
            entry["first_seen"] = min(entry["first_seen"] or value, value)
        if record.last_seen is not None:
            value = record.last_seen.isoformat()
            entry["last_seen"] = max(entry["last_seen"] or value, value)
    rows = sorted(
        grouped.values(),
        key=lambda item: (-(item["backlinks"] or 0), item["domain"]),
    )
    for index, row in enumerate(rows, start=1):
        row["evidence_id"] = f"EV-BL-{index:04d}"
    if overview["referring_domains"] is None:
        overview["referring_domains"] = len(rows)
    if overview["backlinks_total"] is None:
        overview["backlinks_total"] = sum(row["backlinks"] for row in rows)
    return {
        "status": "available",
        "unavailable_reason": None,
        "overview": overview,
        "referring_domains": rows,
    }


PERFORMANCE_METRICS = (
    ("organic_keywords", "Organic keywords"),
    ("organic_traffic", "Estimated organic traffic"),
    ("authority_score", "Authority score"),
    ("referring_domains", "Referring domains"),
    ("common_keywords", "Common keywords with the client"),
)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 2)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 2)


def _compile_performance_vs_competitors(
    market: dict[str, Any], competitors: list[dict[str, Any]], reason: str
) -> dict[str, Any]:
    if not competitors:
        return {
            "status": "unavailable",
            "unavailable_reason": reason,
            "metrics": [],
            "summary": (
                "Competitor benchmarking is withheld: no competitor rows were collected for "
                "this run, so no comparison can be measured."
            ),
        }
    metrics: list[dict[str, Any]] = []
    behind = 0
    ahead = 0
    for field, label in PERFORMANCE_METRICS:
        client_value = (
            None if field == "common_keywords" else _num(market["domain"].get(field))
        )
        values = [
            _num(row.get(field))
            for row in competitors
            if _num(row.get(field)) is not None
        ]
        values = [value for value in values if value is not None]
        median = _median(values)
        best_row = None
        best_value = None
        for row in competitors:
            value = _num(row.get(field))
            if value is not None and (best_value is None or value > best_value):
                best_row, best_value = row, value
        if client_value is None or median is None:
            position = "unknown"
            note = (
                "Comparison unavailable: the metric was not returned for the client domain "
                "or for any competitor."
            )
        elif client_value > median * 1.05:
            position = "ahead"
            ahead += 1
            note = f"Client value exceeds the competitor median of {median}."
        elif client_value < median * 0.95:
            position = "behind"
            behind += 1
            note = f"Client value trails the competitor median of {median}."
        else:
            position = "level"
            note = f"Client value is within 5% of the competitor median of {median}."
        metrics.append(
            {
                "metric": label,
                "client": round(client_value, 2) if client_value is not None else None,
                "competitor_median": median,
                "best_competitor": best_row["domain"] if best_row is not None else None,
                "best_value": round(best_value, 2) if best_value is not None else None,
                "position": position,
                "note": note,
            }
        )
    measured = ahead + behind
    summary = (
        f"{len(competitors)} competitor domains were returned by the market-data provider. "
        f"Of the {len(PERFORMANCE_METRICS)} benchmark metrics, {ahead} place the client ahead "
        f"of the competitor median and {behind} place the client behind; the remainder could "
        "not be measured and stay unavailable. "
        + (
            "Treat the measured gaps as the priority order for authority and coverage work."
            if measured
            else "No metric could be compared, so no priority order is claimed."
        )
    )
    return {
        "status": "available",
        "unavailable_reason": None,
        "metrics": metrics,
        "summary": summary,
    }


def _provider_evidence(
    keywords: list[dict[str, Any]],
    competitors: list[dict[str, Any]],
    backlinks: dict[str, Any],
    market: dict[str, Any],
    sources: list[dict[str, Any]],
    locale: str,
    captured_iso: str,
) -> list[dict[str, Any]]:
    """Evidence rows for every provider-derived row family (QA-03 lineage)."""
    source_id = next(
        (source["id"] for source in sources if source["kind"] in PROVIDER_SOURCE_TYPES),
        "SRC-SEMRUSH",
    )
    captured = market.get("fetched_at") or captured_iso
    rows: list[dict[str, Any]] = []
    for keyword in keywords:
        rows.append(
            {
                "id": keyword["evidence_ids"][0],
                "source_id": source_id,
                "evidence_type": "keyword_metric",
                "observed_value": (
                    f"phrase={keyword['phrase']}; position="
                    f"{keyword['position'] if keyword['position'] is not None else 'unavailable'}; "
                    f"search_volume="
                    f"{keyword['search_volume'] if keyword['search_volume'] is not None else 'unavailable'}; "
                    f"cpc={keyword['cpc'] if keyword['cpc'] is not None else 'unavailable'}"
                ),
                "original_url": keyword["landing_url"],
                "normalized_url": keyword["landing_url"],
                "captured_at": captured,
                "locale": locale,
                "scope": f"{market.get('provider') or 'provider'} organic keyword row",
                "confidence": 1.0,
                "unavailable_reason": keyword["unavailable_reason"],
            }
        )
    for competitor in competitors:
        rows.append(
            {
                "id": competitor["evidence_ids"][0],
                "source_id": source_id,
                "evidence_type": "competitor_metric",
                "observed_value": (
                    f"domain={competitor['domain']}; common_keywords="
                    f"{competitor['common_keywords']}; organic_keywords="
                    f"{competitor['organic_keywords']}; organic_traffic="
                    f"{competitor['organic_traffic']}"
                ),
                "original_url": None,
                "normalized_url": None,
                "captured_at": captured,
                "locale": locale,
                "scope": "provider competitor row",
                "confidence": 1.0,
                "unavailable_reason": competitor["unavailable_reason"],
            }
        )
    for row in backlinks["referring_domains"]:
        rows.append(
            {
                "id": row["evidence_id"],
                "source_id": source_id,
                "evidence_type": "backlink_domain",
                "observed_value": (
                    f"referring_domain={row['domain']}; backlinks={row['backlinks']}; "
                    f"authority_score={row['authority_score']}"
                ),
                "original_url": None,
                "normalized_url": None,
                "captured_at": captured,
                "locale": locale,
                "scope": "provider referring-domain row",
                "confidence": 1.0,
                "unavailable_reason": None,
            }
        )
    if market["status"] == "available":
        rows.append(
            {
                "id": "EV-MARKET-0001",
                "source_id": source_id,
                "evidence_type": "domain_overview",
                "observed_value": "; ".join(
                    f"{field}={market['domain'][field]}"
                    for field in MARKET_FIELDS
                    if market["domain"][field] is not None
                ),
                "original_url": None,
                "normalized_url": None,
                "captured_at": captured,
                "locale": locale,
                "scope": "provider domain overview",
                "confidence": 1.0,
                "unavailable_reason": None,
            }
        )
    return rows


# --------------------------------------------------------------------------- crawl integrity


def _compile_crawl_integrity(run: Any, pages: list[dict[str, Any]]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for snapshot in run.source_snapshots.all():
        if snapshot.source_type == "crawl":
            metadata = snapshot.metadata or {}
            break
    fetched = len(pages)
    challenged = _as_int(
        metadata.get("challenged_pages")
        if metadata.get("challenged_pages") is not None
        else metadata.get("challenged")
    ) or 0
    rate_limited = _as_int(
        metadata.get("rate_limited_pages")
        if metadata.get("rate_limited_pages") is not None
        else metadata.get("rate_limited")
    ) or 0
    quarantined_raw = metadata.get("quarantined_urls") or metadata.get("quarantined") or []
    quarantined = [str(value) for value in quarantined_raw if isinstance(value, str)][:100]
    share = round(challenged / fetched, 4) if fetched else 0.0
    if share >= 0.5 or (fetched == 0 and challenged):
        status = "blocked"
    elif challenged or rate_limited or quarantined:
        status = "degraded"
    else:
        status = "clean"
    note = {
        "clean": "No bot challenge, rate limit or quarantine was recorded during the crawl.",
        "degraded": (
            f"{challenged} challenged and {rate_limited} rate-limited responses were recorded; "
            f"{len(quarantined)} URLs were quarantined. Coverage-sensitive findings should be "
            "read with that in mind."
        ),
        "blocked": (
            f"{challenged} of {fetched} fetches were challenged. Coverage is not "
            "representative and category scores dependent on it stay withheld."
        ),
    }[status]
    return {
        "status": status,
        "fetched_pages": fetched,
        "challenged_pages": challenged,
        "challenge_share": share,
        "rate_limited_pages": rate_limited,
        "quarantined_urls": quarantined,
        "note": note,
    }


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


BRIEF_MIN_WORDS = 1200
BRIEF_MAX_WORDS = 1500
BRIEF_SENTENCE_OVERLAP_LIMIT = 0.15

OUTLINE_BY_TYPE = {
    "Homepage": (
        "Who this business serves",
        "What can be done from this page",
        "How the main routes are organised",
        "Proof that is already published on site",
        "Where to go next",
    ),
    "Collection": (
        "What this collection contains",
        "How to choose between the options",
        "Attributes shoppers compare",
        "Questions this collection should answer",
        "Where to go next",
    ),
    "Product": (
        "What this item is",
        "Who it suits and why",
        "Specifications observed on the page",
        "Questions buyers ask before ordering",
        "Where to go next",
    ),
    "Editorial": (
        "The question this article answers",
        "Background a reader needs first",
        "Step-by-step guidance",
        "Common mistakes and corrections",
        "Where to go next",
    ),
    "Information": (
        "What this page confirms about the business",
        "Details a visitor is checking",
        "How to make contact",
        "Related pages that carry the same trust signals",
        "Where to go next",
    ),
    "Other": (
        "What this page is for",
        "What a visitor should understand first",
        "Supporting detail already on site",
        "Questions this page should answer",
        "Where to go next",
    ),
}

PAD_TEMPLATES = (
    "Acceptance for '{section}' on {path}: an editor confirms the section answers the stated "
    "job for {topic} without adding a fact that is absent from the {as_of} crawl record of "
    "{url}.",
    "Evidence check for '{section}' on {path}: the observed HTTP status {status} and the "
    "recorded internal-link count of {links} for {topic} are restated only where they inform "
    "the reader.",
    "Scope note for '{section}' on {path}: prices, stock, delivery windows and availability "
    "for {topic} stay on the live component of {url}; the brief never freezes them into copy.",
    "Reviewer prompt {n} for {path}: does '{section}' still make sense if every sentence about "
    "{topic} is checked against the captured title and heading for {url}?",
    "Draft control {n} for {path}: '{section}' is written before any other section of the "
    "{topic} brief is finalised, so the page keeps one accountable job.",
    "Measurement note for '{section}' on {path}: after publication the next crawl re-reads "
    "{url} and records whether the {topic} heading, title and word count changed as planned.",
    "Link discipline for '{section}' on {path}: anchors that leave {topic} describe the "
    "destination page in words a reader would use, and each destination was captured in this "
    "crawl of {url}.",
    "Withholding note {n} for {path}: no ranking, traffic or revenue outcome is asserted for "
    "{topic} in '{section}' because no first-party baseline was collected for {url}.",
)


def _words(text: str) -> int:
    return len(str(text).split())


def _block_text(block: dict[str, Any]) -> list[str]:
    if block["type"] == "list":
        return [str(item) for item in block["items"]]
    return [str(block.get("text", ""))]


def _block_word_count(blocks: list[dict[str, Any]]) -> int:
    return sum(_words(text) for block in blocks for text in _block_text(block))


SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_SPLIT.split(str(text).strip()) if part.strip()]


def _asset_sentences(asset: dict[str, Any]) -> list[str]:
    sentences: list[str] = []
    for block in asset["body"]:
        if block["type"] == "heading":
            continue
        for text in _block_text(block):
            sentences.extend(_split_sentences(text))
    return sentences


def sentence_overlap(left: dict[str, Any], right: dict[str, Any]) -> float:
    """Share of sentences two compiled content assets have in common."""
    left_set = {" ".join(sentence.casefold().split()) for sentence in _asset_sentences(left)}
    right_set = {" ".join(sentence.casefold().split()) for sentence in _asset_sentences(right)}
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / min(len(left_set), len(right_set))


def _rewrite_duplicate_sentences(assets: list[dict[str, Any]]) -> int:
    """Localise any sentence already used by an earlier asset; return the rewrite count."""
    seen: set[str] = set()
    rewrites = 0
    for asset in assets:
        path = urlsplit(asset["target_url"]).path or "/"

        def localise(text: str, path: str = path) -> str:
            nonlocal rewrites
            parts = _split_sentences(text)
            if not parts:
                return text
            output: list[str] = []
            for sentence in parts:
                key = " ".join(sentence.casefold().split())
                if key in seen:
                    sentence = f"{sentence.rstrip('.')} (applies to {path})."
                    rewrites += 1
                    key = " ".join(sentence.casefold().split())
                seen.add(key)
                output.append(sentence)
            return " ".join(output)

        for block in asset["body"]:
            if block["type"] == "heading":
                continue
            if block["type"] == "list":
                block["items"] = [localise(item) for item in block["items"]]
            else:
                block["text"] = localise(block.get("text", ""))
    return rewrites


def _link_targets(
    page: dict[str, Any], pages: list[dict[str, Any]], limit: int = 6
) -> list[dict[str, Any]]:
    by_url = {item["normalized_url"]: item for item in pages}
    targets: list[dict[str, Any]] = []
    for url in page["links"]:
        target = by_url.get(url)
        if (
            target is None
            or target["normalized_url"] == page["normalized_url"]
            or target["status_code"] != 200
            or target["page_type"] == "Utility"
        ):
            continue
        targets.append(target)
        if len(targets) >= limit:
            return targets
    tokens = _token_set(page["title"] or page["h1"] or urlsplit(page["normalized_url"]).path)
    chosen = {item["normalized_url"] for item in targets} | {page["normalized_url"]}
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for other in pages:
        if other["normalized_url"] in chosen or other["status_code"] != 200:
            continue
        if other["page_type"] == "Utility":
            continue
        score = _jaccard(
            tokens,
            _token_set(other["title"] or other["h1"] or urlsplit(other["normalized_url"]).path),
        )
        if score > 0:
            scored.append((score, other["normalized_url"], other))
    scored.sort(key=lambda item: (-item[0], item[1]))
    for _, _, other in scored[: max(0, limit - len(targets))]:
        targets.append(other)
    return targets


def _asset_keywords(
    page: dict[str, Any], keywords: list[dict[str, Any]], clusters: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    url = page["normalized_url"]
    direct = [row for row in keywords if row["landing_url"] == url]
    cluster_ids = {
        cluster["id"] for cluster in clusters if cluster["primary_url"] == url
    }
    clustered = [
        row for row in keywords if row["cluster"] in cluster_ids and row not in direct
    ]
    merged = direct + clustered
    merged.sort(key=lambda row: (-(row["search_volume"] or 0), row["phrase"]))
    return merged[:12]


def _compile_content_assets(
    pages: list[dict[str, Any]],
    client_name: str,
    as_of: str,
    *,
    keywords: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    keyword_reason: str,
    outlines: dict[str, list[dict[str, str]]] | None = None,
    outline_source: str = "deterministic",
) -> list[dict[str, Any]]:
    """Expand every approved asset into an evidence-bound brief of >= 1,200 words."""
    candidates = _content_candidates(pages)
    # Publish up to the V18 benchmark's 20 assets, but only for pages that
    # actually qualified: the cap is a ceiling, never a quota to fill.
    count = min(CONTENT_ASSET_CAP, len(candidates))
    assets: list[dict[str, Any]] = []
    used_slugs: set[str] = set()
    for offset, page in enumerate(candidates[:count], start=1):
        asset_id = f"CONTENT-{offset:02d}"
        topic = _topic_label(page, client_name)
        intent, audience = INTENT_BY_TYPE.get(page["page_type"], INTENT_BY_TYPE["Other"])
        facts = page.get("_facts", {})
        url = page["normalized_url"]
        path = urlsplit(url).path or "/"
        word_count = page["word_count"]
        schema_types = [str(v) for v in facts.get("schema_types", []) if isinstance(v, str)]
        targets = _link_targets(page, pages)
        matched = _asset_keywords(page, keywords, clusters)
        supplied = (outlines or {}).get(asset_id) or []
        headings = [str(item["heading"]) for item in supplied] or list(
            OUTLINE_BY_TYPE.get(page["page_type"], OUTLINE_BY_TYPE["Other"])
        )
        guidance = {
            str(item["heading"]): str(item["guidance"]) for item in supplied if item.get("guidance")
        }
        evidence_ids = [page["evidence_id"]] + [
            row["evidence_ids"][0] for row in matched if row["evidence_ids"]
        ]

        body: list[dict[str, Any]] = [
            {"type": "heading", "level": 2, "text": "Search intent and audience"},
            {
                "type": "paragraph",
                "text": (
                    f"{path} is treated as a {page['page_type'].casefold()} page serving "
                    f"{intent} for {audience.casefold()}. "
                    f"The single accountable job of {topic} is to move that reader to the next "
                    f"decision without duplicating another approved-domain URL. "
                    + (
                        f"The strongest matched query for {path} is '{matched[0]['phrase']}'"
                        + (
                            f" with {matched[0]['search_volume']} monthly searches recorded by "
                            f"the market-data provider."
                            if matched[0]["search_volume"] is not None
                            else " with no search volume returned by the provider."
                        )
                        if matched
                        else f"No provider keyword row was matched to {path}: {keyword_reason}"
                    )
                ),
            },
            {"type": "heading", "level": 2, "text": "Current-state evidence"},
            {
                "type": "paragraph",
                "text": (
                    f"The approved crawl captured {url} with HTTP status {page['status_code']} "
                    f"as of {as_of}. "
                    + (
                        f"The observed title was '{page['title']}' ({len(page['title'])} "
                        "characters). "
                        if page["title"]
                        else f"No title tag was captured for {path}. "
                    )
                    + (
                        f"The observed H1 was '{page['h1']}'. "
                        if page["h1"]
                        else f"No H1 heading was captured for {path}. "
                    )
                    + (
                        f"A meta description of {len(page['meta_description'])} characters was "
                        "present. "
                        if page["meta_description"]
                        else f"No meta description was captured for {path}. "
                    )
                    + (
                        f"The captured body held {word_count} words."
                        if isinstance(word_count, int)
                        else f"Word count is unavailable for the {path} capture."
                    )
                ),
            },
            {
                "type": "list",
                "items": [
                    f"Internal links observed on {path}: {page['internal_links']}.",
                    f"External links observed on {path}: {page['external_links']}.",
                    (
                        f"Structured data observed on {path}: {', '.join(schema_types)}."
                        if schema_types
                        else f"No structured data type was recorded for {path}."
                    ),
                    (
                        f"Images on {path}: {page['images_total']} captured, "
                        f"{page['images_missing_alt']} without alt text."
                        if isinstance(page["images_total"], int)
                        else f"Image inventory is unavailable for {path}."
                    ),
                    (
                        f"Canonical observed on {path}: {page['canonical_url']}."
                        if page["canonical_url"]
                        else f"No canonical link element was captured on {path}."
                    ),
                    f"Indexability signal for {path}: {page['indexability']}.",
                ],
            },
            {"type": "heading", "level": 2, "text": "Keyword and query alignment"},
        ]
        if matched:
            body.append(
                {
                    "type": "list",
                    "items": [
                        (
                            f"'{row['phrase']}' - "
                            + (
                                f"{row['search_volume']} monthly searches"
                                if row["search_volume"] is not None
                                else "search volume unavailable"
                            )
                            + (
                                f", tracked position {row['position']}"
                                if row["position"] is not None
                                else ", no tracked position"
                            )
                            + (
                                f", funnel stage {row['funnel_stage']}"
                                if row["funnel_stage"]
                                else ", funnel stage unlabelled"
                            )
                            + f". {row['opportunity']}"
                        )
                        for row in matched
                    ],
                }
            )
        else:
            body.append(
                {
                    "type": "paragraph",
                    "text": (
                        f"No keyword metrics are available for {path}. {keyword_reason} "
                        f"The outline for {topic} is therefore built from observed page "
                        "structure and internal-link evidence alone, and no volume, difficulty "
                        "or ranking figure is asserted anywhere in this brief."
                    ),
                }
            )
        body.append({"type": "heading", "level": 2, "text": "Recommended outline"})
        for heading in headings:
            body.append({"type": "heading", "level": 3, "text": heading})
            phrase = ""
            if matched:
                phrase = matched[headings.index(heading) % len(matched)]["phrase"]
            body.append(
                {
                    "type": "paragraph",
                    "text": (
                        guidance.get(heading)
                        or (
                            f"Write '{heading}' for {path} so a reader of {topic} can act "
                            "without leaving the page for basic understanding. "
                            + (
                                f"Where it reads naturally, this section carries the matched "
                                f"phrase '{phrase}'; it is never repeated mechanically. "
                                if phrase
                                else "No matched query phrase is available, so headings stay "
                                "descriptive rather than keyword-shaped. "
                            )
                            + f"Every statement in this section must be checkable against the "
                            f"{as_of} capture of {url} or removed before review."
                        )
                    ),
                }
            )
        body.append({"type": "heading", "level": 2, "text": "Internal linking plan"})
        if targets:
            body.append(
                {
                    "type": "list",
                    "items": [
                        (
                            f"Link from {path} to {urlsplit(target['normalized_url']).path or '/'} "
                            f"using an anchor close to '{_topic_label(target, client_name)}'"
                            + (
                                "; the link was already observed in this crawl."
                                if target["normalized_url"] in set(page["links"])
                                else "; the link was not observed in this crawl and is a "
                                "candidate for editorial review."
                            )
                        )
                        for target in targets
                    ],
                }
            )
        else:
            body.append(
                {
                    "type": "paragraph",
                    "text": (
                        f"No approved-domain link target was observed or matched for {path}. "
                        "Editorial review should supply the first destination before the "
                        f"{topic} refresh is published."
                    ),
                }
            )
        body.append({"type": "heading", "level": 2, "text": "On-page checklist"})
        body.append(
            {
                "type": "list",
                "items": [
                    f"One H1 on {path} that names {topic} in the reader's language.",
                    f"A title tag for {path} of at most {TITLE_MAX_CHARS} characters that is "
                    "not identical to another approved-domain title.",
                    f"A meta description for {path} between {META_MIN_CHARS} and "
                    f"{META_MAX_CHARS} characters describing what the page does.",
                    f"Descriptive anchors leaving {path} that match their destination pages.",
                    f"No claim about {topic} that is absent from the {as_of} crawl record.",
                    f"Human editorial approval recorded before {path} is republished.",
                ],
            }
        )
        body.append({"type": "heading", "level": 2, "text": "Claim ledger and withholding"})
        body.append(
            {
                "type": "paragraph",
                "text": (
                    f"Every factual statement about {path} in this brief traces to the crawl "
                    f"evidence row {page['evidence_id']}"
                    + (
                        " and to the provider keyword rows "
                        + ", ".join(row["evidence_ids"][0] for row in matched[:4])
                        + "."
                        if matched
                        else "; no provider row was available to extend it."
                    )
                    + f" No traffic, ranking, conversion or revenue outcome is forecast for "
                    f"{topic}, and the brief stays withheld pending human editorial approval."
                ),
            }
        )

        index = 0
        while _block_word_count(body) < BRIEF_MIN_WORDS and index < 64:
            template = PAD_TEMPLATES[index % len(PAD_TEMPLATES)]
            section = headings[index % len(headings)]
            body.append(
                {
                    "type": "paragraph",
                    "text": template.format(
                        section=section,
                        path=path,
                        url=url,
                        topic=topic,
                        as_of=as_of,
                        status=page["status_code"],
                        links=page["internal_links"],
                        n=index + 1,
                    ),
                }
            )
            index += 1

        claims = [
            {
                "claim": (
                    f"The page {url} returned HTTP {page['status_code']} during the "
                    "approved-domain crawl."
                ),
                "evidence_ids": [page["evidence_id"]],
                "confidence": 1.0,
                "validation": "supported",
            },
            {
                "claim": (
                    f"The observed page title for {path} was '{page['title']}'."
                    if page["title"]
                    else f"The page {path} was captured without a title tag."
                ),
                "evidence_ids": [page["evidence_id"]],
                "confidence": 1.0,
                "validation": "supported",
            },
            {
                "claim": f"{page['internal_links']} internal links were observed on {path}.",
                "evidence_ids": [page["evidence_id"]],
                "confidence": 1.0,
                "validation": "supported",
            },
        ]
        if matched:
            top = matched[0]
            claims.append(
                {
                    "claim": (
                        f"The market-data provider returned the phrase '{top['phrase']}' with "
                        + (
                            f"{top['search_volume']} monthly searches."
                            if top["search_volume"] is not None
                            else "no search volume."
                        )
                    ),
                    "evidence_ids": [top["evidence_ids"][0]],
                    "confidence": 1.0,
                    "validation": "supported",
                }
            )
        assets.append(
            {
                "id": asset_id,
                "slug": _slug_for(page, used_slugs),
                "title": f"{topic} evidence-led refresh",
                "asset_type": "Existing-page content refresh",
                "target_url": url,
                "audience": audience,
                "intent": intent,
                "primary_topic": topic,
                "headline": f"{topic} evidence-led refresh",
                "summary": (
                    f"A review-ready refresh brief for {url} grounded in crawl observations "
                    f"captured on {as_of}; changeable details stay on the authoritative live "
                    "page."
                ),
                "body": body,
                "claims": claims,
                "outline_headings": headings,
                "matched_keywords": [row["id"] for row in matched],
                "internal_link_targets": [
                    target["normalized_url"] for target in targets
                ],
                "approval_state": "withheld_pending_human_approval",
                "generation_method": (
                    "llm_outline_evidence_bound"
                    if supplied
                    else "templated_evidence_framework"
                ),
                "outline_source": "llm_evidence_bound" if supplied else outline_source,
                "evidence_ids": sorted(set(evidence_ids)),
                "word_count": 0,
            }
        )
    _rewrite_duplicate_sentences(assets)
    for asset in assets:
        asset["word_count"] = _block_word_count(asset["body"])
    return assets


def _compile_opportunities(
    assets: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    keywords: list[dict[str, Any]],
    keyword_reason: str,
) -> list[dict[str, Any]]:
    """Opportunities come from measured clusters first, then approved assets."""
    keyword_by_id = {row["id"]: row for row in keywords}
    opportunities: list[dict[str, Any]] = []
    for cluster in sorted(
        clusters,
        key=lambda item: (-(item["total_volume"] or 0), item["id"]),
    ):
        members = [row for row in keywords if row["cluster"] == cluster["id"]]
        best = members[0] if members else None
        opportunities.append(
            {
                "id": f"OPP-{len(opportunities) + 1:02d}",
                "cluster": cluster["name"],
                "cluster_id": cluster["id"],
                "intent": cluster["intent"],
                "target_url": cluster["primary_url"],
                "coverage": cluster["coverage"],
                "keyword_count": cluster["keyword_count"],
                "decision": (
                    "Refresh the mapped target; do not create a competing URL"
                    if cluster["primary_url"]
                    else "No mapped URL was found; decide create-or-consolidate at Gate 2"
                ),
                "evidence_ids": list(cluster["evidence_ids"])[:10],
                "keyword_volume": cluster["total_volume"],
                "ranking": best["position"] if best is not None else None,
                "unavailable_reason": None if cluster["total_volume"] is not None else keyword_reason,
            }
        )
    covered_urls = {row["target_url"] for row in opportunities if row["target_url"]}
    for asset in assets:
        if asset["target_url"] in covered_urls:
            continue
        first_keyword = (
            keyword_by_id.get(asset["matched_keywords"][0])
            if asset["matched_keywords"]
            else None
        )
        opportunities.append(
            {
                "id": f"OPP-{len(opportunities) + 1:02d}",
                "cluster": asset["primary_topic"],
                "cluster_id": None,
                "intent": asset["intent"],
                "target_url": asset["target_url"],
                "coverage": "partial" if first_keyword else "unmeasured",
                "keyword_count": len(asset["matched_keywords"]),
                "decision": "Refresh existing target; do not create a competing URL",
                "evidence_ids": list(asset["evidence_ids"])[:10],
                "keyword_volume": (
                    first_keyword["search_volume"] if first_keyword is not None else None
                ),
                "ranking": first_keyword["position"] if first_keyword is not None else None,
                "unavailable_reason": None if first_keyword is not None else keyword_reason,
            }
        )
    return opportunities


# --------------------------------------------------------------------------- on-page proposals


def _fit_title(topic: str, client_name: str) -> str:
    title = f"{topic} | {client_name}"
    if len(title) <= TITLE_MAX_CHARS:
        return title
    keep = max(8, TITLE_MAX_CHARS - len(client_name) - 3)
    trimmed = topic[:keep].rstrip(" -|")
    title = f"{trimmed} | {client_name}"
    return title[:TITLE_MAX_CHARS].rstrip(" -|")


META_PURPOSE = {
    "Homepage": "See what {client} offers, how the main sections are organised and where to go next.",
    "Collection": "Browse the {topic} range, compare the published options and open any item for full detail.",
    "Product": "Read the published detail for {topic}, check the current specifications and continue to the next step.",
    "Editorial": "Read the {topic} guidance published by {client}, with the supporting detail kept on this page.",
    "Information": "Find the published {topic} detail for {client}, including how to get in touch.",
    "Other": "Review the published {topic} detail from {client} and continue to the section you need.",
}


def _fit_meta(page: dict[str, Any], topic: str, client_name: str) -> str:
    base = META_PURPOSE.get(page["page_type"], META_PURPOSE["Other"]).format(
        topic=topic, client=client_name
    )
    if len(base) < META_MIN_CHARS:
        base = f"{base} Details are kept current on this page of the {client_name} website."
    if len(base) < META_MIN_CHARS:
        base = f"{base} Review it before acting."
    return base[:META_MAX_CHARS].rstrip()


def _deterministic_proposal(
    page: dict[str, Any], client_name: str, keyword: dict[str, Any] | None
) -> dict[str, Any]:
    topic = _topic_label(page, client_name)
    current_title = page["title"] or None
    current_meta = page["meta_description"] or None
    current_h1 = page["h1"] or None
    proposed_title = _fit_title(topic, client_name)
    proposed_meta = _fit_meta(page, topic, client_name)
    proposed_h1 = topic[:H1_MAX_CHARS]
    evidence = [page["evidence_id"]]
    if keyword is not None:
        evidence.append(keyword["evidence_ids"][0])
    return {
        "page_id": page["id"],
        "url": page["normalized_url"],
        "page_type": page["page_type"],
        "current_title": current_title,
        "proposed_title": None if proposed_title == current_title else proposed_title,
        "title_rationale": (
            f"Deterministic proposal: lead with the observed topic '{topic}' and close with the "
            f"client name inside the {TITLE_MAX_CHARS}-character limit."
        ),
        "current_meta": current_meta,
        "proposed_meta": None if proposed_meta == current_meta else proposed_meta,
        "current_h1": current_h1,
        "proposed_h1": None if proposed_h1 == current_h1 else proposed_h1,
        "target_keyword": keyword["phrase"] if keyword is not None else None,
        "target_volume": keyword["search_volume"] if keyword is not None else None,
        "source": "deterministic",
        "approval_status": "withheld_pending_editorial_review",
        "evidence_ids": sorted(set(evidence)),
    }


def _proposal_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = _content_candidates(pages)
    return candidates[: min(25, len(candidates))]


def _keyword_for_page(
    page: dict[str, Any], keywords: list[dict[str, Any]], clusters: list[dict[str, Any]]
) -> dict[str, Any] | None:
    matched = _asset_keywords(page, keywords, clusters)
    return matched[0] if matched else None


def _proposal_fact_pack(
    data_client: dict[str, Any],
    project_id: str,
    pages: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    all_pages: list[dict[str, Any]],
    as_of: datetime,
) -> tuple[FactPack, dict[str, str]]:
    facts: list[VerifiedFact] = []
    evidence_ids: set[str] = set()
    fact_key_by_page: dict[str, str] = {}
    by_id = {row["page_id"]: row for row in proposals}
    for page in pages:
        key = f"page_facts:{page['id']}"
        evidence_id = str(uuid5(NAMESPACE_URL, f"{project_id}:{key}"))
        evidence_ids.add(evidence_id)
        fallback = by_id[page["id"]]
        facts.append(
            VerifiedFact(
                key,
                {
                    "page_id": page["id"],
                    "url": page["normalized_url"],
                    "page_type": page["page_type"],
                    "current_title": page["title"],
                    "current_meta_description": page["meta_description"],
                    "current_h1": page["h1"],
                    "word_count": page["word_count"],
                    "internal_links": page["internal_links"],
                    "target_keyword": fallback["target_keyword"],
                    "target_volume": fallback["target_volume"],
                    "client_name": data_client["name"],
                },
                (evidence_id,),
                as_of,
            )
        )
        fact_key_by_page[page["id"]] = key
    statuses = {
        page["normalized_url"]: page["status_code"]
        for page in all_pages
        if page.get("normalized_url")
    }
    return (
        FactPack(
            project_id=project_id,
            approved_domains=(data_client["domain"],),
            facts=tuple(facts),
            available_evidence_ids=frozenset(evidence_ids),
            known_url_statuses=statuses,
        ),
        fact_key_by_page,
    )


def _accept_llm_proposal(
    value: str | None, current: str | None, *, minimum: int, maximum: int
) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    if len(text) < minimum or len(text) > maximum:
        return None
    if current is not None and text.strip() == str(current).strip():
        return None
    return text


# --------------------------------------------------------------------------- generation calls


class _CallBudget:
    """Hard ceiling on package-level LLM calls (settings.PACKAGE_AI_MAX_CALLS)."""

    __slots__ = ("remaining",)

    def __init__(self, remaining: int) -> None:
        self.remaining = max(0, int(remaining))

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


def _guarded_structured_call(
    boundary_factory: Callable[[], Any] | None,
    *,
    task: str,
    fact_pack: FactPack,
    schema_name: str,
    schema: dict[str, Any],
    prompt_version: str,
    ledger_task: str,
    ledger_rows: list[dict[str, Any]],
    max_output_tokens: int,
) -> dict[str, Any] | None:
    """Run one structured generation, ledger it, and never raise."""
    row_id = f"GEN-{len(ledger_rows) + 1:03d}"
    configured_model = os.environ.get("OPENAI_STRATEGY_MODEL", DEFAULT_FINAL_MODEL)
    try:
        if boundary_factory is not None:
            boundary = boundary_factory()
        else:
            from generation.openai_boundary import OpenAIBoundary

            boundary = OpenAIBoundary(
                config=GenerationConfig(
                    final_model=configured_model, max_output_tokens=max_output_tokens
                )
            )
        result = boundary.generate_structured(
            task=task,
            fact_pack=fact_pack,
            schema_name=schema_name,
            schema=schema,
            purpose=GenerationPurpose.FINAL,
        )
        ledger = result.ledger
        ledger_rows.append(
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
                task=ledger_task,
                prompt_version=prompt_version,
            )
        )
        if result.status is not GenerationStatus.AVAILABLE or not result.data:
            return None
        payload = dict(result.data)
        issues = (
            *validate_claims(payload, fact_pack),
            *validate_domains_and_links(payload, fact_pack),
            *validate_placeholders(payload),
        )
        if any(issue.severity in {Severity.HIGH, Severity.CRITICAL} for issue in issues):
            ledger_rows[-1]["unavailable_reason"] = (
                "Generated output failed deterministic quality gates; deterministic output kept."
            )
            return None
        return payload
    except Exception as exc:  # noqa: BLE001 - generation must never break compilation
        ledger_rows.append(
            _ledger_row(
                row_id,
                configured_model=configured_model,
                status="unavailable",
                unavailable_reason=f"Generation failed safely: {str(exc)[:300]}",
                task=ledger_task,
                prompt_version=prompt_version,
            )
        )
        return None


def _compile_onpage_proposals(
    *,
    client: dict[str, Any],
    project_id: str,
    pages: list[dict[str, Any]],
    keywords: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    as_of_dt: datetime,
    ledger_rows: list[dict[str, Any]],
    attempt: bool,
    skip_reason: str,
    boundary_factory: Callable[[], Any] | None,
    budget: _CallBudget,
) -> list[dict[str, Any]]:
    """Grounded title/meta/H1 proposals with a deterministic fallback per page."""
    client_name = client["name"]
    selected = _proposal_pages(pages)
    proposals = [
        _deterministic_proposal(page, client_name, _keyword_for_page(page, keywords, clusters))
        for page in selected
    ]
    if not proposals:
        return proposals
    if not attempt:
        ledger_rows.append(
            _ledger_row(
                f"GEN-{len(ledger_rows) + 1:03d}",
                configured_model=os.environ.get("OPENAI_STRATEGY_MODEL", DEFAULT_FINAL_MODEL),
                status="unavailable",
                unavailable_reason=skip_reason,
                task="Evidence-bound on-page title, meta and H1 proposals",
                prompt_version=PROMPT_VERSION_ONPAGE,
            )
        )
        return proposals
    if not budget.take():
        ledger_rows.append(
            _ledger_row(
                f"GEN-{len(ledger_rows) + 1:03d}",
                configured_model=os.environ.get("OPENAI_STRATEGY_MODEL", DEFAULT_FINAL_MODEL),
                status="unavailable",
                unavailable_reason="Package LLM call budget was exhausted before this task.",
                task="Evidence-bound on-page title, meta and H1 proposals",
                prompt_version=PROMPT_VERSION_ONPAGE,
            )
        )
        return proposals
    try:
        fact_pack, _ = _proposal_fact_pack(
            client, project_id, selected, proposals, pages, as_of_dt
        )
    except Exception as exc:  # noqa: BLE001 - fall back to deterministic proposals
        ledger_rows.append(
            _ledger_row(
                f"GEN-{len(ledger_rows) + 1:03d}",
                configured_model=os.environ.get("OPENAI_STRATEGY_MODEL", DEFAULT_FINAL_MODEL),
                status="unavailable",
                unavailable_reason=f"Fact pack could not be built safely: {str(exc)[:200]}",
                task="Evidence-bound on-page title, meta and H1 proposals",
                prompt_version=PROMPT_VERSION_ONPAGE,
            )
        )
        return proposals
    payload = _guarded_structured_call(
        boundary_factory,
        task=ONPAGE_PROPOSAL_TASK,
        fact_pack=fact_pack,
        schema_name="package_onpage_proposals",
        schema=onpage_proposal_schema(),
        prompt_version=PROMPT_VERSION_ONPAGE,
        ledger_task="Evidence-bound on-page title, meta and H1 proposals",
        ledger_rows=ledger_rows,
        max_output_tokens=6000,
    )
    if payload is None:
        return proposals
    by_page = {row["page_id"]: row for row in proposals}
    for item in payload.get("proposals", []):
        row = by_page.get(str(item.get("page_id", "")))
        if row is None:
            continue
        title = _accept_llm_proposal(
            item.get("proposed_title"), row["current_title"], minimum=10, maximum=TITLE_MAX_CHARS
        )
        meta = _accept_llm_proposal(
            item.get("proposed_meta_description"),
            row["current_meta"],
            minimum=META_MIN_CHARS,
            maximum=META_MAX_CHARS,
        )
        heading = _accept_llm_proposal(
            item.get("proposed_h1"), row["current_h1"], minimum=3, maximum=H1_MAX_CHARS
        )
        if title is None and meta is None and heading is None:
            continue
        row["proposed_title"] = title
        row["proposed_meta"] = meta
        row["proposed_h1"] = heading
        rationale = " ".join(str(item.get("rationale", "")).split())
        row["title_rationale"] = rationale or row["title_rationale"]
        row["source"] = "llm_evidence_bound"
    return proposals


def _compile_content_outlines(
    *,
    client: dict[str, Any],
    project_id: str,
    pages: list[dict[str, Any]],
    keywords: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    as_of_dt: datetime,
    ledger_rows: list[dict[str, Any]],
    attempt: bool,
    skip_reason: str,
    boundary_factory: Callable[[], Any] | None,
    budget: _CallBudget,
) -> dict[str, list[dict[str, str]]]:
    """One structured call returning the outline set for every content asset."""
    candidates = _content_candidates(pages)[:CONTENT_ASSET_CAP]
    if not candidates:
        return {}
    if not attempt or not budget.take():
        ledger_rows.append(
            _ledger_row(
                f"GEN-{len(ledger_rows) + 1:03d}",
                configured_model=os.environ.get("OPENAI_STRATEGY_MODEL", DEFAULT_FINAL_MODEL),
                status="unavailable",
                unavailable_reason=(
                    skip_reason
                    if not attempt
                    else "Package LLM call budget was exhausted before this task."
                ),
                task="Evidence-bound content outline set",
                prompt_version=PROMPT_VERSION_OUTLINES,
            )
        )
        return {}
    facts: list[VerifiedFact] = []
    evidence_ids: set[str] = set()
    for offset, page in enumerate(candidates, start=1):
        asset_id = f"CONTENT-{offset:02d}"
        key = f"content_asset:{asset_id}"
        evidence_id = str(uuid5(NAMESPACE_URL, f"{project_id}:{key}"))
        evidence_ids.add(evidence_id)
        matched = _asset_keywords(page, keywords, clusters)
        facts.append(
            VerifiedFact(
                key,
                {
                    "asset_id": asset_id,
                    "url": page["normalized_url"],
                    "page_type": page["page_type"],
                    "current_title": page["title"],
                    "current_h1": page["h1"],
                    "word_count": page["word_count"],
                    "internal_links": page["internal_links"],
                    "matched_keywords": [
                        {"phrase": row["phrase"], "search_volume": row["search_volume"]}
                        for row in matched[:6]
                    ],
                },
                (evidence_id,),
                as_of_dt,
            )
        )
    try:
        fact_pack = FactPack(
            project_id=project_id,
            approved_domains=(client["domain"],),
            facts=tuple(facts),
            available_evidence_ids=frozenset(evidence_ids),
            known_url_statuses={
                page["normalized_url"]: page["status_code"]
                for page in pages
                if page.get("normalized_url")
            },
        )
    except Exception as exc:  # noqa: BLE001 - deterministic outlines are the fallback
        ledger_rows.append(
            _ledger_row(
                f"GEN-{len(ledger_rows) + 1:03d}",
                configured_model=os.environ.get("OPENAI_STRATEGY_MODEL", DEFAULT_FINAL_MODEL),
                status="unavailable",
                unavailable_reason=f"Fact pack could not be built safely: {str(exc)[:200]}",
                task="Evidence-bound content outline set",
                prompt_version=PROMPT_VERSION_OUTLINES,
            )
        )
        return {}
    payload = _guarded_structured_call(
        boundary_factory,
        task=CONTENT_OUTLINE_TASK,
        fact_pack=fact_pack,
        schema_name="package_content_outlines",
        schema=content_outline_schema(),
        prompt_version=PROMPT_VERSION_OUTLINES,
        ledger_task="Evidence-bound content outline set",
        ledger_rows=ledger_rows,
        max_output_tokens=8000,
    )
    if payload is None:
        return {}
    known = {f"CONTENT-{offset:02d}" for offset in range(1, len(candidates) + 1)}
    outlines: dict[str, list[dict[str, str]]] = {}
    seen_headings: set[str] = set()
    for item in payload.get("outlines", []):
        asset_id = str(item.get("asset_id", ""))
        if asset_id not in known or asset_id in outlines:
            continue
        sections: list[dict[str, str]] = []
        for section in item.get("sections", []):
            heading = " ".join(str(section.get("heading", "")).split())
            guidance = " ".join(str(section.get("guidance", "")).split())
            key = heading.casefold()
            if not heading or not guidance or key in seen_headings:
                continue
            seen_headings.add(key)
            sections.append({"heading": heading, "guidance": guidance})
        if len(sections) >= 3:
            outlines[asset_id] = sections
    return outlines


# --------------------------------------------------------------------------- deployment

REDIRECT_CONFIDENCE_FLOOR = 0.35


def _metadata_review_rows(
    pages: list[dict[str, Any]],
    client_name: str,
    proposals: list[dict[str, Any]],
    keyword_reason: str,
) -> list[dict[str, Any]]:
    """Metadata rows fed by the compiled proposals; no proposal repeats a current value."""
    by_page = {row["page_id"]: row for row in proposals}
    rows: list[dict[str, Any]] = []
    for page in pages:
        if page["status_code"] != 200 or page["page_type"] == "Utility":
            continue
        proposal = by_page.get(page["id"])
        current_title = page["title"] or ""
        current_meta = page["meta_description"] or ""
        current_h1 = page["h1"] or ""
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
                        if len(current_title) > TITLE_MAX_CHARS
                        else ("Too short" if len(current_title) < 25 else "Review")
                    )
                ),
                "proposed_title": proposal["proposed_title"] if proposal else None,
                "current_meta_description": current_meta or None,
                "meta_description_length": len(current_meta),
                "meta_description_issue": (
                    "Missing"
                    if not current_meta
                    else (
                        "Too long"
                        if len(current_meta) > 160
                        else ("Too short" if len(current_meta) < META_MIN_CHARS else "Review")
                    )
                ),
                "proposed_meta_description": proposal["proposed_meta"] if proposal else None,
                "current_h1": current_h1 or None,
                "h1_issue": (
                    "Missing"
                    if not current_h1
                    else ("Multiple captured" if " | " in current_h1 else "Review")
                ),
                "proposed_h1": proposal["proposed_h1"] if proposal else None,
                "target_keyword": (
                    proposal["target_keyword"]
                    if proposal and proposal["target_keyword"]
                    else f"Unavailable - {keyword_reason}"
                ),
                "target_volume": proposal["target_volume"] if proposal else None,
                "proposal_source": proposal["source"] if proposal else "unavailable",
                "priority": (
                    "P1" if not current_title or not current_meta or not current_h1 else "P2"
                ),
                "evidence_id": page["evidence_id"],
                "approval_status": "withheld_pending_editorial_review",
            }
        )
    return rows


def _redirect_destination(
    page: dict[str, Any], inventory: list[dict[str, Any]], client_name: str
) -> dict[str, Any]:
    """Best-match destination for a failing URL, or an explicit no-match record."""
    source_path = urlsplit(page["normalized_url"]).path
    source_tokens = _token_set(source_path)
    best: dict[str, Any] | None = None
    best_score = 0.0
    best_signals: tuple[float, float, float] = (0.0, 0.0, 0.0)
    for candidate in inventory:
        candidate_path = urlsplit(candidate["normalized_url"]).path
        if candidate_path == source_path:
            continue
        path_score = _jaccard(source_tokens, _token_set(candidate_path))
        title_score = _jaccard(
            source_tokens, _token_set(candidate["title"] or candidate["h1"] or "")
        )
        parent = candidate_path.rstrip("/")
        bonus = 0.10 if parent and source_path.startswith(parent + "/") else 0.0
        score = round(path_score * 0.55 + title_score * 0.35 + bonus, 4)
        if score > best_score or (
            score == best_score
            and best is not None
            and candidate["normalized_url"] < best["normalized_url"]
        ):
            best, best_score, best_signals = candidate, score, (path_score, title_score, bonus)
    if best is None or best_score < REDIRECT_CONFIDENCE_FLOOR:
        return {
            "target_url": None,
            "confidence": round(best_score, 4),
            "matched_on": "no confident match",
            "anchor": None,
            "reason": (
                "No crawled URL scored above the "
                f"{REDIRECT_CONFIDENCE_FLOOR:.2f} confidence floor; a destination must be chosen "
                "from content equivalence and link-graph evidence rather than a catch-all page."
            ),
        }
    path_score, title_score, bonus = best_signals
    signals = []
    if path_score > 0:
        signals.append(f"path-token overlap {path_score:.2f}")
    if title_score > 0:
        signals.append(f"title similarity {title_score:.2f}")
    if bonus > 0:
        signals.append("parent-collection ancestry")
    return {
        "target_url": best["normalized_url"],
        "confidence": round(best_score, 4),
        "matched_on": "; ".join(signals) or "path-token overlap",
        "anchor": _topic_label(best, client_name),
        "reason": (
            f"Best crawl-inventory match for {source_path}; the destination returned HTTP "
            f"{best['status_code']} in the same crawl."
        ),
    }


def _redirect_candidates(
    pages: list[dict[str, Any]], client_name: str
) -> list[dict[str, Any]]:
    inventory = [
        page
        for page in pages
        if page["status_code"] == 200 and page["page_type"] != "Utility"
    ]
    rows: list[dict[str, Any]] = []
    for page in pages:
        if page["status_code"] in {200, 301, 302}:
            continue
        match = _redirect_destination(page, inventory, client_name)
        rows.append(
            {
                "source_url": page["normalized_url"],
                "target_url": match["target_url"],
                "status_code": page["status_code"],
                "confidence": match["confidence"],
                "matched_on": match["matched_on"],
                "proposed_anchor": match["anchor"],
                "evidence_id": page["evidence_id"],
                "evidence_ids": [page["evidence_id"]],
                "approval_status": "withheld_pending_graph_validation",
                "included_in_deployment": False,
                "reason": match["reason"],
            }
        )
    return rows


def _inbound_counts(pages: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = dict.fromkeys(
        (page["normalized_url"] for page in pages), 0
    )
    for page in pages:
        for link in page["links"]:
            if link in counts and link != page["normalized_url"]:
                counts[link] += 1
    return counts


def _internal_link_rows(pages: list[dict[str, Any]], client_name: str) -> list[dict[str, Any]]:
    """Structural parents first, then authority-to-orphan recommendations."""
    by_url = {page["normalized_url"]: page for page in pages}
    inbound = _inbound_counts(pages)
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
                "target_inbound_links": inbound.get(target, 0),
                "observed_status": "Observed in crawl" if observed else "Candidate - not observed",
                "evidence_ids": [page["evidence_id"], by_url[target]["evidence_id"]],
                "approval_status": "review_ready" if observed else "withheld_pending_review",
            }
        )

    money_pages = [
        page
        for page in pages
        if page["status_code"] == 200
        and page["page_type"] in {"Homepage", "Collection", "Product"}
        and inbound.get(page["normalized_url"], 0) <= 1
    ]
    authorities = sorted(
        (
            page
            for page in pages
            if page["status_code"] == 200 and page["page_type"] != "Utility"
        ),
        key=lambda page: (-inbound.get(page["normalized_url"], 0), page["normalized_url"]),
    )
    for target in money_pages:
        target_url = target["normalized_url"]
        target_tokens = _token_set(
            target["title"] or target["h1"] or urlsplit(target_url).path
        )
        added = 0
        for source_page in authorities:
            if added >= 3:
                break
            source_url = source_page["normalized_url"]
            if source_url == target_url or (source_url, target_url) in seen:
                continue
            if target_url in set(source_page["links"]):
                continue
            overlap = _jaccard(
                target_tokens,
                _token_set(
                    source_page["title"] or source_page["h1"] or urlsplit(source_url).path
                ),
            )
            if inbound.get(source_url, 0) == 0 and overlap == 0:
                continue
            seen.add((source_url, target_url))
            rows.append(
                {
                    "source_url": source_url,
                    "target_url": target_url,
                    "anchor": _topic_label(target, client_name),
                    "rationale": (
                        f"{target['page_type']} page has "
                        f"{inbound.get(target_url, 0)} observed inbound internal links; the "
                        f"source page carries {inbound.get(source_url, 0)} and shares "
                        f"{overlap:.2f} token overlap with the destination."
                    ),
                    "link_type": "Authority page to low-inbound money page",
                    "target_inbound_links": inbound.get(target_url, 0),
                    "observed_status": "Candidate - not observed",
                    "evidence_ids": [source_page["evidence_id"], target["evidence_id"]],
                    "approval_status": "withheld_pending_review",
                }
            )
            added += 1
    return rows[:250]


def _compile_deployment(
    pages: list[dict[str, Any]],
    client_name: str,
    proposals: list[dict[str, Any]],
    keyword_reason: str,
) -> dict[str, Any]:
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
        "redirect_candidates": _redirect_candidates(pages, client_name),
        "canonical_candidates": canonical_candidates,
        "metadata_review": _metadata_review_rows(pages, client_name, proposals, keyword_reason),
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
                "No manual action, removal-attempt record or toxicity threshold breach was "
                "recorded for this run; no domain is proposed for disavow."
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
    keywords = data["keywords"]
    clusters = data["keyword_clusters"]
    performance = data["performance_vs_competitors"]
    gaps = sum(1 for cluster in clusters if cluster["coverage"] == "gap")
    market_section = {
        "title": "Market position and demand coverage",
        "level": 1,
        "paragraphs": (
            [
                f"The market-data provider returned {len(keywords)} keywords grouped into "
                f"{len(clusters)} deterministic clusters; {gaps} of those clusters have no "
                "matching crawled URL and are recorded as coverage gaps.",
                performance["summary"],
            ]
            if keywords
            else [
                "No provider keyword, competitor or backlink row was compiled for this run, so "
                "demand coverage, share of voice and authority comparisons are withheld rather "
                "than estimated.",
                data["market"]["unavailable_reason"] or PROVIDER_MISSING_REASON,
            ]
        ),
        "decision": (
            "Prioritise the mapped clusters and close the recorded gaps."
            if keywords
            else "Connect the market-data provider before any demand or authority claim is made."
        ),
    }
    return [
        market_section,
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
    lineage_rows = [
        *findings,
        *actions,
        *data["keywords"],
        *data["keyword_clusters"],
        *data["competitors"],
        *data["onpage_proposals"],
        *data["opportunities"],
    ]
    missing_lineage = sum(
        1
        for row in lineage_rows
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

    keywords = data["keywords"]
    with_volume = sum(1 for row in keywords if row["search_volume"] is not None)
    volume_share = round(with_volume / len(keywords), 4) if keywords else 0.0
    integrity = data["crawl_integrity"]
    metadata_rows = deployment["metadata_review"]
    proposed = sum(
        1
        for row in metadata_rows
        if row["proposed_title"] or row["proposed_meta_description"] or row["proposed_h1"]
    )
    proposal_share = round(proposed / len(metadata_rows), 4) if metadata_rows else 0.0

    measures = [
        ("Normalized pages", len(pages)),
        ("Aggregated findings", len(findings)),
        ("Canonical actions", len(actions)),
        ("Content assets", len(content)),
        ("Evidence rows", len(data["evidence"])),
        ("Provider keywords", len(keywords)),
        ("Keyword clusters", len(data["keyword_clusters"])),
        ("Competitor domains", len(data["competitors"])),
        ("Referring domains", len(data["backlinks"]["referring_domains"])),
        ("On-page proposals", len(data["onpage_proposals"])),
        ("Redirect candidates", len(deployment["redirect_candidates"])),
        ("Internal link candidates", len(deployment["internal_link_candidates"])),
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
            f"{missing_lineage} of {len(lineage_rows)} findings, actions, keywords, clusters, "
            "competitors, proposals or opportunities failed to resolve to evidence IDs",
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
        (
            "QA-09",
            "Keyword metric coverage",
            "PASS" if keywords and volume_share >= 0.5 else "UNAVAILABLE",
            (
                f"{with_volume} of {len(keywords)} provider keywords carry a search volume "
                f"({volume_share:.0%})"
                if keywords
                else f"No provider keyword rows were compiled: {data['market']['unavailable_reason'] or PROVIDER_MISSING_REASON}"
            ),
        ),
        (
            "QA-10",
            "Crawl integrity",
            {"clean": "PASS", "degraded": "UNAVAILABLE", "blocked": "FAIL"}[integrity["status"]],
            (
                f"{integrity['challenged_pages']} challenged and "
                f"{integrity['rate_limited_pages']} rate-limited responses across "
                f"{integrity['fetched_pages']} fetched pages "
                f"({integrity['challenge_share']:.0%} challenge share)"
            ),
        ),
        (
            "QA-11",
            "On-page proposal coverage",
            "PASS" if metadata_rows and proposal_share >= 0.5 else "UNAVAILABLE",
            (
                f"{proposed} of {len(metadata_rows)} reviewed pages carry at least one proposed "
                f"change ({proposal_share:.0%}); pages already matching the rules are recorded "
                "as no change required"
                if metadata_rows
                else "No successful HTML page was eligible for on-page review"
            ),
        ),
        (
            "QA-12",
            "Competitor availability",
            "PASS" if data["competitors"] else "UNAVAILABLE",
            (
                f"{len(data['competitors'])} competitor domains were returned by the "
                "market-data provider"
                if data["competitors"]
                else data["performance_vs_competitors"]["unavailable_reason"]
                or COMPETITOR_MISSING_REASON
            ),
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
        "keyword_volume_share": volume_share,
        "proposal_coverage": proposal_share,
        "crawl_challenge_share": integrity["challenge_share"],
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
    performance = data["performance_vs_competitors"]
    comparison_slides: list[dict[str, Any]] = []
    if performance["status"] == "available":
        comparison_slides.append(
            {
                "kind": "comparison",
                "eyebrow": "COMPETITOR PERFORMANCE",
                "title": "Measured gaps, not assumed ones.",
                "body": performance["summary"],
                "points": [
                    {
                        "label": metric["metric"],
                        "text": (
                            f"Client {metric['client']} vs median "
                            f"{metric['competitor_median']} ({metric['position']})"
                            if metric["client"] is not None
                            and metric["competitor_median"] is not None
                            else "Unavailable - metric not returned for both sides"
                        ),
                    }
                    for metric in performance["metrics"][:5]
                ],
            }
        )
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
        *comparison_slides,
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
    task: str = "Evidence-constrained package strategy enrichment",
    prompt_version: str = ENRICHMENT_PROMPT_VERSION,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "task": task,
        "configured_model": configured_model,
        "returned_model": returned_model,
        "prompt_version": prompt_version,
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

    # ---- provider-backed market, keyword, competitor and backlink families -------------
    metrics = list(run.metrics.all().order_by("metric_key", "created_at"))
    keyword_records = list(run.keywords.all())
    backlink_records = list(run.backlinks.all())
    provider_snapshot = _provider_snapshot(run)
    provider_reason = (
        _provider_reason(run, provider_snapshot)
        if provider_snapshot is None or provider_snapshot.availability != "available"
        else "The market-data provider returned no rows of this type for this run."
    )
    market = _compile_market(run, metrics, project.locale)
    brand_tokens = _token_set(client.name) | _token_set(project.primary_domain.split(".")[0])
    keywords = _compile_keywords(
        run, keyword_records, metrics, pages, brand_tokens, provider_reason
    )
    clusters = _compile_keyword_clusters(keywords, pages)
    competitors = _compile_competitors(metrics, provider_reason)
    backlinks = _compile_backlinks(run, backlink_records, market, provider_reason)
    performance = _compile_performance_vs_competitors(market, competitors, provider_reason)
    evidence.extend(
        _provider_evidence(
            keywords, competitors, backlinks, market, sources, project.locale, captured_iso
        )
    )
    keyword_reason = (
        "Keyword volume and ranking metrics were compiled from the market-data provider."
        if keywords
        else provider_reason
    )
    crawl_integrity = _compile_crawl_integrity(run, pages)

    # ---- grounded generations (hard-capped by settings.PACKAGE_AI_MAX_CALLS) -----------
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

    max_calls = int(getattr(settings, "PACKAGE_AI_MAX_CALLS", 3) or 0)
    budget = _CallBudget(max(0, max_calls - 1))  # one call is reserved for enrichment
    ledger_rows: list[dict[str, Any]] = []
    as_of_dt = datetime.fromisoformat(captured_iso)
    if as_of_dt.tzinfo is None:
        as_of_dt = as_of_dt.replace(tzinfo=UTC)
    client_block = {
        "name": client.name,
        "domain": project.primary_domain,
        "locale": project.locale,
    }
    onpage_proposals = _compile_onpage_proposals(
        client=client_block,
        project_id=str(project.pk),
        pages=pages,
        keywords=keywords,
        clusters=clusters,
        as_of_dt=as_of_dt,
        ledger_rows=ledger_rows,
        attempt=attempt,
        skip_reason=skip_reason,
        boundary_factory=boundary_factory,
        budget=budget,
    )
    outlines = _compile_content_outlines(
        client=client_block,
        project_id=str(project.pk),
        pages=pages,
        keywords=keywords,
        clusters=clusters,
        as_of_dt=as_of_dt,
        ledger_rows=ledger_rows,
        attempt=attempt,
        skip_reason=skip_reason,
        boundary_factory=boundary_factory,
        budget=budget,
    )
    content_assets = _compile_content_assets(
        pages,
        client.name,
        as_of,
        keywords=keywords,
        clusters=clusters,
        keyword_reason=keyword_reason,
        outlines=outlines,
    )
    opportunities = _compile_opportunities(content_assets, clusters, keywords, keyword_reason)
    deployment = _compile_deployment(pages, client.name, onpage_proposals, keyword_reason)

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
    if not keywords:
        limitations.append(
            f"Keyword volume, position and cluster metrics were not compiled: {provider_reason}"
        )
    if not competitors:
        limitations.append(
            "Competitor benchmarking was not compiled: "
            f"{performance['unavailable_reason'] or COMPETITOR_MISSING_REASON}"
        )
    if backlinks["status"] != "available":
        limitations.append(
            f"Backlink and referring-domain metrics were not compiled: {backlinks['unavailable_reason']}"
        )
    if crawl_integrity["status"] != "clean":
        limitations.append(crawl_integrity["note"])
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
        "market": market,
        "keywords": keywords,
        "keyword_clusters": clusters,
        "competitors": competitors,
        "performance_vs_competitors": performance,
        "backlinks": backlinks,
        "onpage_proposals": onpage_proposals,
        "crawl_integrity": crawl_integrity,
        "methodology": [
            {"topic": "Keyword funnel labelling", "rule": KEYWORD_METHODOLOGY},
            {"topic": "Keyword clustering and URL mapping", "rule": CLUSTER_METHODOLOGY},
            {"topic": "Redirect destination proposal", "rule": REDIRECT_METHODOLOGY},
        ],
        "strategy_sections": [],
        "measurement_plan": _compile_measurement_plan(len(pages), len(findings), sources),
        "generation_ledger": ledger_rows,
        "qa": {},
        "limitations": limitations,
        "deployment": deployment,
        "deck": [],
    }
    data["qa"] = _compile_qa(data, domains)
    data["strategy_sections"] = _compile_strategy_sections(data, unavailable_labels)
    data["deck"] = _compile_deck(data)

    if attempt:
        _enrich(data, boundary_factory)
    else:
        data["generation_ledger"].append(
            _ledger_row(
                f"GEN-{len(data['generation_ledger']) + 1:03d}",
                configured_model=os.environ.get("OPENAI_STRATEGY_MODEL", DEFAULT_FINAL_MODEL),
                status="unavailable",
                unavailable_reason=skip_reason,
            )
        )

    for page in data["pages"]:
        page.pop("_facts", None)
        page.pop("_page_pk", None)
    return repair_tree(data)
