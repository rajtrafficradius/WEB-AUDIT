"""Automatic website audit: crawl, evidence, findings, recommendations and actions."""
from decimal import Decimal
from urllib.parse import urlsplit

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.domain.audit import record_event
from app.domain.constants import AvailabilityStatus, RunState, StageStatus
from app.domain.models import (
    ActionItem,
    AuditRun,
    Evidence,
    Finding,
    PageSnapshot,
    Recommendation,
    RunStage,
    SourceSnapshot,
)

from .crawler import BoundedCrawler, CrawlConfig
from .models import RUN_LIMITS, BusinessProfile, RunProfile
from .models import PageSnapshot as EnginePage
from .rules import RULESET_VERSION, AuditContext, CrawlIntegrity, run_rules
from .scoring import CATEGORY_WEIGHTS, scorecard

IMPACT = {"critical": 95, "high": 80, "medium": 60, "low": 35, "info": 15}
PENALTY = {"critical": 18, "high": 10, "medium": 5, "low": 2, "info": 0}
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
FINDING_EVIDENCE_CAP = 25

# Honest per-category evidence coverage for a crawl-only automatic audit.  Categories
# that the crawler cannot observe at all (paid tools, backlink indexes, CRO research)
# stay at 0 so the scorecard withholds rather than fabricates their scores.
CATEGORY_COVERAGE = {
    "technical": 1.0,
    "on_page": 1.0,
    "performance": 0.5,
    "analytics": 0.7,
    "geo_aeo": 0.8,
    "keyword_architecture": 0.5,
    "ecommerce": 0.7,
    "local": 0.6,
    "cro": 0.0,
    "authority": 0.0,
}


# Stage order is data, not a ternary: "highest-sequence RUNNING stage" lookups
# break silently when a new stage lands on the default sequence of an old one.
STAGE_SEQUENCE = {
    "collecting": 10,
    "auditing": 20,
    "enriching": 25,
    "packaging": 30,
}
DEFAULT_STAGE_SEQUENCE = 20
MARKET_DATA_TASK = "studio.analysis.collect_market_data"
QUARANTINE_URL_CAP = 50


def _stage(run, name, status, **data):
    stage, _ = RunStage.objects.get_or_create(
        run=run, name=name,
        defaults={"sequence": STAGE_SEQUENCE.get(name, DEFAULT_STAGE_SEQUENCE)},
    )
    now = timezone.now()
    if status == StageStatus.RUNNING:
        stage.attempts += 1
        stage.started_at = stage.started_at or now
    else:
        stage.finished_at = now
    stage.status, stage.heartbeat_at = status, now
    stage.checkpoint = {**(stage.checkpoint or {}), **data}
    stage.save()


def _business_profile(value):
    return {"local": BusinessProfile.LOCAL, "ecommerce": BusinessProfile.ECOMMERCE,
            "hybrid": BusinessProfile.HYBRID}.get(value, BusinessProfile.SERVICE_SAAS)


def _page_budget(run):
    try:
        profile = RunProfile(run.profile)
    except ValueError:
        profile = RunProfile.QUICK
    return max(1, min(RUN_LIMITS[profile].page_budget, settings.AUTO_AUDIT_PAGE_LIMIT))


