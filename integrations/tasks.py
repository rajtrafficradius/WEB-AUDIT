"""Celery entry point for provider enrichment.

The task name uses the ``studio.analysis.*`` prefix on purpose: that is the only
prefix routed to the queue a worker actually consumes.  A differently named task
would be accepted by the broker and then never executed.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from app.domain.constants import AvailabilityStatus, StageStatus
from app.domain.models import AuditRun, RunStage, SourceSnapshot

from .market_data import MAX_CACHE_DAYS, SOURCE_TYPE, MarketDataService

logger = logging.getLogger(__name__)

STAGE_NAME = "enriching"
STAGE_SEQUENCE = 15


def _stage(run: AuditRun, status: str, /, **checkpoint: Any) -> None:
    """Create or heartbeat the enrichment stage.  Never raises."""

    try:
        stage, _ = RunStage.objects.get_or_create(
            run=run, name=STAGE_NAME, defaults={"sequence": STAGE_SEQUENCE}
        )
        now = timezone.now()
        if status == StageStatus.RUNNING:
            stage.attempts += 1
            stage.started_at = stage.started_at or now
        else:
            stage.finished_at = now
        stage.status = status
        stage.heartbeat_at = now
        stage.checkpoint = {**(stage.checkpoint or {}), **checkpoint}
        stage.save()
    except Exception:  # noqa: BLE001 - stage telemetry must never fail a run
        logger.exception("Could not update the enrichment stage", extra={"run": str(run.pk)})


def _has_fresh_snapshot(run: AuditRun) -> bool:
    days = max(0, min(MAX_CACHE_DAYS, int(getattr(settings, "SEMRUSH_CACHE_DAYS", 30) or 0)))
    queryset = SourceSnapshot.objects.filter(run=run, source_type=SOURCE_TYPE)
    if days:
        cutoff = timezone.now() - timedelta(days=days)
        queryset = queryset.filter(created_at__gte=cutoff)
    return queryset.exists()


@shared_task(
    bind=True,
    name="studio.analysis.collect_market_data",
    queue="analysis",
    acks_late=True,
    reject_on_worker_lost=True,
)
def collect_market_data(self, run_id: str) -> dict[str, Any]:
    """Collect budgeted SEMrush market data for one run.  Never raises."""

    try:
        run = AuditRun.objects.select_related("project").get(pk=run_id)
    except Exception:  # noqa: BLE001 - a missing run is not a worker failure
        logger.warning("Market-data task received an unknown run", extra={"run": str(run_id)})
        return {"run_id": str(run_id), "status": "unavailable", "reason": "run_not_found"}

    if _has_fresh_snapshot(run):
        snapshot = (
            SourceSnapshot.objects.filter(run=run, source_type=SOURCE_TYPE)
            .order_by("-created_at")
            .first()
        )
        _stage(run, StageStatus.SUCCEEDED, idempotent=True)
        return {
            "run_id": str(run.pk),
            "status": (
                "available"
                if snapshot is not None and snapshot.availability == AvailabilityStatus.AVAILABLE
                else "unavailable"
            ),
            "idempotent": True,
        }

    _stage(run, StageStatus.RUNNING, message="Requesting budgeted market data")
    try:
        result = MarketDataService(run).collect()
    except Exception:  # noqa: BLE001 - the service is fail-open; this is belt and braces
        logger.exception("Market-data collection raised", extra={"run": str(run.pk)})
        result = None

    if result is None or result.status != "available":
        from .demo_market import collect_demo_market_data, demo_mode_enabled

        if demo_mode_enabled():
            # Demo fallback: the real pipeline runs against a simulated
            # transport so reports fill with site-derived, clearly-flagged
            # placeholder metrics until a working key takes over.
            reason = result.unavailable_reason if result is not None else "collector_error"
            logger.info(
                "Falling back to simulated market data", extra={"run": str(run.pk),
                                                                "reason": reason},
            )
            try:
                result = collect_demo_market_data(run)
            except Exception:  # noqa: BLE001 - demo data must never fail a run
                logger.exception("Demo market data raised", extra={"run": str(run.pk)})

    if result is None:
        _stage(run, StageStatus.SKIPPED, message="Market data could not be collected")
        return {"run_id": str(run.pk), "status": "unavailable", "reason": "collector_error"}

    if result.status == "available" and not result.referring_domains_persisted:
        from .demo_market import demo_mode_enabled, top_up_demo_refdomains

        if demo_mode_enabled():
            # A working key on the lite plan skips the costly refdomains
            # report; in demo mode simulate just that report so backlink
            # deliverables are never emptier than the demo they replace.
            try:
                result.referring_domains_persisted = top_up_demo_refdomains(run)
            except Exception:  # noqa: BLE001 - top-up must never fail the run
                logger.exception("Demo refdomain top-up raised", extra={"run": str(run.pk)})

    _stage(
        run,
        StageStatus.SUCCEEDED if result.status == "available" else StageStatus.SKIPPED,
        units_spent=result.units_spent,
        tier=result.tier,
        database=result.database,
        collection_status=result.status,
        unavailable_reason=result.unavailable_reason,
        output_count=result.keywords_persisted,
    )
    return {
        "run_id": str(run.pk),
        "status": result.status,
        "units_spent": result.units_spent,
        "keywords": result.keywords_persisted,
        "competitors": result.competitors_persisted,
        "referring_domains": result.referring_domains_persisted,
        "reason": result.unavailable_reason,
    }
