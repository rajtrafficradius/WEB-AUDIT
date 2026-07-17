"""Automatic website audit: crawl, evidence, findings, recommendations and actions."""
from decimal import Decimal
from urllib.parse import urlsplit

from celery import shared_task
from django.conf import settings
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
from .models import BusinessProfile
from .models import PageSnapshot as EnginePage
from .rules import RULESET_VERSION, AuditContext, run_rules

IMPACT = {"critical": 95, "high": 80, "medium": 60, "low": 35, "info": 15}
PENALTY = {"critical": 18, "high": 10, "medium": 5, "low": 2, "info": 0}


def _stage(run, name, status, **data):
    stage, _ = RunStage.objects.get_or_create(
        run=run, name=name, defaults={"sequence": 10 if name == "collecting" else 20}
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


def _advice(code):
    return {
        "technical.http_status": ("Repair the failing URL", "Restore the URL or propose one relevant approved-domain redirect."),
        "on_page.title": ("Write a distinct page title", "Create a concise, intent-aligned title and compare it with other crawled titles."),
        "on_page.meta_description": ("Add a useful meta description", "Draft an accurate page-specific search summary."),
        "on_page.h1": ("Correct the primary heading", "Use one clear H1 and lower-level headings for supporting sections."),
        "technical.canonical_boundary": ("Review the unsafe canonical", "Propose an approved-domain canonical for administrator review."),
        "technical.robots_directive": ("Confirm indexation intent", "Compare the directive with the page purpose before changing it."),
    }.get(code, ("Resolve the evidence-backed issue", "Apply the smallest safe correction and verify it in a new crawl."))


def _collect(run, result, captured):
    source = SourceSnapshot.objects.create(
        run=run, source_type="crawl",
        availability=AvailabilityStatus.AVAILABLE if result.pages else AvailabilityStatus.UNAVAILABLE,
        unavailable_reason="" if result.pages else "No approved-domain page was collected.",
        record_count=len(result.pages), captured_at=captured, locale=run.project.locale,
        scope=f"{len(result.pages)} fetched; {result.discovered_count} discovered; {result.stopped_reason}",
        rule_version=RULESET_VERSION, confidence=Decimal("1") if result.pages else Decimal("0"),
        metadata={"failure_count": len(result.failures), "stopped_reason": result.stopped_reason},
    )
    allowed = tuple(v.casefold().rstrip(".") for v in run.project.approved_domains)
    pages, evidence_map, seen = [], {}, set()
    for item in result.pages:
        host = (urlsplit(item.final_url).hostname or "").casefold().rstrip(".")
        if item.final_url in seen or not any(host == v or host.endswith("." + v) for v in allowed):
            continue
        seen.add(item.final_url)
        page = PageSnapshot.objects.create(
            run=run, source_snapshot=source, original_url=item.requested_url,
            normalized_url=item.final_url, domain=host, approved_domain=True,
            status_code=item.status_code, content_type=item.content_type or "",
            canonical_url=item.canonical_url or "",
            redirect_target_url=item.redirect_chain[-1] if len(item.redirect_chain) > 1 else "",
            robots_indexable=not bool({"noindex", "none"}.intersection(item.robots_directives)),
            title=item.title or "", meta_description=item.meta_description or "",
            h1=" | ".join(item.h1), content_sha256=item.body_sha256,
            captured_at=captured, locale=run.project.locale, scope="approved-domain crawl",
            rule_version=RULESET_VERSION, confidence=Decimal("1"),
            facts={"h1_values": list(item.h1), "robots_directives": list(item.robots_directives), "links": list(item.links)},
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
        ))
    return pages, evidence_map


