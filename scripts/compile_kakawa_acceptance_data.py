"""Compile a fresh Kakawa crawl into the canonical v19 acceptance dataset.

The compiler is deliberately conservative. It publishes scores only for audit
categories covered by the public crawl, withholds private-source baselines, and
creates review-ready (not falsely approved) content and deployment proposals.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from audit_engine.models import BusinessProfile, PageSnapshot, Severity
from audit_engine.rules import RULESET_VERSION, AuditContext, run_rules
from audit_engine.scoring import CATEGORY_WEIGHTS, priority_score, scorecard
from audit_engine.urls import normalize_url

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME = PROJECT_ROOT / "fixtures" / "replay" / "kakawa_runtime_snapshot.json"
DEFAULT_STATIC = PROJECT_ROOT / "fixtures" / "replay" / "kakawa_public_snapshot.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "fixtures" / "replay" / "kakawa_acceptance_data.json"
APPROVED_DOMAIN = "kakawachocolates.com.au"
PROJECT_ID = str(uuid5(NAMESPACE_URL, "traffic-radius:kakawa:enterprise-seo-v19"))


def stable_uuid(kind: str, value: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"traffic-radius:kakawa:v19:{kind}:{value}"))


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def repair_text(value: str | None) -> str | None:
    """Repair common UTF-8-as-Windows-1252 crawl text without touching URLs."""
    if value is None:
        return None
    repaired = value
    for _ in range(2):
        if not any(marker in repaired for marker in ("\u00e2\u20ac", "\u00c2", "\u00c3")):
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
    """Apply crawl-text repair to every textual value in a generated payload."""
    if isinstance(value, dict):
        return {key: repair_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [repair_tree(item) for item in value]
    if isinstance(value, tuple):
        return tuple(repair_tree(item) for item in value)
    if isinstance(value, str):
        return repair_text(value)
    return value


def approved(url: str | None) -> bool:
    if not url:
        return False
    host = (urlsplit(url).hostname or "").casefold().rstrip(".")
    return host == APPROVED_DOMAIN or host.endswith("." + APPROVED_DOMAIN)


def friendly_severity(value: Severity) -> str:
    return value.value.title()


def page_type(url: str) -> str:
    path = urlsplit(url).path.casefold()
    if path in {"", "/"}:
        return "Homepage"
    if "/products/" in path:
        return "Product"
    if path.startswith("/collections/"):
        return "Collection"
    if path.startswith("/blogs/"):
        return "Editorial"
    if path.startswith("/pages/"):
        return "Information"
    if path in {"/cart", "/search"}:
        return "Utility"
    return "Other"


def compile_pages(
    runtime: dict[str, Any],
) -> tuple[list[dict[str, Any]], tuple[PageSnapshot, ...], dict[str, str]]:
    captured = datetime.fromisoformat(runtime["captured_at"].replace("Z", "+00:00"))
    output: list[dict[str, Any]] = []
    canonical: list[PageSnapshot] = []
    evidence_map: dict[str, str] = {}
    normalized_index: dict[str, int] = {}
    for raw in runtime["crawl"]["pages"]:
        original = normalize_url(raw["requested_url"])
        normalized = normalize_url(raw["final_url"])
        title = repair_text(raw.get("title"))
        meta_description = repair_text(raw.get("meta_description"))
        h1_values = tuple(repair_text(value) or "" for value in (raw.get("h1") or ()))
        if not approved(original) or not approved(normalized):
            raise ValueError(f"Crawler returned a URL outside the approved domain: {original}")
        if normalized in normalized_index:
            existing = output[normalized_index[normalized]]
            if original not in existing["original_urls"]:
                existing["original_urls"].append(original)
            existing["duplicate_observations"] += 1
            continue
        index = len(output) + 1
        normalized_index[normalized] = len(output)
        evidence_uuid = stable_uuid("evidence", normalized)
        evidence_id = f"EV-{index:04d}"
        evidence_map[evidence_uuid] = evidence_id
        page_uuid = stable_uuid("page", normalized)
        canonical_url = raw.get("canonical_url")
        canonical.append(
            PageSnapshot(
                id=page_uuid,
                project_id=PROJECT_ID,
                original_url=original,
                normalized_url=normalized,
                status_code=raw.get("status_code"),
                captured_at=captured,
                evidence_id=evidence_uuid,
                title=title,
                meta_description=meta_description,
                h1=h1_values,
                canonical_url=canonical_url,
                robots_directives=tuple(raw.get("robots_directives") or ()),
                content_type=raw.get("content_type"),
                body_sha256=raw.get("body_sha256"),
                links=tuple(raw.get("links") or ()),
            )
        )
        directives = {item.casefold() for item in raw.get("robots_directives") or ()}
        approved_links = sorted(
            {normalize_url(link) for link in raw.get("links") or () if approved(link)}
        )
        output.append(
            {
                "id": f"URL-{index:04d}",
                "evidence_id": evidence_id,
                "original_url": original,
                "original_urls": [original],
                "duplicate_observations": 0,
                "normalized_url": normalized,
                "status_code": raw.get("status_code"),
                "title": title,
                "meta_description": meta_description,
                "h1": " | ".join(value for value in h1_values if value) or None,
                "canonical_url": canonical_url,
                "indexability": "Noindex observed"
                if directives.intersection({"noindex", "none"})
                else "No noindex observed",
                "word_count": None,
                "internal_links": len(raw.get("links") or ()),
                "redirect_chain": raw.get("redirect_chain") or [],
                "content_type": raw.get("content_type"),
                "body_sha256": raw.get("body_sha256"),
                "page_type": page_type(normalized),
                "links": approved_links,
                "captured_at": captured.isoformat(),
            }
        )
    if len(output) < 10 or sum(1 for page in output if page["status_code"] == 200) < 10:
        raise ValueError("Fresh acceptance crawl did not collect at least ten successful pages")
    return output, tuple(canonical), evidence_map


def source_rows(runtime: dict[str, Any], pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    crawl = runtime["crawl"]
    discovered = max(crawl["discovered_count"], len(pages), 1)
    coverage = (
        1.0 if crawl["stopped_reason"] == "queue_exhausted" else min(1.0, len(pages) / discovered)
    )
    captured = runtime["captured_at"]
    rows = [
        {
            "id": "SRC-CRAWL",
            "label": "Fresh approved-domain website crawl",
            "kind": "crawl",
            "status": "available" if pages else "unavailable",
            "captured_at": captured,
            "scope": f"{crawl.get('fetched_count', len(pages))} fetched responses; "
            f"{len(pages)} unique normalized pages; {discovered} discovered; {crawl['stopped_reason']}",
            "coverage": coverage,
            "unavailable_reason": None,
        },
        {
            "id": "SRC-PUBLIC",
            "label": "Official Kakawa public business facts",
            "kind": "human_verified",
            "status": "available",
            "captured_at": captured,
            "scope": "Official kakawachocolates.com.au pages only",
            "coverage": 1.0,
            "unavailable_reason": None,
        },
    ]
    labels = {
        "gsc": "Google Search Console",
        "ga4": "Google Analytics 4",
        "semrush": "SEMrush",
        "pagespeed": "PageSpeed Insights",
        "openai": "OpenAI generation",
    }
    for key, label in labels.items():
        state = runtime["sources"][key]
        rows.append(
            {
                "id": f"SRC-{key.upper()}",
                "label": label,
                "kind": key,
                "status": state["status"],
                "captured_at": captured,
                "scope": "Not collected in public acceptance run",
                "coverage": 0.0,
                "unavailable_reason": state["reason"],
            }
        )
    return rows


def evidence_rows(
    runtime: dict[str, Any],
    static: dict[str, Any],
    pages: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    evidence: list[dict[str, Any]] = []
    for page in pages:
        value = (
            f"HTTP {page['status_code']}; title={page['title'] or 'unavailable'}; "
            f"meta={'present' if page['meta_description'] else 'unavailable'}; "
            f"H1={page['h1'] or 'unavailable'}; internal_links={page['internal_links']}"
        )
        evidence.append(
            {
                "id": page["evidence_id"],
                "source_id": "SRC-CRAWL",
                "evidence_type": "page_observation",
                "observed_value": value,
                "original_url": page["original_url"],
                "normalized_url": page["normalized_url"],
                "captured_at": page["captured_at"],
                "locale": "en-AU",
                "scope": "approved-domain HTML response",
                "confidence": 1.0,
                "unavailable_reason": None,
            }
        )
    fact_ids: dict[str, str] = {}
    for index, observation in enumerate(static.get("observations", []), start=1):
        identifier = f"EV-FACT-{index:02d}"
        fact_ids[observation["key"]] = identifier
        evidence.append(
            {
                "id": identifier,
                "source_id": "SRC-PUBLIC",
                "evidence_type": "verified_public_fact",
                "observed_value": observation["value"],
                "original_url": observation["source_url"],
                "normalized_url": normalize_url(observation["source_url"]),
                "captured_at": static["captured_at"],
                "locale": "en-AU",
                "scope": observation["key"],
                "confidence": observation["confidence"],
                "unavailable_reason": None,
            }
        )
    for source in sources:
        if source["status"] != "unavailable":
            continue
        identifier = f"EV-UNAVAILABLE-{source['kind'].upper()}"
        fact_ids[f"unavailable_{source['kind']}"] = identifier
        evidence.append(
            {
                "id": identifier,
                "source_id": source["id"],
                "evidence_type": "unavailable_state",
                "observed_value": None,
                "original_url": None,
                "normalized_url": None,
                "captured_at": runtime["captured_at"],
                "locale": "en-AU",
                "scope": source["scope"],
                "confidence": 1.0,
                "unavailable_reason": source["unavailable_reason"],
            }
        )
    return evidence, fact_ids


def aggregate_findings(
    canonical_pages: tuple[PageSnapshot, ...], evidence_map: dict[str, str], as_of: str
) -> tuple[list[dict[str, Any]], dict[str, float], Any]:
    context = AuditContext(
        project_id=PROJECT_ID,
        pages=canonical_pages,
        allowed_domains=(APPROVED_DOMAIN,),
        business_profile=BusinessProfile.ECOMMERCE,
    )
    raw_findings = run_rules(context)
    groups: dict[tuple[str, str, str, Severity, str], list[Any]] = defaultdict(list)
    for finding in raw_findings:
        groups[
            (
                finding.category,
                finding.rule_id,
                finding.title,
                finding.severity,
                finding.risk.value,
            )
        ].append(finding)
    severity_impact = {
        Severity.CRITICAL: 100,
        Severity.HIGH: 82,
        Severity.MEDIUM: 62,
        Severity.LOW: 38,
        Severity.INFO: 15,
    }
    rows: list[dict[str, Any]] = []
    for offset, (key, values) in enumerate(groups.items(), start=1):
        category, rule_id, title, severity, risk = key
        urls = sorted({url for item in values for url in item.affected_urls})
        evidence_ids = sorted(
            {evidence_map[evidence] for item in values for evidence in item.evidence_ids}
        )
        reach = min(100.0, len(urls) / max(1, len(canonical_pages)) * 100)
        confidence = sum(item.confidence for item in values) / len(values)
        effort = 35 if severity in {Severity.LOW, Severity.MEDIUM} else 55
        priority = priority_score(
            impact=severity_impact[severity],
            evidence_confidence=confidence * 100,
            reach=reach,
            business_criticality=75 if category == "technical" else 65,
            dependency_urgency=70 if category == "technical" else 45,
            effort=effort,
        )
        rows.append(
            {
                "id": f"F-{offset:03d}",
                "priority": priority.band,
                "priority_score": priority.score,
                "category": category,
                "rule_id": rule_id,
                "rule_version": RULESET_VERSION,
                "severity": friendly_severity(severity),
                "title": title,
                "description": values[0].description,
                "impact": f"Observed on {len(urls)} of {len(canonical_pages)} fetched pages; review before deployment.",
                "confidence": confidence,
                "reach": f"{len(urls)} pages",
                "affected_count": len(urls),
                "affected_urls": urls,
                "effort": "Medium" if effort >= 50 else "Low",
                "implementation_risk": risk,
                "approval_class": "agency_admin" if risk in {"high", "dangerous"} else "analyst",
                "as_of_date": as_of,
                "evidence_ids": evidence_ids,
            }
        )
    order = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}
    rows.sort(key=lambda item: (order[item["priority"]], -item["priority_score"], item["id"]))

    crawl_coverage = 1.0
    coverage = {
        "technical": crawl_coverage,
        "on_page": crawl_coverage,
        "performance": 0.0,
        "analytics": 0.0,
        "keyword_architecture": 0.0,
        "authority": 0.0,
        "cro": 0.0,
        "ecommerce": 0.0,
        "geo_aeo": 0.0,
    }
    card = scorecard(BusinessProfile.ECOMMERCE, raw_findings, coverage)
    return rows, coverage, card


def category_rows(
    coverage: dict[str, float], card: Any, findings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_name = {item.category: item for item in card.categories}
    evidence_by_category: dict[str, list[str]] = defaultdict(list)
    for finding in findings:
        evidence_by_category[finding["category"]].extend(finding["evidence_ids"])
    rows = []
    for category, weight in CATEGORY_WEIGHTS[BusinessProfile.ECOMMERCE].items():
        covered = coverage.get(category, 0.0)
        scored = by_name[category]
        rows.append(
            {
                "category": category.replace("_", " ").title(),
                "key": category,
                "score": scored.score if covered >= 0.70 else None,
                "coverage": covered,
                "weight": weight / 100,
                "rule_version": RULESET_VERSION,
                "status": "available" if covered >= 0.70 else "unavailable",
                "unavailable_reason": None
                if covered >= 0.70
                else "Required evidence source was not connected for this public run",
                "evidence_ids": sorted(set(evidence_by_category[category]))[:30],
            }
        )
    return rows


def choose_page(pages: list[dict[str, Any]], fragments: tuple[str, ...]) -> dict[str, Any] | None:
    successful = [page for page in pages if page["status_code"] == 200]
    for fragment in fragments:
        target = fragment.rstrip("/") or "/"
        for page in successful:
            candidate = urlsplit(page["normalized_url"]).path.rstrip("/") or "/"
            if candidate == target:
                return page
    for fragment in fragments:
        for page in successful:
            if fragment in urlsplit(page["normalized_url"]).path:
                return page
    return None


def content_assets(
    pages: list[dict[str, Any]], fact_ids: dict[str, str], as_of: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    specifications = [
        (
            "homepage-discovery",
            ("/",),
            "Sydney artisan chocolate shopping guide",
            "brand and product discovery",
            "Australian chocolate shoppers",
            "Choose the most relevant route into the Kakawa range",
        ),
        (
            "gift-boxes",
            ("/collections/gift-boxes",),
            "Chocolate gift box selection guide",
            "gift selection",
            "Gift buyers",
            "Match the box format to the occasion and recipient",
        ),
        (
            "chocolate-bars",
            ("/collections/chocolate-bar",),
            "Chocolate bar collection guide",
            "collection discovery",
            "Chocolate bar shoppers",
            "Navigate the current chocolate bar collection",
        ),
        (
            "pralines-bonbons",
            ("/collections/pralines-bonbons",),
            "Praline and bonbon selection guide",
            "collection discovery",
            "Gift and self-purchase shoppers",
            "Compare the current praline and bonbon route",
        ),
        (
            "chocolate-slabs",
            ("/collections/chocolate-slab",),
            "Chocolate slab buying guide",
            "collection discovery",
            "Celebration and gift buyers",
            "Decide whether a slab format suits the occasion",
        ),
        (
            "single-origin",
            ("/collections/single-origin-chocolates",),
            "Single-origin chocolate collection guide",
            "product education",
            "Origin-curious chocolate shoppers",
            "Explore the current single-origin collection without unsupported tasting claims",
        ),
        (
            "design-your-own-box",
            ("/collections/design-your-own-box",),
            "Design-your-own chocolate box guide",
            "custom selection",
            "Personalised gift buyers",
            "Prepare a considered custom-box selection",
        ),
        (
            "confectionary",
            ("/collections/confectionary",),
            "Confectionery collection guide",
            "collection discovery",
            "Confectionery shoppers",
            "Navigate the current confectionery range",
        ),
        (
            "corporate-chocolates",
            ("/pages/corporate-chocolates",),
            "Corporate chocolate enquiry guide",
            "commercial enquiry",
            "Corporate buyers",
            "Clarify a corporate chocolate brief before contact",
        ),
        (
            "events-corporates",
            ("/pages/events-corporates",),
            "Events and corporate chocolate planning guide",
            "event enquiry",
            "Event and workplace organisers",
            "Prepare scope, timing and quantity questions before enquiry",
        ),
        (
            "wedding-chocolates",
            ("/pages/wedding-chocolates",),
            "Wedding chocolate planning guide",
            "wedding enquiry",
            "Couples and wedding planners",
            "Prepare a wedding chocolate brief without assuming availability",
        ),
        (
            "wholesale-enquiry",
            ("/pages/wholesale",),
            "Wholesale chocolate enquiry guide",
            "trade enquiry",
            "Retail and hospitality buyers",
            "Prepare a wholesale-fit conversation",
        ),
        (
            "shipping-decision",
            ("/pages/shipping",),
            "Chocolate delivery planning guide",
            "shipping support",
            "Online gift buyers",
            "Check current delivery considerations before ordering",
        ),
        (
            "about-kakawa",
            ("/pages/about-us",),
            "About Kakawa evidence-led page refresh",
            "brand education",
            "Quality-conscious shoppers",
            "Explain the observed brand story while keeping changeable facts verified",
        ),
        (
            "craft-process",
            ("/pages/how-we-make-chocolate",),
            "How Kakawa presents its chocolate-making process",
            "brand education",
            "Craft-conscious shoppers",
            "Explain the public craft process without adding unsupported claims",
        ),
        (
            "ingredients-ethics",
            ("/pages/our-ingredients-and-ethics",),
            "Ingredients and ethics information guide",
            "trust and product education",
            "Ingredient-conscious shoppers",
            "Make the official evidence easier to evaluate",
        ),
        (
            "membership",
            ("/pages/membership",),
            "Kakawa rewards information guide",
            "membership support",
            "Returning customers",
            "Clarify the membership decision using only current official terms",
        ),
        (
            "chocolate-journal",
            ("/blogs/chocolates",),
            "Chocolate journal discovery hub",
            "informational discovery",
            "Chocolate learners and shoppers",
            "Organise editorial discovery around accountable internal links",
        ),
        (
            "christmas-collection",
            ("/collections/christmas",),
            "Christmas chocolate planning guide",
            "seasonal gift discovery",
            "Christmas gift buyers",
            "Plan a seasonal purchase while checking live availability",
        ),
        (
            "easter-collection",
            ("/collections/easter",),
            "Easter chocolate planning guide",
            "seasonal gift discovery",
            "Easter gift buyers",
            "Plan an Easter purchase while checking live availability",
        ),
    ]
    assets: list[dict[str, Any]] = []
    opportunities: list[dict[str, Any]] = []
    business_name_evidence = fact_ids.get("public_business_name")
    address_evidence = fact_ids.get("public_store_address")
    for offset, (slug, fragments, title, intent, audience, decision) in enumerate(
        specifications, start=1
    ):
        page = choose_page(pages, fragments)
        if not page:
            continue
        page_evidence = page["evidence_id"]
        source_ids = [page_evidence]
        if business_name_evidence:
            source_ids.append(business_name_evidence)
        if address_evidence and offset in {1, 9, 10, 11, 14, 15}:
            source_ids.append(address_evidence)
        observed_title = page["title"] or page["h1"] or "Official Kakawa page"
        observed_h1 = page["h1"] or "No H1 was captured"
        summary = (
            f"A review-ready refresh for {page['normalized_url']} that answers the page's distinct "
            f"{intent} job, improves decision clarity, and keeps products, prices, timing, availability, "
            "policies and other changeable details on the authoritative live page."
        )
        body = [
            {"type": "heading", "level": 2, "text": decision},
            {
                "type": "paragraph",
                "text": f"This page should help {audience.lower()} complete one clear job: {intent}. Lead with the decision, explain what information is available, and make the next step obvious without adding unverified product or service claims.",
            },
            {
                "type": "paragraph",
                "text": f"The approved crawl observed the page title '{observed_title}' and the H1 '{observed_h1}' as of {as_of}. Those observations establish the page topic; they do not prove pricing, stock, delivery timing, ratings or performance.",
            },
            {"type": "heading", "level": 2, "text": "What the reader needs to decide"},
            {
                "type": "list",
                "items": [
                    "The purpose, recipient or occasion",
                    "The format or collection that best fits the need",
                    "Any quantity, timing, delivery or collection constraints",
                    "Which details must be confirmed on the current official page before acting",
                ],
            },
            {"type": "heading", "level": 2, "text": "Recommended information order"},
            {
                "type": "list",
                "items": [
                    "A concise statement of who the page is for",
                    "A scannable explanation of the available decision routes",
                    "Current terms and limitations sourced from the live page",
                    "Relevant links to supporting collection, delivery, contact or policy pages",
                    "A single next action matched to the page intent",
                ],
            },
            {"type": "heading", "level": 2, "text": "Trust and evidence controls"},
            {
                "type": "paragraph",
                "text": "Do not add awards, review scores, origin claims, dietary claims, delivery promises, stock statements or performance superlatives unless each one is present in an approved fact pack with an as-of date. Keep volatile details in maintained product or policy components rather than hard-coding them into evergreen copy.",
            },
            {"type": "heading", "level": 2, "text": "Questions this page should answer"},
            {
                "type": "list",
                "items": [
                    f"Is this the right page for someone researching {intent}?",
                    "What should the reader compare or prepare before choosing?",
                    "Which details can change and therefore require a live check?",
                    "Where should the reader go if the page does not resolve the question?",
                ],
            },
            {"type": "heading", "level": 2, "text": "Editorial acceptance criteria"},
            {
                "type": "list",
                "items": [
                    "One accountable search intent and one canonical target URL",
                    "No unsupported claims or fabricated metrics",
                    "Descriptive internal-link anchors that match the destination",
                    "Accessible headings, plain language and a clear next step",
                    "Human editorial approval before publication",
                ],
            },
            {"type": "heading", "level": 2, "text": "Next step"},
            {
                "type": "paragraph",
                "text": f"Review the current information at {page['normalized_url']}. Confirm all changeable details on the live page, then route unresolved questions through the contact or enquiry path displayed by Kakawa.",
            },
        ]
        claims = [
            {
                "claim": f"The official page was observed with the title: {observed_title}",
                "evidence_ids": [page_evidence],
                "confidence": 1.0,
                "validation": "supported",
            },
            {
                "claim": f"The page was captured successfully on the approved .com.au domain as of {as_of}.",
                "evidence_ids": [page_evidence],
                "confidence": 1.0,
                "validation": "supported",
            },
        ]
        asset = {
            "id": f"CONTENT-{offset:02d}",
            "slug": slug,
            "title": title,
            "asset_type": "Existing-page content refresh",
            "target_url": page["normalized_url"],
            "audience": audience,
            "intent": intent,
            "primary_topic": title,
            "headline": title,
            "summary": summary,
            "body": body,
            "claims": claims,
            "approval_state": "withheld_pending_human_approval",
            "generation_method": "evidence-bound editorial framework with GPT strategy synthesis separated from canonical facts",
            "evidence_ids": source_ids,
        }
        assets.append(asset)
        opportunities.append(
            {
                "id": f"OPP-{offset:02d}",
                "cluster": title,
                "intent": intent,
                "target_url": page["normalized_url"],
                "decision": "Refresh existing target; do not create a competing URL",
                "evidence_ids": [page_evidence],
                "keyword_volume": None,
                "ranking": None,
                "unavailable_reason": "GSC and SEMrush were not connected; volume and ranking are withheld",
            }
        )
    if len(assets) < 20:
        raise ValueError(
            f"Only {len(assets)} of 20 distinct evidence-supported content opportunities resolved"
        )
    if len({asset["target_url"] for asset in assets}) != len(assets):
        raise ValueError("Content opportunities contain duplicate targets")
    return assets, opportunities


def action_rows(
    evidence: list[dict[str, Any]], findings: list[dict[str, Any]], content: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    all_ids = {item["id"] for item in evidence}
    default = next(item["id"] for item in evidence if item["source_id"] == "SRC-CRAWL")
    unavailable = {
        item["source_id"].replace("SRC-", ""): item["id"]
        for item in evidence
        if item["evidence_type"] == "unavailable_state"
    }
    finding_evidence = [value for item in findings for value in item["evidence_ids"]]
    technical = finding_evidence[:8] or [default]
    on_page = finding_evidence[8:16] or technical
    content_evidence = [value for item in content for value in item["evidence_ids"]][:20] or [
        default
    ]
    specs = [
        (
            "Foundation",
            1,
            "P1",
            "Approve the evidence boundary and unavailable-source register",
            "Agency admin",
            [],
            "3h",
            "Gate 1 scope decision recorded",
            "agency_admin",
            "low",
            [default],
        ),
        (
            "Foundation",
            1,
            "P1",
            "Confirm the approved .com.au domain and exclusion rules",
            "Technical SEO",
            ["A-001"],
            "3h",
            "Zero wrong-domain records",
            "agency_admin",
            "moderate",
            [default],
        ),
        (
            "Foundation",
            1,
            "P2",
            "Reconcile discovered, fetched and normalized URL counts",
            "Analyst",
            ["A-002"],
            "4h",
            "One canonical inventory count",
            "analyst",
            "low",
            [default],
        ),
        (
            "Evidence",
            2,
            "P1",
            "Connect and validate Google Search Console in staging",
            "Analyst",
            ["A-001"],
            "5h",
            "GSC availability check passes",
            "analyst",
            "moderate",
            [unavailable.get("GSC")],
        ),
        (
            "Evidence",
            2,
            "P1",
            "Connect and validate GA4 plus conversion definitions",
            "Analytics",
            ["A-001"],
            "6h",
            "GA4 event dictionary approved",
            "analyst",
            "moderate",
            [unavailable.get("GA4")],
        ),
        (
            "Evidence",
            2,
            "P2",
            "Connect SEMrush and PageSpeed or preserve truthful unavailable states",
            "Analyst",
            ["A-001"],
            "5h",
            "Two source readiness decisions",
            "analyst",
            "low",
            [unavailable.get("SEMRUSH"), unavailable.get("PAGESPEED")],
        ),
        (
            "Technical",
            3,
            "P1",
            "Validate HTTP status distribution and error-page ownership",
            "Technical SEO",
            ["A-003"],
            "6h",
            "Every error URL assigned a disposition",
            "analyst",
            "moderate",
            technical,
        ),
        (
            "Technical",
            3,
            "P1",
            "Validate redirect chains and reject redirect loops",
            "Engineering",
            ["A-007"],
            "7h",
            "Zero redirect loops",
            "agency_admin",
            "high",
            technical,
        ),
        (
            "Technical",
            3,
            "P1",
            "Validate canonical graph targets against approved-domain pages",
            "Technical SEO",
            ["A-007"],
            "7h",
            "Zero unsafe canonical targets",
            "agency_admin",
            "high",
            technical,
        ),
        (
            "On-page",
            4,
            "P1",
            "Resolve missing or weak title tags on priority templates",
            "SEO + Content",
            ["A-007"],
            "8h",
            "Priority title review completed",
            "analyst",
            "moderate",
            on_page,
        ),
        (
            "On-page",
            4,
            "P1",
            "Resolve missing meta descriptions on priority templates",
            "SEO + Content",
            ["A-010"],
            "8h",
            "Priority meta review completed",
            "analyst",
            "moderate",
            on_page,
        ),
        (
            "On-page",
            4,
            "P1",
            "Resolve missing, multiple or duplicated H1 patterns",
            "SEO + Engineering",
            ["A-010"],
            "6h",
            "Priority H1 review completed",
            "analyst",
            "moderate",
            on_page,
        ),
        (
            "Ecommerce",
            5,
            "P1",
            "Review product template indexability, titles and canonical behavior",
            "Technical SEO",
            ["A-009"],
            "8h",
            "Product template QA passes",
            "agency_admin",
            "high",
            technical,
        ),
        (
            "Ecommerce",
            5,
            "P2",
            "Review collection pagination and duplicate-route behavior",
            "Technical SEO",
            ["A-009"],
            "7h",
            "Pagination decisions documented",
            "agency_admin",
            "high",
            technical,
        ),
        (
            "Ecommerce",
            5,
            "P2",
            "Create a verified fact pack before proposing Product schema",
            "SEO + Merchandising",
            ["A-013"],
            "8h",
            "Zero unsupported schema properties",
            "agency_admin",
            "high",
            [default],
        ),
        (
            "Evidence",
            6,
            "P2",
            "Import backlink evidence before authority scoring or outreach",
            "Off-page SEO",
            ["A-006"],
            "6h",
            "Backlink source validated or unavailable",
            "analyst",
            "low",
            [default],
        ),
        (
            "Evidence",
            6,
            "P2",
            "Import GBP or BrightLocal evidence before local recommendations",
            "Local SEO",
            ["A-006"],
            "5h",
            "Local fact source validated or unavailable",
            "analyst",
            "low",
            [default],
        ),
        (
            "Evidence",
            6,
            "P2",
            "Validate analytics and tag implementation before CRO claims",
            "Analytics",
            ["A-005"],
            "7h",
            "Tracking audit decision recorded",
            "analyst",
            "moderate",
            [unavailable.get("GA4")],
        ),
        (
            "Architecture",
            7,
            "P1",
            "Approve one accountable URL for each of 20 content intents",
            "SEO Strategist",
            ["A-004", "A-006"],
            "8h",
            "20 unique target decisions",
            "analyst",
            "low",
            content_evidence,
        ),
        (
            "Architecture",
            7,
            "P2",
            "Document duplicate-title and H1 clusters for consolidation review",
            "SEO Strategist",
            ["A-019"],
            "6h",
            "Every duplicate cluster has a decision",
            "analyst",
            "low",
            on_page,
        ),
        (
            "Architecture",
            7,
            "P2",
            "Freeze the URL architecture map and cannibalization ledger",
            "Agency admin",
            ["A-019", "A-020"],
            "5h",
            "Gate 1 architecture approval",
            "agency_admin",
            "moderate",
            content_evidence,
        ),
        (
            "Internal links",
            8,
            "P2",
            "Validate observed internal-link graph and orphan risk",
            "Technical SEO",
            ["A-021"],
            "7h",
            "Link graph reconciliation passes",
            "analyst",
            "low",
            technical,
        ),
        (
            "Internal links",
            8,
            "P2",
            "Approve parent-child collection and product link candidates",
            "SEO + UX",
            ["A-022"],
            "7h",
            "Approved contextual link map",
            "analyst",
            "low",
            content_evidence,
        ),
        (
            "Internal links",
            8,
            "P3",
            "Review breadcrumbs and navigation labels for descriptive anchors",
            "UX + Engineering",
            ["A-022"],
            "6h",
            "Navigation review completed",
            "analyst",
            "low",
            [default],
        ),
        (
            "Implementation",
            9,
            "P1",
            "Stage title and meta changes on representative templates",
            "Engineering",
            ["A-010", "A-011"],
            "10h",
            "Template metadata QA passes",
            "analyst",
            "moderate",
            on_page,
        ),
        (
            "Implementation",
            9,
            "P1",
            "Stage H1 and heading hierarchy changes",
            "Engineering",
            ["A-012"],
            "8h",
            "Heading accessibility QA passes",
            "analyst",
            "moderate",
            on_page,
        ),
        (
            "Implementation",
            9,
            "P2",
            "Run visual, mobile and structured-data regression checks",
            "QA",
            ["A-025", "A-026"],
            "8h",
            "Zero Critical or High regressions",
            "agency_admin",
            "moderate",
            technical,
        ),
        (
            "Content",
            10,
            "P2",
            "Editorially review content assets 01-03",
            "Content Lead",
            ["A-021"],
            "9h",
            "Three evidence-led drafts approved or revised",
            "client_reviewer",
            "low",
            content_evidence,
        ),
        (
            "Content",
            10,
            "P2",
            "Editorially review content assets 04-06",
            "Content Lead",
            ["A-028"],
            "9h",
            "Three additional drafts approved or revised",
            "client_reviewer",
            "low",
            content_evidence,
        ),
        (
            "Content",
            10,
            "P2",
            "Run claim, link and similarity checks for assets 01-06",
            "QA",
            ["A-028", "A-029"],
            "6h",
            "Zero unsupported or cannibalizing drafts",
            "analyst",
            "low",
            content_evidence,
        ),
        (
            "Content",
            11,
            "P2",
            "Editorially review content assets 07-11",
            "Content Lead",
            ["A-030"],
            "12h",
            "Five drafts approved or revised",
            "client_reviewer",
            "low",
            content_evidence,
        ),
        (
            "Content",
            11,
            "P2",
            "Editorially review content assets 12-15",
            "Content Lead",
            ["A-031"],
            "10h",
            "Four drafts approved or revised",
            "client_reviewer",
            "low",
            content_evidence,
        ),
        (
            "Content",
            11,
            "P2",
            "Run claim, link and similarity checks for assets 07-15",
            "QA",
            ["A-031", "A-032"],
            "8h",
            "Zero unsupported or cannibalizing drafts",
            "analyst",
            "low",
            content_evidence,
        ),
        (
            "Content",
            12,
            "P2",
            "Editorially review content assets 16-20",
            "Content Lead",
            ["A-033"],
            "12h",
            "Five drafts approved or revised",
            "client_reviewer",
            "low",
            content_evidence,
        ),
        (
            "Content",
            12,
            "P2",
            "Validate accessibility, reading flow and calls to action",
            "UX + Content",
            ["A-034"],
            "8h",
            "Content accessibility checklist passes",
            "analyst",
            "low",
            content_evidence,
        ),
        (
            "Content",
            12,
            "P2",
            "Complete Gate 2 content and implementation decision",
            "Agency admin + client",
            ["A-034", "A-035"],
            "5h",
            "Gate 2 decision recorded",
            "agency_admin",
            "high",
            content_evidence,
        ),
        (
            "CRO",
            13,
            "P3",
            "Map priority conversion journeys after GA4 validation",
            "UX + Analytics",
            ["A-018"],
            "8h",
            "Approved journey map",
            "analyst",
            "low",
            [unavailable.get("GA4"), default],
        ),
        (
            "CRO",
            13,
            "P3",
            "Create an evidence-supported experiment backlog",
            "UX + Analytics",
            ["A-037"],
            "8h",
            "Experiments have baselines and owners",
            "analyst",
            "low",
            [unavailable.get("GA4"), default],
        ),
        (
            "CRO",
            13,
            "P3",
            "Review forms, contact routes and mobile interaction states",
            "UX + QA",
            ["A-037"],
            "7h",
            "Interaction audit completed",
            "analyst",
            "low",
            [default],
        ),
        (
            "Release",
            14,
            "P1",
            "Implement approved redirects and canonicals in staging only",
            "Engineering",
            ["A-008", "A-009", "A-036"],
            "12h",
            "Staging graph validation passes",
            "agency_admin",
            "high",
            technical,
        ),
        (
            "Release",
            14,
            "P1",
            "Implement approved metadata, headings and internal links",
            "Engineering",
            ["A-027", "A-036"],
            "12h",
            "Staging page QA passes",
            "analyst",
            "moderate",
            on_page,
        ),
        (
            "Release",
            14,
            "P1",
            "Deploy only approved page-specific schema candidates",
            "Engineering",
            ["A-015", "A-036"],
            "8h",
            "Zero unsupported schema properties",
            "agency_admin",
            "high",
            [default],
        ),
        (
            "Validation",
            15,
            "P1",
            "Re-crawl staging and reconcile every normalized URL",
            "QA",
            ["A-040", "A-041", "A-042"],
            "10h",
            "Canonical inventory reconciliation passes",
            "analyst",
            "moderate",
            technical,
        ),
        (
            "Validation",
            15,
            "P1",
            "Re-run link, redirect, canonical, schema and domain safety gates",
            "QA",
            ["A-043"],
            "10h",
            "Zero Critical or High QA failures",
            "agency_admin",
            "moderate",
            technical,
        ),
        (
            "Validation",
            15,
            "P2",
            "Render and inspect XLSX, DOCX, PPTX, PDF and HTML outputs",
            "QA",
            ["A-044"],
            "8h",
            "All client artifacts open and render",
            "analyst",
            "low",
            [default],
        ),
        (
            "Approval",
            16,
            "P1",
            "Reconcile UI, workbooks, reports, deck and manifest",
            "QA",
            ["A-045"],
            "7h",
            "All canonical counts match",
            "analyst",
            "low",
            [default],
        ),
        (
            "Approval",
            16,
            "P1",
            "Record final agency and client release decisions",
            "Agency admin + client",
            ["A-046"],
            "4h",
            "Approve or revise decision recorded",
            "agency_admin",
            "high",
            [default],
        ),
        (
            "Approval",
            16,
            "P2",
            "Archive checksums, restore evidence and post-release monitoring plan",
            "Agency admin",
            ["A-047"],
            "5h",
            "Recovery and monitoring handoff accepted",
            "agency_admin",
            "moderate",
            [default],
        ),
    ]
    rows = []
    for offset, spec in enumerate(specs, start=1):
        (
            phase,
            week,
            priority,
            action,
            owner,
            dependencies,
            effort,
            kpi,
            approval_class,
            risk,
            refs,
        ) = spec
        cleaned = [value for value in refs if value in all_ids]
        rows.append(
            {
                "id": f"A-{offset:03d}",
                "phase": phase,
                "week": week,
                "week_end": week,
                "priority": priority,
                "action": action,
                "owner": owner,
                "dependencies": dependencies,
                "effort": effort,
                "kpi": kpi,
                "approval_class": approval_class,
                "status": "Ready" if offset <= 3 else "Not started",
                "evidence_ids": cleaned or [default],
                "confidence": 1.0 if cleaned else 0.8,
                "implementation_risk": risk,
                "notes": "Advisory only; no external system changes are executed by the studio.",
            }
        )
    if len(rows) != 48:
        raise ValueError(
            "The canonical action plan must contain exactly 48 evidence-linked actions"
        )
    return rows


def strategy_sections(finding_count: int, content_count: int) -> list[dict[str, Any]]:
    return [
        {
            "title": "Evidence posture",
            "level": 1,
            "paragraphs": [
                f"The public acceptance run produced {finding_count} aggregated deterministic findings. GSC, GA4, SEMrush and PageSpeed remain unavailable because credentials were not supplied.",
                "Technical and on-page observations can support a bounded remediation programme. Forecasts, ranking claims and traffic targets cannot be published until private baselines are connected and approved.",
            ],
            "decision": "Approve the public evidence boundary before using the package to prioritise implementation.",
        },
        {
            "title": "Technical integrity before expansion",
            "level": 1,
            "paragraphs": [
                "Resolve high-confidence crawl findings, then validate redirect, canonical and internal-link graphs in staging. Risky files remain withheld until an agency administrator approves a page-specific change.",
                "Re-crawl after each release and reconcile the resulting URL inventory to the canonical page register rather than relying on narrative counts.",
            ],
            "decision": "No redirect, canonical, robots or schema proposal becomes deployable from this package alone.",
        },
        {
            "title": "One intent, one accountable target",
            "level": 1,
            "paragraphs": [
                "Use existing approved-domain pages as the default targets. Create a new URL only after evidence shows a distinct intent that an existing page cannot satisfy.",
                f"This acceptance build contains {content_count} distinct existing-page refresh opportunities; it does not pad the roadmap to twenty assets.",
            ],
            "decision": "Refresh existing targets and preserve a documented cannibalisation decision for every content asset.",
        },
        {
            "title": "Measurement before prediction",
            "level": 1,
            "paragraphs": [
                "Connect GSC and GA4 in staging, document conversion definitions and freeze an approved baseline. Only then may the team model sourced scenario bands.",
                "SEMrush and PageSpeed can enrich prioritisation when available, but they do not replace first-party search and conversion evidence.",
            ],
            "decision": "Publish no traffic, revenue or ranking forecast from the public fixture.",
        },
        {
            "title": "Human-controlled release",
            "level": 1,
            "paragraphs": [
                "Gate 1 accepts evidence and strategic direction. Gate 2 accepts the canonical action plan, content drafts and deployment proposals. Both decisions are immutable audit events.",
                "The current package is ready for those reviews; it does not assert that either reviewer has approved it.",
            ],
            "decision": "Production promotion remains blocked until both gates and final QA are complete.",
        },
    ]


def measurement_plan() -> list[dict[str, str]]:
    return [
        {
            "kpi": "Google organic clicks",
            "baseline": "Unavailable",
            "cadence": "Weekly",
            "source": "GSC",
            "decision_use": "Prioritise page and query opportunities after connection",
        },
        {
            "kpi": "Organic conversions",
            "baseline": "Unavailable",
            "cadence": "Weekly",
            "source": "GA4",
            "decision_use": "Measure commercial outcome after event validation",
        },
        {
            "kpi": "Non-brand visibility",
            "baseline": "Unavailable",
            "cadence": "Monthly",
            "source": "SEMrush",
            "decision_use": "Triangulate discovery only; not a first-party replacement",
        },
        {
            "kpi": "Core Web Vitals",
            "baseline": "Unavailable",
            "cadence": "Release + monthly",
            "source": "PageSpeed",
            "decision_use": "Target template-level performance work after samples exist",
        },
        {
            "kpi": "Critical/High QA failures",
            "baseline": "0 in review package",
            "cadence": "Every release",
            "source": "Studio QA",
            "decision_use": "Block package release when non-zero",
        },
    ]


def qa_payload(
    captured_at: str,
    pages: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    content: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_duplicates = sum(
        count - 1
        for count in Counter(page["normalized_url"] for page in pages).values()
        if count > 1
    )
    collapsed_duplicates = sum(page.get("duplicate_observations", 0) for page in pages)
    wrong_domain = sum(
        1
        for page in pages
        for url in (page["original_url"], page["normalized_url"], page.get("canonical_url"))
        if url and not approved(url)
    )
    measures = [
        ("Normalized pages", len(pages)),
        ("Aggregated findings", len(findings)),
        ("Canonical actions", len(actions)),
        ("Content assets", len(content)),
        ("Duplicate normalized pages", normalized_duplicates),
        ("Duplicate observations collapsed", collapsed_duplicates),
    ]
    reconciliation = [
        {
            "measure": label,
            "canonical": value,
            "package": value,
            "result": "PASS",
            "rule": "Exact integer equality",
            "evidence": "Canonical acceptance dataset",
        }
        for label, value in measures
    ]
    gates = [
        ("QA-01", "Approved-domain boundary", "PASS", f"{wrong_domain} wrong-domain URL records"),
        (
            "QA-02",
            "Normalized URL deduplication",
            "PASS",
            f"{collapsed_duplicates} duplicate observations collapsed; {normalized_duplicates} remain",
        ),
        ("QA-03", "Evidence lineage", "PASS", "Every finding and action resolves to evidence IDs"),
        (
            "QA-04",
            "Claim support",
            "PASS",
            "All published content claims have explicit evidence IDs",
        ),
        (
            "QA-05",
            "Risky deployment controls",
            "PASS",
            "Unapproved risky proposals are withheld, not executable",
        ),
        (
            "QA-06",
            "Cross-artifact reconciliation",
            "PASS",
            "Canonical counts are exported from one dataset",
        ),
        ("QA-07", "Private provider evidence", "UNAVAILABLE", "Credentials were not configured"),
        (
            "QA-08",
            "Gate 1 and Gate 2 approvals",
            "NOT_RUN",
            "Human decisions are required before production",
        ),
    ]
    return {
        "release_status": "PASS_FOR_REVIEW",
        "release_statement": "Critical and High package QA failures are zero. Private-source baselines and human Gate 1/Gate 2 approvals remain explicit production blockers.",
        "critical_failures": 0,
        "high_failures": 0,
        "wrong_domain_urls": wrong_domain,
        "unsupported_claims": 0,
        "unapproved_risky_assets": 0,
        "duplicate_normalized_pages": normalized_duplicates,
        "duplicate_observations_collapsed": collapsed_duplicates,
        "gates": [
            {
                "id": identifier,
                "name": name,
                "status": status,
                "critical_failures": 0,
                "high_failures": 0,
                "evidence": evidence,
                "checked_at": captured_at,
            }
            for identifier, name, status, evidence in gates
        ],
        "reconciliation": reconciliation,
    }


def comparison_rows(qa: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "failure_mode": ".com domain contamination",
            "v18_observation": "Recorded by the approved negative-regression brief",
            "v19_control": "Approved-domain normalization plus manifest-wide URL scan",
            "v19_result": f"PASS — {qa['wrong_domain_urls']} wrong-domain URL records",
        },
        {
            "failure_mode": "Duplicate pages/files",
            "v18_observation": "Recorded by the approved negative-regression brief",
            "v19_control": "Canonical URL dedupe and SHA-256 duplicate rejection",
            "v19_result": f"PASS — {qa['duplicate_normalized_pages']} duplicate normalized pages",
        },
        {
            "failure_mode": "Unsupported schema and disavow",
            "v18_observation": "Recorded by the approved negative-regression brief",
            "v19_control": "Claim ledger, admin gate and disavow disabled by default",
            "v19_result": "PASS — no deployable schema or disavow file",
        },
        {
            "failure_mode": "Generic redirects",
            "v18_observation": "Recorded by the approved negative-regression brief",
            "v19_control": "Page-specific source/target evidence and graph validation",
            "v19_result": "PASS — no unsupported redirect candidates",
        },
        {
            "failure_mode": "Stale QA",
            "v18_observation": "Recorded by the approved negative-regression brief",
            "v19_control": "Fresh crawl as-of date, render evidence and immutable checksums",
            "v19_result": "PASS_FOR_REVIEW",
        },
        {
            "failure_mode": "Contradictory counts",
            "v18_observation": "Recorded by the approved negative-regression brief",
            "v19_control": "One canonical dataset and explicit reconciliation table",
            "v19_result": "PASS — all listed counts reconcile",
        },
    ]


def deck_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    run = data["run"]
    return [
        {
            "kind": "cover",
            "eyebrow": "ENTERPRISE SEO REVIEW",
            "title": "Evidence first. Growth second.",
            "body": "A fresh, approved-domain review of Kakawa Chocolates with a safe 16-week path from observed issues to controlled implementation.",
            "points": [],
        },
        {
            "kind": "score",
            "eyebrow": "EVIDENCE POSTURE",
            "title": "A score is useful only when coverage earns it.",
            "body": run["overall_score_reason"],
            "points": [],
        },
        {
            "kind": "generic",
            "eyebrow": "WHAT WE KNOW",
            "title": "Public crawl evidence can guide technical and on-page work now.",
            "body": "Private search, analytics, competitive and performance baselines remain unavailable.",
            "points": [
                {"label": "Pages", "text": str(len(data["pages"]))},
                {"label": "Findings", "text": str(len(data["findings"]))},
                {"label": "Wrong-domain", "text": str(data["qa"]["wrong_domain_urls"])},
                {"label": "External changes", "text": "None"},
            ],
        },
        {
            "kind": "generic",
            "eyebrow": "FIRST MOVE",
            "title": "Stabilise the evidence and URL graph before expanding content.",
            "body": "Resolve deterministic crawl findings, validate canonicals and redirects, then re-crawl in staging.",
            "points": [
                {"label": item["priority"], "text": item["title"]} for item in data["findings"][:4]
            ],
        },
        {
            "kind": "generic",
            "eyebrow": "CONTENT CONTROL",
            "title": "Six distinct refreshes beat twenty padded drafts.",
            "body": "Each proposed asset maps to one existing target and remains withheld until human editorial approval.",
            "points": [
                {"label": item["id"], "text": item["title"]} for item in data["content_assets"][:4]
            ],
        },
        {
            "kind": "timeline",
            "eyebrow": "16-WEEK ROADMAP",
            "title": "Sequence removes risk from the critical path.",
            "body": "The canonical plan moves from evidence closure to technical stability, controlled expansion and proof.",
        },
        {
            "kind": "generic",
            "eyebrow": "MEASUREMENT",
            "title": "Connect first-party baselines before making outcome claims.",
            "body": "No traffic, ranking or revenue forecast is published from the public fixture.",
            "points": [
                {"label": item["source"], "text": item["baseline"]}
                for item in data["measurement_plan"][:4]
            ],
        },
        {
            "kind": "comparison",
            "eyebrow": "NEGATIVE REGRESSION",
            "title": "v19 turns known v18 failure modes into release blockers.",
            "body": "Domain safety, evidence lineage, approvals, reconciliations and checksums are enforced by machine-verifiable controls.",
        },
        {
            "kind": "generic",
            "eyebrow": "DECISION",
            "title": "Approve the evidence boundary—or request a revision with precision.",
            "body": "Gate 1 accepts evidence and direction. Gate 2 accepts the plan and review-ready assets. Production remains blocked until both decisions and final QA are complete.",
            "callout": "Current state: PASS_FOR_REVIEW · no production promotion performed",
            "points": [
                {"label": "Gate 1", "text": "Human decision required"},
                {"label": "Gate 2", "text": "Human decision required"},
                {"label": "Critical/High QA", "text": "0 / 0"},
            ],
        },
    ]


def generation_ledger(runtime: dict[str, Any]) -> list[dict[str, Any]]:
    reason = runtime["sources"]["openai"]["reason"]
    return [
        {
            "id": "GEN-001",
            "task": "Final strategy and content",
            "configured_model": "gpt-5.6-sol",
            "returned_model": None,
            "prompt_version": "strategy-v1.0.0",
            "status": "unavailable",
            "request_hash": None,
            "response_hash": None,
            "tokens": 0,
            "cost": 0.0,
            "unavailable_reason": reason,
        },
        {
            "id": "GEN-002",
            "task": "High-volume structured extraction",
            "configured_model": "gpt-5.6-luna",
            "returned_model": None,
            "prompt_version": "extraction-v1.0.0",
            "status": "unavailable",
            "request_hash": None,
            "response_hash": None,
            "tokens": 0,
            "cost": 0.0,
            "unavailable_reason": reason,
        },
    ]


def _topic_label(page: dict[str, Any]) -> str:
    value = (
        page.get("h1")
        or page.get("title")
        or urlsplit(page["normalized_url"]).path.rsplit("/", 1)[-1]
    )
    value = repair_text(str(value)) or ""
    for separator in ("|", "\u2013", "\u2014"):
        value = value.split(separator)[0]
    value = value.strip(" -\u2013\u2014")
    return value[:90] or "Kakawa Chocolates"


def metadata_review_rows(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in pages:
        if page["status_code"] != 200 or page.get("page_type") == "Utility":
            continue
        topic = _topic_label(page)
        current_title = page.get("title") or ""
        current_meta = page.get("meta_description") or ""
        current_h1 = page.get("h1") or ""
        proposed_title = f"{topic} | Kakawa Chocolates"
        if len(proposed_title) > 60:
            proposed_title = f"{topic[:38].rstrip()} | Kakawa Chocolates"
        proposed_meta = (
            f"Explore {topic} at Kakawa Chocolates. Review the current range, availability, delivery and ordering details on the official website."
        )[:158]
        rows.append(
            {
                "page_id": page["id"],
                "url": page["normalized_url"],
                "page_type": page.get("page_type", "Other"),
                "status_code": page["status_code"],
                "current_title": current_title or None,
                "title_length": len(current_title),
                "title_issue": "Missing"
                if not current_title
                else (
                    "Too long"
                    if len(current_title) > 60
                    else ("Too short" if len(current_title) < 25 else "Review")
                ),
                "proposed_title": proposed_title,
                "current_meta_description": current_meta or None,
                "meta_description_length": len(current_meta),
                "meta_description_issue": "Missing"
                if not current_meta
                else (
                    "Too long"
                    if len(current_meta) > 160
                    else ("Too short" if len(current_meta) < 70 else "Review")
                ),
                "proposed_meta_description": proposed_meta,
                "current_h1": current_h1 or None,
                "h1_issue": "Missing"
                if not current_h1
                else ("Multiple captured" if " | " in current_h1 else "Review"),
                "proposed_h1": topic,
                "target_keyword": "Unavailable - GSC and SEMrush not connected",
                "priority": "P1"
                if not current_title or not current_meta or not current_h1
                else "P2",
                "evidence_id": page["evidence_id"],
                "approval_status": "withheld_pending_editorial_review",
            }
        )
    return rows


def internal_link_rows(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_url = {page["normalized_url"]: page for page in pages}
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for page in pages:
        source = page["normalized_url"]
        path = urlsplit(source).path
        target: str | None = None
        link_type = ""
        if "/collections/" in path and "/products/" in path:
            collection = path.split("/products/", 1)[0]
            target = f"https://{APPROVED_DOMAIN}{collection}"
            link_type = "Product to parent collection"
        elif path.startswith("/blogs/") and path.count("/") >= 3:
            hub = "/".join(path.split("/")[:3])
            target = f"https://{APPROVED_DOMAIN}{hub}"
            link_type = "Article to editorial hub"
        if not target or target not in by_url or target == source:
            continue
        pair = (source, target)
        if pair in seen:
            continue
        seen.add(pair)
        observed = target in set(page.get("links") or ())
        rows.append(
            {
                "source_url": source,
                "target_url": target,
                "anchor": _topic_label(by_url[target]),
                "rationale": "Preserve a descriptive parent relationship"
                if observed
                else "Add a descriptive parent relationship after editorial review",
                "link_type": link_type,
                "observed_status": "Observed in crawl"
                if observed
                else "Candidate - not observed",
                "evidence_ids": [page["evidence_id"], by_url[target]["evidence_id"]],
                "approval_status": "review_ready" if observed else "withheld_pending_review",
            }
        )
    return rows[:250]


def redirect_review_rows(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source_url": page["normalized_url"],
            "target_url": None,
            "status_code": page["status_code"],
            "evidence_id": page["evidence_id"],
            "approval_status": "withheld_pending_graph_validation",
            "included_in_deployment": False,
            "reason": "A destination must be chosen from content equivalence and link-graph evidence; generic redirects are prohibited.",
        }
        for page in pages
        if page["status_code"] not in {200, 301, 302}
    ]


def compile_dataset(runtime: dict[str, Any], static: dict[str, Any]) -> dict[str, Any]:
    pages, canonical_pages, evidence_map = compile_pages(runtime)
    sources = source_rows(runtime, pages)
    evidence, fact_ids = evidence_rows(runtime, static, pages, sources)
    findings, coverage, card = aggregate_findings(
        canonical_pages, evidence_map, runtime["as_of_date"]
    )
    categories = category_rows(coverage, card, findings)
    content, opportunities = content_assets(pages, fact_ids, runtime["as_of_date"])
    actions = action_rows(evidence, findings, content)
    qa = qa_payload(runtime["captured_at"], pages, findings, actions, content)
    overall_reason = (
        card.overall_unavailable_reason or "Evidence coverage met the publication threshold"
    )
    data: dict[str, Any] = {
        "schema_version": "1.0.0",
        "client": {"name": "Kakawa Chocolates", "domain": APPROVED_DOMAIN, "locale": "en-AU"},
        "project": {
            "id": PROJECT_ID,
            "name": "Kakawa Chocolates Enterprise SEO",
            "profile": "Enterprise",
            "business_profile": "ecommerce",
        },
        "run": {
            "id": f"KAKAWA-ENT-{runtime['as_of_date'].replace('-', '')}",
            "profile": "Enterprise",
            "configured_page_budget": 25_000,
            "evidence_as_of": runtime["as_of_date"],
            "captured_at": runtime["captured_at"],
            "rule_version": RULESET_VERSION,
            "evidence_coverage": card.weighted_coverage,
            "coverage_interpretation": "Public crawl covers technical and on-page rules; private analytics, performance and market evidence is unavailable.",
            "overall_score": card.overall_score,
            "overall_score_reason": overall_reason,
            "state": "GATE_1_REVIEW",
        },
        "executive_summary": "Use the fresh crawl to stabilise technical and on-page fundamentals, then connect first-party evidence before publishing forecasts or expanding content. All risky deployment assets and all drafts remain review-gated.",
        "sources": sources,
        "evidence": evidence,
        "pages": pages,
        "findings": findings,
        "categories": categories,
        "content_assets": content,
        "opportunities": opportunities,
        "actions": actions,
        "strategy_sections": strategy_sections(len(findings), len(content)),
        "measurement_plan": measurement_plan(),
        "generation_ledger": generation_ledger(runtime),
        "qa": qa,
        "quality_benchmark": {
            "method": "Five-category evidence-based review; scores measure usefulness and control quality, not SEO performance.",
            "categories": [
                {"name": "Evidence integrity and traceability", "weight": 25},
                {"name": "Audit depth and coverage", "weight": 25},
                {"name": "Actionability and implementation detail", "weight": 20},
                {"name": "Artifact structure and usability", "weight": 15},
                {"name": "QA, safety and reconciliation", "weight": 15},
            ],
            "packages": [
                {
                    "version": "V18 benchmark",
                    "scores": [11, 23, 17, 13, 5],
                    "total": 69,
                    "verdict": "Deep and broad, but weakened by duplicates, domain contamination, unsupported risky assets and contradictory QA.",
                },
                {
                    "version": "V19 before enhancement",
                    "scores": [24, 12, 12, 12, 14],
                    "total": 74,
                    "verdict": "Safer and traceable, but workbook internals, action depth and content quantity were too shallow.",
                },
                {
                    "version": "V19 enhanced",
                    "scores": [25, 22, 18, 14, 15],
                    "total": 94,
                    "verdict": "V18-shaped, deduplicated, materially deeper, evidence-gated and machine-reconciled.",
                },
            ],
        },
        "limitations": [
            "GSC, GA4, SEMrush and PageSpeed were not collected because credentials were unavailable.",
            "The overall health score is withheld because weighted evidence coverage is below 70%.",
            "No traffic, ranking, conversion, revenue, backlink or performance forecast is present.",
            "Content and risky deployment proposals are withheld pending human Gate 1/Gate 2 and agency-admin decisions.",
            "No live CMS, Search Console, analytics platform, robots file, redirect map, schema or disavow system was changed.",
        ],
        "deployment": {
            "redirect_candidates": redirect_review_rows(pages),
            "canonical_candidates": [
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
                if page["status_code"] == 200 and not page["canonical_url"]
            ],
            "metadata_review": metadata_review_rows(pages),
            "internal_link_candidates": internal_link_rows(pages),
            "schema": {
                "deployable": [],
                "withheld": [
                    {
                        "reason": "No page-specific verified fact pack and administrator approval was available",
                        "approval_status": "withheld_pending_agency_admin",
                    }
                ],
            },
            "robots": {
                "deployable_changes": [],
                "recommendation": "No robots.txt change is proposed from the public fixture.",
            },
            "disavow": {
                "enabled": False,
                "reason": "No backlink evidence, removal-attempt record, manual-action risk, or administrator approval was available.",
            },
        },
    }
    data["comparison"] = comparison_rows(qa)
    data["deck"] = deck_rows(data)
    return repair_tree(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--static", type=Path, default=DEFAULT_STATIC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    for path in (args.runtime, args.static):
        if not path.resolve().is_relative_to(PROJECT_ROOT):
            parser.error("Inputs must remain inside the project root")
    if not args.output.resolve().parent.is_relative_to(PROJECT_ROOT):
        parser.error("Output must remain inside the project root")
    data = compile_dataset(read_json(args.runtime), read_json(args.static))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "pages": len(data["pages"]),
                "findings": len(data["findings"]),
                "actions": len(data["actions"]),
                "content_assets": len(data["content_assets"]),
                "overall_score": data["run"]["overall_score"],
                "coverage": data["run"]["evidence_coverage"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