def _advice(code):
    return {
        "technical.http_status": ("Repair the failing URL", "Restore the URL or propose one relevant approved-domain redirect."),
        "on_page.title": ("Write a distinct page title", "Create a concise, intent-aligned title and compare it with other crawled titles."),
        "on_page.meta_description": ("Add a useful meta description", "Draft an accurate page-specific search summary."),
        "on_page.h1": ("Correct the primary heading", "Use one clear H1 and lower-level headings for supporting sections."),
        "technical.crawl_degraded": ("Allow the audit crawler through bot protection", "Allow-list the audit crawler's user agent and source addresses in your WAF, CDN or rate-limit rules, then re-run the audit so the quarantined URLs can be assessed. No change to those pages is implied by this item."),
        "technical.canonical_boundary": ("Review the unsafe canonical", "Propose an approved-domain canonical for administrator review."),
        "technical.robots_directive": ("Confirm indexation intent", "Compare the directive with the page purpose before changing it."),
        "technical.redirect_chain_length": ("Flatten the redirect chain", "Point every internal link and redirect rule directly at the final destination URL so each request resolves in a single hop."),
        "technical.canonical_missing": ("Add a self-referencing canonical", "Declare a rel=\"canonical\" link on each page pointing at its own preferred URL to neutralise parameter and duplicate variants."),
        "technical.duplicate_content": ("Consolidate duplicate pages", "Select one canonical URL per duplicated body, 301-redirect or canonicalise the copies, and repoint internal links at the surviving URL."),
        "technical.insecure_internal_links": ("Upgrade internal links to HTTPS", "Rewrite internal hrefs that still use http:// to their https:// equivalents so no navigation path passes through an insecure hop."),
        "technical.broken_internal_link": ("Fix broken internal links", "Update or remove every link that targets a 4xx/5xx URL: restore the destination, or point the link at the closest live equivalent."),
        "technical.url_hygiene": ("Standardise URL formatting", "Adopt short, lowercase, hyphen-separated paths for new URLs and plan 301 redirects before renaming any live URL."),
        "technical.deep_page": ("Reduce click depth", "Surface deep pages through hub pages, category listings or contextual links so they sit within four clicks of the homepage."),
        "technical.orphan_page": ("Link to orphaned pages", "Add at least one crawlable internal link from a relevant indexed page to each orphan, or deliberately retire pages that no longer serve a purpose."),
        "on_page.title_duplicate": ("Differentiate duplicate titles", "Rewrite each affected title so it names that page's unique topic and intent; never reuse one title across URLs."),
        "on_page.meta_description_duplicate": ("Differentiate duplicate meta descriptions", "Write a page-specific summary for each affected URL that reflects its own content and call to action."),
        "on_page.h1_duplicate": ("Differentiate duplicate H1 headings", "Give each affected page a primary heading that states its own topic instead of repeating a shared heading."),
        "on_page.title_short": ("Expand the short title", "Extend the title into a descriptive 15-60 character phrase that pairs the page topic with its differentiator."),
        "on_page.thin_content": ("Strengthen thin content", "Expand the page until it fully answers its target intent, or consolidate it into a stronger related page with a 301 redirect."),
        "on_page.image_alt_missing": ("Add missing image alt text", "Write concise, descriptive alt attributes for every informative image and use empty alt only for purely decorative images."),
        "on_page.h2_missing": ("Structure long copy with H2 subheadings", "Break the page into scannable sections with descriptive H2 subheadings that reflect the questions the copy answers."),
        "performance.slow_response": ("Improve server response time", "Profile the slow endpoint, enable caching or a CDN, and reduce server work until HTML responds well under 1.2 seconds."),
        "performance.heavy_page": ("Reduce page weight", "Compress and lazy-load images, remove unused scripts and defer non-critical assets to bring the document under 1.5 MB."),
        "analytics.tracking_missing": ("Install site-wide analytics", "Deploy GA4, ideally via Google Tag Manager, across every template and verify events in DebugView before relying on the data."),
        "analytics.tracking_partial": ("Complete the analytics rollout", "Add the existing analytics container to every template that lacks it so measurement covers the whole site consistently."),
        "geo_aeo.structured_data_missing": ("Roll out structured data", "Implement JSON-LD appropriate to each template (Organization, WebSite, breadcrumbs, content types) and validate with the Rich Results test."),
        "geo_aeo.organization_schema_missing": ("Publish Organization schema", "Add a site-wide Organization or LocalBusiness JSON-LD block with legal name, logo, and sameAs links to verified profiles."),
        "geo_aeo.html_lang_missing": ("Declare the document language", "Set the html lang attribute (for example lang=\"en-AU\") in the base template of every page."),
        "keyword_architecture.title_cannibalization": ("Resolve title cannibalisation", "Assign each competing page a distinct primary keyword and rewrite its title, or consolidate overlapping pages into one authoritative URL."),
        "keyword_architecture.generic_title": ("Replace the generic title", "Write a title that leads with the page's primary topic and ends with the brand, instead of the brand name alone."),
        "ecommerce.product_schema_missing": ("Add Product structured data", "Emit Product JSON-LD with name, image, price, availability and review fields on every product template, then validate a sample."),
        "local.localbusiness_schema_missing": ("Publish LocalBusiness schema", "Add LocalBusiness JSON-LD with NAP details, geo coordinates and opening hours that match the Google Business Profile exactly."),
    }.get(code, ("Resolve the evidence-backed issue", "Apply the smallest safe correction and verify it in a new crawl."))