def _create_findings(run, pages, evidence_map):
    context = AuditContext(
        project_id=str(run.project_id), pages=tuple(pages),
        allowed_domains=tuple(run.project.approved_domains),
        business_profile=_business_profile(run.project.business_type),
    )
    page_map = {v.normalized_url: v for v in run.pages.all()}
    for item in run_rules(context):
        finding = Finding.objects.create(
            run=run, page=page_map.get(item.affected_urls[0]) if item.affected_urls else None,
            category=item.category, code=item.rule_id, title=item.title,
            description=item.description, severity=item.severity.value,
            affected_count=max(1, len(item.affected_urls)),
            affected_share=Decimal(str(round(item.affected_share, 4))),
            score_penalty=PENALTY[item.severity.value], confidence=item.confidence,
            rule_version=item.rule_version,
        )
        finding.evidence.add(*[evidence_map[v] for v in item.evidence_ids if v in evidence_map])
        title, implementation = _advice(item.rule_id)
        impact = IMPACT[item.severity.value]
        risk = "medium" if item.risk.value == "moderate" else item.risk.value
        recommendation = Recommendation.objects.create(
            finding=finding, title=title, rationale=item.description,
            implementation=implementation, impact=max(1, min(5, round(impact / 20))),
            effort=2 if impact >= 60 else 1, risk_class=risk,
        )
        score = min(100, round(impact * .42 + item.confidence * 23 + item.affected_share * 15 + 12))
        ActionItem.objects.create(
            run=run, recommendation=recommendation, title=title, description=implementation,
            week=1 if impact >= 80 else (2 if impact >= 60 else 4),
            owner_label="SEO / web team", impact=impact,
            evidence_confidence=round(item.confidence * 100, 2),
            reach=round(item.affected_share * 100, 2), business_criticality=impact,
            dependency_urgency=70 if impact >= 80 else 40, effort=40 if impact >= 60 else 20,
            priority_score=score,
            priority_tier="P1" if score >= 75 else ("P2" if score >= 55 else ("P3" if score >= 35 else "P4")),
            risk_class=risk,
        )


@shared_task(bind=True, name="audit_engine.tasks.run_website_audit", queue="analysis",
             acks_late=True, reject_on_worker_lost=True)
def run_website_audit(self, run_id):
    run = AuditRun.objects.select_related("project").get(pk=run_id)
    if run.state not in {RunState.DRAFT, RunState.FAILED}:
        return {"run_id": run_id, "state": run.state, "idempotent": True}
    try:
        run.state, run.error_code, run.error_summary = RunState.COLLECTING, "", ""
        run.save(update_fields=["state", "error_code", "error_summary", "updated_at"])
        _stage(run, "collecting", StageStatus.RUNNING, message="Discovering pages")
        result = BoundedCrawler(CrawlConfig(
            allowed_domains=tuple(run.project.approved_domains),
            max_pages=settings.AUTO_AUDIT_PAGE_LIMIT, max_depth=4,
            max_duration_seconds=settings.AUTO_AUDIT_DURATION_SECONDS,
            request_timeout_seconds=12, min_host_delay_seconds=.2,
        )).crawl((f"https://{run.project.primary_domain}/",))
        captured = timezone.now()
        pages, evidence = _collect(run, result, captured)
        if not pages:
            raise RuntimeError("No pages were collected. Check robots.txt, DNS, or the website URL.")
        _stage(run, "collecting", StageStatus.SUCCEEDED, output_count=len(pages))
        run.state, run.source_cutoff_at = RunState.AUDITING, captured
        run.save(update_fields=["state", "source_cutoff_at", "updated_at"])
        _stage(run, "auditing", StageStatus.RUNNING, message="Applying evidence rules")
        _create_findings(run, pages, evidence)
        coverage = min(100, round(len(pages) * 100 / max(1, result.discovered_count), 2))
        penalty = sum(float(v) for v in run.findings.values_list("score_penalty", flat=True))
        run.evidence_coverage, run.confidence = Decimal(str(coverage)), Decimal("1")
        run.health_score = Decimal(str(max(0, round(100 - penalty, 2)))) if coverage >= 70 else None
        run.state, run.version = RunState.GATE_1_REVIEW, run.version + 1
        run.save(update_fields=["evidence_coverage", "confidence", "health_score", "state", "version", "updated_at"])
        _stage(run, "auditing", StageStatus.SUCCEEDED, output_count=run.findings.count())
        record_event(event_type="audit.ready_for_review", run=run, object_instance=run,
                     payload={"pages": len(pages), "findings": run.findings.count()})
        return {"run_id": run_id, "state": run.state, "pages": len(pages), "findings": run.findings.count()}
    except Exception as exc:
        active = run.stages.filter(status=StageStatus.RUNNING).order_by("-sequence").first()
        if active:
            active.status, active.error_code, active.error_summary = StageStatus.FAILED, "audit_execution_failed", str(exc)[:1000]
            active.finished_at, active.heartbeat_at = timezone.now(), timezone.now()
            active.save()
        run.state, run.error_code, run.error_summary = RunState.FAILED, "audit_execution_failed", str(exc)[:1000]
        run.version += 1
        run.save(update_fields=["state", "error_code", "error_summary", "version", "updated_at"])
        record_event(event_type="audit.failed", run=run, object_instance=run)
        raise