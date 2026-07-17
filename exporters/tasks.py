"""Celery render tasks that persist immutable, tenant-scoped artifacts."""

from __future__ import annotations

import logging
from html import escape
from typing import Any

from celery import shared_task
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from app.domain.audit import record_event
from app.domain.constants import StageStatus
from app.domain.models import AuditRun, RunStage
from app.domain.permissions import can_access_project
from app.domain.storage import artifact_bytes_available, save_artifact_bytes

from .run_package import build_package_for_run

logger = logging.getLogger(__name__)

MAX_SUMMARY_ROWS = 100
PACKAGING_STAGE_NAME = "packaging"
PACKAGING_STAGE_SEQUENCE = 30


def _text(value: Any) -> str:
    return escape(str(value), quote=True)


def _finding_rows(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return '<tr><td colspan="4">No findings are recorded for this run.</td></tr>'
    return "".join(
        "<tr>"
        f"<td>{_text(item['severity']).title()}</td>"
        f"<td>{_text(item['category'])}</td>"
        f"<td>{_text(item['title'])}</td>"
        f"<td>{_text(item['affected_count'])}</td>"
        "</tr>"
        for item in findings
    )


def _action_rows(actions: list[dict[str, Any]]) -> str:
    if not actions:
        return '<tr><td colspan="4">No action items are recorded for this run.</td></tr>'
    return "".join(
        "<tr>"
        f"<td>{_text(item['priority_tier'])}</td>"
        f"<td>{_text(item['week'])}</td>"
        f"<td>{_text(item['title'])}</td>"
        f"<td>{_text(item['owner_label'] or 'Unassigned')}</td>"
        "</tr>"
        for item in actions
    )


def _render_run_summary(run: AuditRun) -> bytes:
    findings = list(
        run.findings.order_by("severity", "category", "title").values(
            "severity", "category", "title", "affected_count"
        )[:MAX_SUMMARY_ROWS]
    )
    actions = list(
        run.actions.order_by("week", "-priority_score", "title").values(
            "priority_tier", "week", "title", "owner_label"
        )[:MAX_SUMMARY_ROWS]
    )
    health_score = "Withheld — evidence coverage is below the publication threshold"
    if run.health_score is not None:
        health_score = f"{run.health_score}%"
    evidence_as_of = run.source_cutoff_at.date().isoformat() if run.source_cutoff_at else "Unavailable"
    document = f"""<!doctype html>
<html lang="{_text(run.project.locale)}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="robots" content="noindex,nofollow,noarchive">
  <title>{_text(run.project.name)} — enterprise SEO run summary</title>
  <style>
    :root {{ color-scheme: light; --ink:#13263a; --muted:#526579; --line:#d8e0e8;
      --paper:#fff; --wash:#f3f6f8; --accent:#d56f2b; }}
    * {{ box-sizing:border-box; }} body {{ margin:0; background:var(--wash); color:var(--ink);
      font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif; }}
    main {{ width:min(1120px,calc(100% - 32px)); margin:32px auto; background:var(--paper);
      border:1px solid var(--line); box-shadow:0 18px 50px rgba(19,38,58,.08); }}
    header,section {{ padding:clamp(24px,5vw,56px); }} header {{ border-top:8px solid var(--accent); }}
    h1 {{ max-width:18ch; margin:.2rem 0 1rem; font-size:clamp(2rem,5vw,4.5rem); line-height:1; }}
    h2 {{ margin-top:0; font-size:1.55rem; }} .eyebrow {{ color:var(--accent); font-weight:800;
      letter-spacing:.12em; text-transform:uppercase; }} .lede {{ max-width:70ch; color:var(--muted); }}
    dl {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:1px;
      margin:32px 0 0; background:var(--line); }} dl div {{ padding:18px; background:var(--wash); }}
    dt {{ color:var(--muted); font-size:.78rem; font-weight:700; letter-spacing:.08em;
      text-transform:uppercase; }} dd {{ margin:6px 0 0; font-weight:750; }}
    section {{ border-top:1px solid var(--line); }} .table-wrap {{ overflow-x:auto; }}
    table {{ width:100%; border-collapse:collapse; }} th,td {{ padding:12px; border-bottom:1px solid var(--line);
      text-align:left; vertical-align:top; }} th {{ background:var(--wash); font-size:.8rem;
      letter-spacing:.06em; text-transform:uppercase; }} footer {{ padding:20px 56px; color:var(--muted);
      border-top:1px solid var(--line); font-size:.85rem; }}
    @media (max-width:640px) {{ main {{ width:100%; margin:0; border:0; }} header,section {{ padding:24px; }}
      footer {{ padding:18px 24px; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <p class="eyebrow">Traffic Radius · controlled review artifact</p>
    <h1>{_text(run.project.name)}</h1>
    <p class="lede">Evidence-led run summary for {_text(run.project.client.name)}. This private artifact
      reports only canonical records captured by the studio and does not publish or modify a client website.</p>
    <dl>
      <div><dt>Run state</dt><dd>{_text(run.get_state_display())}</dd></div>
      <div><dt>Profile</dt><dd>{_text(run.get_profile_display())}</dd></div>
      <div><dt>Evidence as of</dt><dd>{_text(evidence_as_of)}</dd></div>
      <div><dt>Evidence coverage</dt><dd>{_text(run.evidence_coverage)}%</dd></div>
      <div><dt>Health score</dt><dd>{_text(health_score)}</dd></div>
      <div><dt>Approved domain</dt><dd>{_text(run.project.primary_domain)}</dd></div>
    </dl>
  </header>
  <section aria-labelledby="findings-title">
    <h2 id="findings-title">Findings</h2>
    <p class="lede">Showing up to {MAX_SUMMARY_ROWS} canonical findings. Total: {run.findings.count()}.</p>
    <div class="table-wrap"><table>
      <thead><tr><th scope="col">Severity</th><th scope="col">Category</th>
        <th scope="col">Finding</th><th scope="col">Affected</th></tr></thead>
      <tbody>{_finding_rows(findings)}</tbody>
    </table></div>
  </section>
  <section aria-labelledby="actions-title">
    <h2 id="actions-title">Action plan</h2>
    <p class="lede">Showing up to {MAX_SUMMARY_ROWS} canonical actions. Total: {run.actions.count()}.</p>
    <div class="table-wrap"><table>
      <thead><tr><th scope="col">Priority</th><th scope="col">Week</th>
        <th scope="col">Action</th><th scope="col">Owner</th></tr></thead>
      <tbody>{_action_rows(actions)}</tbody>
    </table></div>
  </section>
  <footer>Run {_text(run.pk)} · rule version {_text(run.rule_version)} · generated from canonical records</footer>
</main>
</body>
</html>
"""
    return document.encode("utf-8")


@shared_task(
    bind=True,
    name="studio.render.run_summary_html",
    queue="render",
    acks_late=True,
    reject_on_worker_lost=True,
)
def render_run_summary_html(
    self,
    run_id: str,
    idempotency_token: str,
    requested_by_id: str | None = None,
) -> dict[str, Any]:
    """Render one deterministic private HTML summary and persist it append-only."""
    token = idempotency_token.strip()
    if not token or len(token) > 128:
        raise ValueError("A bounded idempotency token is required")

    with transaction.atomic():
        run = (
            AuditRun.objects.select_for_update()
            .select_related("project__client", "created_by")
            .get(pk=run_id)
        )
        existing = run.artifacts.filter(
            artifact_type="run_summary_html",
            metadata__idempotency_token=token,
        ).first()
        if existing:
            return {
                "run_id": str(run.pk),
                "artifact_id": str(existing.pk),
                "sha256": existing.sha256,
                "idempotent": True,
            }

        actor = run.created_by
        if requested_by_id:
            actor = get_user_model().objects.filter(pk=requested_by_id).first()
            if actor is None or not can_access_project(actor, run.project):
                raise PermissionError("The requester cannot access this project")

        payload = _render_run_summary(run)
        artifact, _ = save_artifact_bytes(
            run=run,
            payload=payload,
            filename=f"{run.project.slug}-run-summary.html",
            title=f"{run.project.name} — run summary",
            artifact_type="run_summary_html",
            media_type="text/html; charset=utf-8",
            created_by=actor,
            metadata={
                "idempotency_token": token,
                "run_version": run.version,
                "task_id": str(getattr(self.request, "id", "") or ""),
            },
        )
        record_event(
            event_type="artifact.rendered",
            actor=actor,
            run=run,
            object_instance=artifact,
            payload={"artifact_type": artifact.artifact_type, "sha256": artifact.sha256},
        )
        return {
            "run_id": str(run.pk),
            "artifact_id": str(artifact.pk),
            "sha256": artifact.sha256,
            "idempotent": False,
        }


# Named under studio.analysis.* so it routes to the 'analysis' queue — the one
# queue every deployment shape consumes (the embedded single-service worker,
# a dedicated analysis worker, or the full multi-service topology). Routing it
# to 'render' left packages queued forever on deployments without a render
# consumer.
@shared_task(
    bind=True,
    name="studio.analysis.build_audit_package",
    acks_late=True,
    reject_on_worker_lost=True,
)
def build_audit_package(
    self,
    run_id: str,
    requested_by_id: str | None = None,
) -> dict[str, Any]:
    """Build the full deliverable package for a run, once per run version.

    A failed package build must never poison the audit run, so failures are
    recorded on the packaging stage and returned as data instead of re-raised.
    """
    run = AuditRun.objects.select_related("project__client", "created_by").get(pk=run_id)
    existing = (
        run.artifacts.filter(artifact_type="package", metadata__run_version=run.version)
        .order_by("-created_at")
        .first()
    )
    if existing and artifact_bytes_available(existing):
        return {
            "run_id": str(run.pk),
            "artifact_id": str(existing.pk),
            "sha256": existing.sha256,
            "idempotent": True,
        }

    stage, _ = RunStage.objects.get_or_create(
        run=run,
        name=PACKAGING_STAGE_NAME,
        defaults={"sequence": PACKAGING_STAGE_SEQUENCE},
    )
    now = timezone.now()
    stage.status = StageStatus.RUNNING
    stage.attempts += 1
    stage.started_at = stage.started_at or now
    stage.heartbeat_at = now
    stage.error_code = ""
    stage.error_summary = ""
    stage.checkpoint = {**(stage.checkpoint or {}), "message": "Preparing the deliverable package"}
    stage.save()

    def _progress(message: str) -> None:
        stage.checkpoint = {**(stage.checkpoint or {}), "message": str(message)[:500]}
        stage.heartbeat_at = timezone.now()
        stage.save(update_fields=["checkpoint", "heartbeat_at", "updated_at"])

    actor = run.created_by
    if requested_by_id:
        candidate = get_user_model().objects.filter(pk=requested_by_id).first()
        if candidate is not None and can_access_project(candidate, run.project):
            actor = candidate

    try:
        # The build runs outside any long transaction; artifact persistence
        # manages its own atomic block inside save_artifact_bytes.
        artifact, _manifest_row = build_package_for_run(run, actor=actor, progress=_progress)
    except Exception as exc:
        logger.exception("Package build failed for run %s", run_id)
        stage.status = StageStatus.FAILED
        stage.error_code = "package_build_failed"
        stage.error_summary = str(exc)[:1000]
        stage.finished_at = timezone.now()
        stage.heartbeat_at = stage.finished_at
        stage.save()
        record_event(
            event_type="package.failed",
            actor=actor,
            run=run,
            object_instance=run,
            payload={"error": str(exc)[:500]},
        )
        return {
            "run_id": str(run.pk),
            "artifact_id": None,
            "sha256": None,
            "idempotent": False,
            "failed": True,
            "error": str(exc)[:500],
        }

    stage.status = StageStatus.SUCCEEDED
    stage.finished_at = timezone.now()
    stage.heartbeat_at = stage.finished_at
    stage.checkpoint = {
        **(stage.checkpoint or {}),
        "message": "Package ready",
        "artifact_id": str(artifact.pk),
    }
    stage.save()
    return {
        "run_id": str(run.pk),
        "artifact_id": str(artifact.pk),
        "sha256": artifact.sha256,
        "idempotent": False,
    }