def _url_depth(url):
    return len([segment for segment in urlsplit(url).path.split("/") if segment])


def _failure_digest(result):
    """Summarise crawl failures instead of throwing them away."""

    codes, samples = {}, []
    for failure in result.failures:
        codes[failure.code] = codes.get(failure.code, 0) + 1
        if len(samples) < QUARANTINE_URL_CAP:
            samples.append({
                "url": failure.url,
                "code": failure.code,
                "message": failure.message[:300],
                "challenge": bool(getattr(failure, "challenge", False)),
                "challenge_kind": getattr(failure, "challenge_kind", None),
                "retry_after": getattr(failure, "retry_after", None),
            })
    return codes, samples


def _crawl_integrity_payload(fetched, challenged, rate_limited, quarantined):
    total = fetched + challenged
    share = round(challenged / total, 4) if total else 0.0
    if share > 0.30:
        status = "blocked"
        note = ("Bot protection answered most requests, so this audit covers only a "
                "minority of the site. Allow-list the audit crawler and re-run for full coverage.")
    elif share > 0.05:
        status = "degraded"
        note = ("Some URLs returned bot-challenge or rate-limit responses and were "
                "quarantined; they produced no findings.")
    else:
        status = "clean"
        note = "The crawl completed without material bot-protection interference."
    return {
        "status": status,
        "fetched_pages": fetched,
        "challenged_pages": challenged,
        "challenge_share": share,
        "rate_limited_pages": rate_limited,
        "quarantined_urls": list(quarantined[:QUARANTINE_URL_CAP]),
        "note": note,
    }


def _collect(run, result, captured):
    fetched_items = [item for item in result.pages if not getattr(item, "challenge", False)]
    challenged_items = [item for item in result.pages if getattr(item, "challenge", False)]
    failure_codes, failure_samples = _failure_digest(result)
    source = SourceSnapshot.objects.create(
        run=run, source_type="crawl",
        availability=AvailabilityStatus.AVAILABLE if fetched_items else AvailabilityStatus.UNAVAILABLE,
        unavailable_reason="" if fetched_items else "No approved-domain page was collected.",
        record_count=len(fetched_items), captured_at=captured, locale=run.project.locale,
        scope=f"{len(fetched_items)} fetched; {result.discovered_count} discovered; {result.stopped_reason}",
        rule_version=RULESET_VERSION, confidence=Decimal("1") if fetched_items else Decimal("0"),
        metadata={
            "failure_count": len(result.failures),
            "stopped_reason": result.stopped_reason,
            "discovered_count": result.discovered_count,
            "failure_codes": failure_codes,
            "failures": failure_samples,
            "challenged_count": len(challenged_items),
            "rate_limited_count": int(getattr(result, "rate_limited_count", 0)),
        },
    )
    allowed = tuple(v.casefold().rstrip(".") for v in run.project.approved_domains)
    pages, evidence_map, seen = [], {}, set()
    quarantined_urls = []
    for item in challenged_items:
        host = (urlsplit(item.final_url).hostname or "").casefold().rstrip(".")
        if item.final_url in seen or not any(host == v or host.endswith("." + v) for v in allowed):
            continue
        seen.add(item.final_url)
        quarantined_urls.append(item.final_url)
        reason = (
            f"The origin returned a {item.challenge_kind or 'bot challenge'} response "
            f"(HTTP {item.status_code}); the page content was never delivered to the crawler."
        )
        quarantined = PageSnapshot.objects.create(
            run=run, source_snapshot=source, original_url=item.requested_url,
            normalized_url=item.final_url, domain=host, approved_domain=True,
            status_code=item.status_code, content_type=item.content_type or "",
            canonical_url="", redirect_target_url="", robots_indexable=None,
            title="", meta_description="", h1="", content_sha256=item.body_sha256,
            response_ms=item.response_ms, captured_at=captured, locale=run.project.locale,
            scope="quarantined: bot challenge", rule_version=RULESET_VERSION,
            confidence=Decimal("0"),
            facts={
                "challenge": True,
                "challenge_kind": item.challenge_kind,
                "retry_after": item.retry_after,
                "availability": AvailabilityStatus.UNAVAILABLE,
                "unavailable_reason": reason,
                "url_depth": _url_depth(item.final_url),
            },
        )
        Evidence.objects.create(
            run=run, source_snapshot=source, page=quarantined,
            evidence_type="website_crawl_challenge",
            title=f"Quarantined challenge response: {item.final_url}",
            locator=item.final_url, sha256=item.body_sha256,
            details={"status_code": item.status_code, "challenge_kind": item.challenge_kind,
                     "retry_after": item.retry_after},
            availability=AvailabilityStatus.UNAVAILABLE, unavailable_reason=reason,
            captured_at=captured, locale=run.project.locale, scope="single URL",
            rule_version=RULESET_VERSION, confidence=Decimal("0"),
        )
    for item in fetched_items:
        host = (urlsplit(item.final_url).hostname or "").casefold().rstrip(".")
        if item.final_url in seen or not any(host == v or host.endswith("." + v) for v in allowed):
            continue
        seen.add(item.final_url)
        depth = _url_depth(item.final_url)
        page = PageSnapshot.objects.create(
            run=run, source_snapshot=source, original_url=item.requested_url,
            normalized_url=item.final_url, domain=host, approved_domain=True,
            status_code=item.status_code, content_type=item.content_type or "",
            canonical_url=item.canonical_url or "",
            redirect_target_url=item.redirect_chain[-1] if len(item.redirect_chain) > 1 else "",
            robots_indexable=not bool({"noindex", "none"}.intersection(item.robots_directives)),
            title=item.title or "", meta_description=item.meta_description or "",
            h1=" | ".join(item.h1), content_sha256=item.body_sha256,
            response_ms=item.response_ms,
            captured_at=captured, locale=run.project.locale, scope="approved-domain crawl",
            rule_version=RULESET_VERSION, confidence=Decimal("1"),
            facts={
                "h1_values": list(item.h1),
                "robots_directives": list(item.robots_directives),
                "links": list(item.links),
                "external_links": list(item.external_links),
                "word_count": int(item.word_count or 0),
                "body_bytes": int(item.body_bytes),
                "response_ms": item.response_ms,
                "images_total": int(item.images_total),
                "images_missing_alt": int(item.images_missing_alt),
                "schema_types": list(item.schema_types),
                "h2_values": list(item.h2),
                "og_title": bool(item.og_title),
                "og_description": bool(item.og_description),
                "lang": item.lang,
                "viewport": bool(item.viewport),
                "hreflang_count": int(item.hreflang_count),
                "analytics_tags": list(item.analytics_tags),
                "url_depth": depth,
            },
        )
        evidence = Evidence.objects.create(
            run=run, source_snapshot=source, page=page, evidence_type="website_crawl_page",
            title=f"Crawl observation: {item.final_url}", locator=item.final_url,
            sha256=item.body_sha256, details={"status_code": item.status_code, "title": item.title},
            availability=AvailabilityStatus.AVAILABLE, captured_at=captured,
            locale=run.project.locale, scope="single URL", rule_version=RULESET_VERSION,
            confidence=Decimal("1"),
        )
        evidence_map[str(evidence.pk)] = evidence
        pages.append(EnginePage(
            id=str(page.pk), project_id=str(run.project_id), original_url=item.requested_url,
            normalized_url=item.final_url, status_code=item.status_code, captured_at=captured,
            evidence_id=str(evidence.pk), title=item.title, meta_description=item.meta_description,
            h1=item.h1, canonical_url=item.canonical_url, robots_directives=item.robots_directives,
            content_type=item.content_type, body_sha256=item.body_sha256, links=item.links,
            word_count=item.word_count, body_bytes=item.body_bytes, response_ms=item.response_ms,
            images_total=item.images_total, images_missing_alt=item.images_missing_alt,
            schema_types=item.schema_types, h2=item.h2, external_links=item.external_links,
            og_title=item.og_title, og_description=item.og_description, lang=item.lang,
            viewport=item.viewport, hreflang_count=item.hreflang_count,
            analytics_tags=item.analytics_tags, url_depth=depth,
            redirect_chain=item.redirect_chain,
        ))
    integrity = _crawl_integrity_payload(
        len(pages), len(quarantined_urls),
        int(getattr(result, "rate_limited_count", 0)), quarantined_urls,
    )
    source.metadata = {**source.metadata, "crawl_integrity": integrity}
    source.save(update_fields=["metadata", "updated_at"])
    return pages, evidence_map, integrity


def _create_findings(run, pages, evidence_map, integrity=None):
    """Evaluate rules, then persist ONE grouped Finding/Recommendation/Action per rule."""

    integrity = integrity or {}
    quarantined = tuple(integrity.get("quarantined_urls") or ())
    context = AuditContext(
        project_id=str(run.project_id), pages=tuple(pages),
        allowed_domains=tuple(run.project.approved_domains),
        business_profile=_business_profile(run.project.business_type),
        challenged_urls=frozenset(quarantined),
        crawl_integrity=CrawlIntegrity(
            fetched_pages=int(integrity.get("fetched_pages") or 0),
            challenged_pages=int(integrity.get("challenged_pages") or 0),
            rate_limited_pages=int(integrity.get("rate_limited_pages") or 0),
            quarantined_urls=quarantined,
            note=str(integrity.get("note") or ""),
        ) if integrity else None,
    )
    engine_findings = run_rules(context)
    page_map = {v.normalized_url: v for v in run.pages.all()}
    grouped = {}
    for item in engine_findings:
        grouped.setdefault(item.rule_id, []).append(item)
    total_pages = max(1, len(pages))
    medium_index = low_index = 0
    for rule_id in sorted(grouped):
        group = grouped[rule_id]
        lead = min(group, key=lambda entry: SEVERITY_RANK[entry.severity.value])
        severity = lead.severity.value
        urls = list(dict.fromkeys(url for entry in group for url in entry.affected_urls))
        share = min(1.0, sum(entry.affected_share for entry in group))
        affected_count = max(1, len(urls), round(share * total_pages))
        confidence = min(entry.confidence for entry in group)
        evidence_ids = list(dict.fromkeys(
            evidence_id for entry in group for evidence_id in entry.evidence_ids
        ))[:FINDING_EVIDENCE_CAP]
        description = lead.description
        if len(group) > 1:
            description = f"{description} Observed on {affected_count} crawled pages."
        finding = Finding.objects.create(
            run=run, page=page_map.get(urls[0]) if urls else None,
            category=lead.category, code=rule_id, title=lead.title,
            description=description, severity=severity,
            affected_count=affected_count,
            affected_share=Decimal(str(round(share, 4))),
            score_penalty=PENALTY[severity],
            confidence=Decimal(str(round(confidence, 4))),
            rule_version=lead.rule_version,
        )
        finding.evidence.add(*[evidence_map[v] for v in evidence_ids if v in evidence_map])
        title, implementation = _advice(rule_id)
        impact = IMPACT[severity]
        risk = "medium" if lead.risk.value == "moderate" else lead.risk.value
        recommendation = Recommendation.objects.create(
            finding=finding, title=title, rationale=description,
            implementation=implementation, impact=max(1, min(5, round(impact / 20))),
            effort=2 if impact >= 60 else 1, risk_class=risk,
        )
        if severity in {"critical", "high"}:
            week = 1
        elif severity == "medium":
            week = 2 + medium_index % 3
            medium_index += 1
        else:
            week = 5 + low_index % 4
            low_index += 1
        score = min(100, round(impact * .42 + confidence * 23 + share * 15 + 12))
        ActionItem.objects.create(
            run=run, recommendation=recommendation, title=title, description=implementation,
            week=week, owner_label="SEO / web team", impact=impact,
            evidence_confidence=round(confidence * 100, 2),
            reach=round(share * 100, 2), business_criticality=impact,
            dependency_urgency=70 if impact >= 80 else 40, effort=40 if impact >= 60 else 20,
            priority_score=score,
            priority_tier="P1" if score >= 75 else ("P2" if score >= 55 else ("P3" if score >= 35 else "P4")),
            risk_class=risk,
        )
    return engine_findings


def _dispatch_market_data(run):
    """Queue the optional market/competitor enrichment between audit and packaging.

    Market data is an enhancement, never a precondition: a missing module, a dead
    broker or a provider outage must leave the completed audit untouched.
    """

    # MARKET_DATA_ENABLED is an explicit kill switch (default on); enrichment
    # only actually runs when a key resolves (per-project, organisation-wide,
    # or the environment fallback), so we skip the dispatch when none exists.
    if not getattr(settings, "MARKET_DATA_ENABLED", True):
        return False
    try:
        from integrations.market_data import MarketDataService

        if not MarketDataService.is_configured(run):
            return False
        from celery import current_app

        RunStage.objects.get_or_create(
            run=run, name="enriching",
            defaults={"sequence": STAGE_SEQUENCE["enriching"], "status": StageStatus.PENDING},
        )
        current_app.send_task(MARKET_DATA_TASK, args=[str(run.pk)], queue="analysis")
    except Exception:
        record_event(event_type="market_data.queue_failed", run=run, object_instance=run)
        return False
    return True


def _advance_run(run, **fields) -> bool:
    """Persist run fields ONLY if the run was not cancelled in the meantime.

    The crawl can take minutes; a user cancel during that window must win.
    Returns False (and leaves the row untouched) when the run is CANCELLED.
    """

    fields["updated_at"] = timezone.now()
    updated = (
        AuditRun.objects.filter(pk=run.pk)
        .exclude(state=RunState.CANCELLED)
        .update(**fields)
    )
    if updated:
        for key, value in fields.items():
            setattr(run, key, value)
    return bool(updated)


def _clear_prior_attempt(run) -> None:
    """Remove a failed attempt's partial evidence before a retry re-collects.

    PageSnapshot is unique per (run, normalized_url), so a rerun over
    leftovers would abort on the first duplicate insert.
    """

    run.findings.all().delete()
    run.pages.all().delete()
    run.evidence.all().delete()
    run.source_snapshots.filter(source_type="crawl").delete()


@shared_task(bind=True, name="audit_engine.tasks.run_website_audit", queue="analysis",
             acks_late=True, reject_on_worker_lost=True)
def run_website_audit(self, run_id):
    # Claim the run atomically: with two worker replicas, duplicate or
    # redelivered messages must not start a second concurrent audit.
    with transaction.atomic():
        run = (
            AuditRun.objects.select_for_update()
            .select_related("project")
            .get(pk=run_id)
        )
        if run.state not in {RunState.DRAFT, RunState.FAILED}:
            return {"run_id": run_id, "state": run.state, "idempotent": True}
        if run.state == RunState.FAILED:
            _clear_prior_attempt(run)
        run.state, run.error_code, run.error_summary = RunState.COLLECTING, "", ""
        run.save(update_fields=["state", "error_code", "error_summary", "updated_at"])
    try:
        _stage(run, "collecting", StageStatus.RUNNING, message="Discovering pages")
        result = BoundedCrawler(CrawlConfig(
            allowed_domains=tuple(run.project.approved_domains),
            max_pages=_page_budget(run), max_depth=5,
            max_duration_seconds=settings.AUTO_AUDIT_DURATION_SECONDS,
            request_timeout_seconds=12, min_host_delay_seconds=.2,
        )).crawl((f"https://{run.project.primary_domain}/",))
        captured = timezone.now()
        pages, evidence, integrity = _collect(run, result, captured)
        if not pages:
            raise RuntimeError(
                "No readable pages were collected. Check robots.txt, DNS, bot protection, "
                "or the website URL."
                if integrity["challenged_pages"]
                else "No pages were collected. Check robots.txt, DNS, or the website URL."
            )
        _stage(
            run, "collecting", StageStatus.SUCCEEDED, output_count=len(pages),
            crawl_integrity=integrity,
        )
        if not _advance_run(run, state=RunState.AUDITING, source_cutoff_at=captured):
            return {"run_id": run_id, "state": RunState.CANCELLED, "cancelled": True}
        _stage(run, "auditing", StageStatus.RUNNING, message="Applying evidence rules")
        engine_findings = _create_findings(run, pages, evidence, integrity)
        profile = _business_profile(run.project.business_type)
        card = scorecard(profile, engine_findings, CATEGORY_COVERAGE)
        run.evidence_coverage = Decimal(str(round(card.weighted_coverage * 100, 2)))
        run.confidence = Decimal("1")
        run.health_score = (
            Decimal(str(card.overall_score))
            if card.overall_score is not None and card.weighted_coverage >= 0.70
            else None
        )
        if not _advance_run(
            run,
            evidence_coverage=run.evidence_coverage,
            confidence=run.confidence,
            health_score=run.health_score,
            state=RunState.GATE_1_REVIEW,
            version=run.version + 1,
        ):
            return {"run_id": run_id, "state": RunState.CANCELLED, "cancelled": True}
        weights = CATEGORY_WEIGHTS[profile]
        _stage(
            run, "auditing", StageStatus.SUCCEEDED, output_count=run.findings.count(),
            scorecard=[
                {"category": item.category, "score": item.score,
                 "coverage": item.evidence_coverage,
                 "weight": float(weights.get(item.category, 0.0)),
                 "finding_count": item.finding_count}
                for item in card.categories
            ],
            stopped_reason=result.stopped_reason, discovered=result.discovered_count,
            crawl_integrity=integrity,
        )
        record_event(event_type="audit.ready_for_review", run=run, object_instance=run,
                     payload={"pages": len(pages), "findings": run.findings.count()})
        _dispatch_market_data(run)
        if getattr(settings, "AUTO_BUILD_PACKAGE", False):
            try:
                from exporters.tasks import build_audit_package

                RunStage.objects.get_or_create(
                    run=run, name="packaging",
                    defaults={"sequence": STAGE_SEQUENCE["packaging"]},
                )
                build_audit_package.delay(str(run.pk))
            except Exception:
                record_event(event_type="package.queue_failed", run=run, object_instance=run)
        return {"run_id": run_id, "state": run.state, "pages": len(pages), "findings": run.findings.count()}
    except Exception as exc:
        active = run.stages.filter(status=StageStatus.RUNNING).order_by("-sequence").first()
        if active:
            active.status, active.error_code, active.error_summary = StageStatus.FAILED, "audit_execution_failed", str(exc)[:1000]
            active.finished_at, active.heartbeat_at = timezone.now(), timezone.now()
            active.save()
        if _advance_run(
            run,
            state=RunState.FAILED,
            error_code="audit_execution_failed",
            error_summary=str(exc)[:1000],
            version=run.version + 1,
        ):
            record_event(event_type="audit.failed", run=run, object_instance=run)
        raise
