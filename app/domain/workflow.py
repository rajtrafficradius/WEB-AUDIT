"""Transactional audit-run state machine and approval gates."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from .audit import record_event
from .constants import ApprovalDecision, ApprovalGate, ReviewStatus, RunState
from .models import Approval, Artifact, AuditRun, Project, QAResult
from .permissions import can_approve_gate, can_manage_project, can_review_project


class WorkflowError(Exception):
    code = "workflow_error"


class InvalidTransition(WorkflowError):
    code = "invalid_transition"


class TransitionConflict(WorkflowError):
    code = "transition_conflict"


class ApprovalRequired(WorkflowError):
    code = "approval_required"


class QualityGateFailed(WorkflowError):
    code = "quality_gate_failed"


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    RunState.DRAFT: {RunState.COLLECTING, RunState.CANCELLED},
    RunState.COLLECTING: {RunState.AUDITING, RunState.FAILED, RunState.CANCELLED},
    RunState.AUDITING: {RunState.GATE_1_REVIEW, RunState.FAILED, RunState.CANCELLED},
    RunState.GATE_1_REVIEW: {RunState.PLANNING, RunState.REVISION_REQUESTED, RunState.CANCELLED},
    RunState.PLANNING: {RunState.GENERATING, RunState.FAILED, RunState.CANCELLED},
    RunState.GENERATING: {RunState.GATE_2_REVIEW, RunState.FAILED, RunState.CANCELLED},
    RunState.GATE_2_REVIEW: {RunState.FINAL_QA, RunState.REVISION_REQUESTED, RunState.CANCELLED},
    RunState.FINAL_QA: {
        RunState.PACKAGED,
        RunState.REVISION_REQUESTED,
        RunState.FAILED,
        RunState.CANCELLED,
    },
    RunState.PACKAGED: {RunState.APPROVED, RunState.REVISION_REQUESTED},
    RunState.REVISION_REQUESTED: {
        RunState.PLANNING,
        RunState.GENERATING,
        RunState.FINAL_QA,
        RunState.CANCELLED,
    },
    RunState.FAILED: {
        RunState.COLLECTING,
        RunState.AUDITING,
        RunState.PLANNING,
        RunState.GENERATING,
        RunState.FINAL_QA,
        RunState.CANCELLED,
    },
    RunState.APPROVED: set(),
    RunState.CANCELLED: set(),
}


PROFILE_LIMITS = {
    "quick": {"crawl_pages": 250, "pagespeed_samples": 10, "content_assets": 0},
    "standard": {"crawl_pages": 2500, "pagespeed_samples": 50, "content_assets": 10},
    "enterprise": {"crawl_pages": 25000, "pagespeed_samples": 200, "content_assets": 20},
}


def _has_approval(run: AuditRun, gate: str) -> bool:
    return run.approvals.filter(gate=gate, decision=ApprovalDecision.APPROVED).exists()


def _guard_transition(run: AuditRun, to_state: str) -> None:
    if (
        run.state == RunState.GATE_1_REVIEW
        and to_state == RunState.PLANNING
        and not _has_approval(run, ApprovalGate.GATE_1)
    ):
        raise ApprovalRequired("Gate 1 approval is required before planning")
    if (
        run.state == RunState.GATE_2_REVIEW
        and to_state == RunState.FINAL_QA
        and not _has_approval(run, ApprovalGate.GATE_2)
    ):
        raise ApprovalRequired("Gate 2 approval is required before final QA")
    if run.state == RunState.FINAL_QA and to_state == RunState.PACKAGED:
        blocking = run.qa_results.filter(
            status=QAResult.Status.FAIL,
            severity__in=("critical", "high"),
        ).exists()
        if blocking:
            raise QualityGateFailed(
                "Critical or high QA failures must be resolved before packaging"
            )
        unapproved_risky = (
            run.artifacts.filter(approval_required=True)
            .exclude(review_status=ReviewStatus.APPROVED)
            .exists()
        )
        if unapproved_risky:
            raise ApprovalRequired("All high-risk artifacts must be approved before packaging")
    if (
        run.state == RunState.PACKAGED
        and to_state == RunState.APPROVED
        and not _has_approval(run, ApprovalGate.PACKAGE)
    ):
        raise ApprovalRequired("Final package approval is required")


@transaction.atomic
def create_run_idempotent(
    *,
    project: Project,
    profile: str,
    idempotency_key: str,
    rule_version: str,
    actor,
    request=None,
) -> tuple[AuditRun, bool]:
    if not can_manage_project(actor, project):
        raise PermissionError("Project management permission is required")
    key = idempotency_key.strip()
    if not key or len(key) > 128:
        raise ValueError("A valid idempotency key is required")
    existing = AuditRun.objects.filter(project=project, idempotency_key=key).first()
    if existing:
        return existing, False
    try:
        run = AuditRun.objects.create(
            project=project,
            profile=profile,
            idempotency_key=key,
            rule_version=rule_version,
            created_by=actor,
        )
    except IntegrityError:
        run = AuditRun.objects.get(project=project, idempotency_key=key)
        return run, False
    record_event(
        event_type="run.created",
        actor=actor,
        request=request,
        object_instance=run,
        payload={"profile": profile},
    )
    return run, True


@transaction.atomic
def transition_run(
    *,
    run: AuditRun,
    to_state: str,
    actor,
    expected_version: int,
    request=None,
    reason: str = "",
) -> AuditRun:
    locked = (
        AuditRun.objects.select_for_update()
        .select_related("project", "project__client")
        .get(pk=run.pk)
    )
    if locked.version != expected_version:
        raise TransitionConflict("The run changed; refresh before trying again")
    if to_state not in ALLOWED_TRANSITIONS.get(locked.state, set()):
        raise InvalidTransition(f"Cannot transition from {locked.state} to {to_state}")
    if to_state == RunState.REVISION_REQUESTED:
        if not can_review_project(actor, locked.project):
            raise PermissionError("Review permission is required")
        if not reason.strip():
            raise ValueError("A revision reason is required")
    elif to_state == RunState.APPROVED:
        if not can_approve_gate(actor, locked, ApprovalGate.PACKAGE):
            raise PermissionError("Package approval permission is required")
    elif not can_manage_project(actor, locked.project):
        raise PermissionError("Project management permission is required")
    _guard_transition(locked, to_state)
    previous = locked.state
    locked.state = to_state
    locked.version += 1
    update_fields = ["state", "version", "updated_at"]
    if to_state == RunState.CANCELLED:
        locked.cancelled_at = timezone.now()
        update_fields.append("cancelled_at")
    if to_state == RunState.APPROVED:
        locked.completed_at = timezone.now()
        update_fields.append("completed_at")
    locked.save(update_fields=update_fields)
    record_event(
        event_type="run.transitioned",
        actor=actor,
        request=request,
        object_instance=locked,
        payload={"from": previous, "to": to_state, "reason": reason[:1000]},
    )
    return locked


@transaction.atomic
def decide_approval(
    *,
    approval: Approval,
    decision: str,
    actor,
    expected_run_version: int,
    comment: str = "",
    request=None,
) -> Approval:
    locked = (
        Approval.objects.select_for_update()
        .select_related("run__project", "artifact")
        .get(pk=approval.pk)
    )
    run = AuditRun.objects.select_for_update().get(pk=locked.run_id)
    if run.version != expected_run_version:
        raise TransitionConflict("The run changed; refresh before reviewing")
    if locked.decision != ApprovalDecision.PENDING:
        raise TransitionConflict("This approval has already been decided")
    if decision not in {
        ApprovalDecision.APPROVED,
        ApprovalDecision.REVISION_REQUESTED,
        ApprovalDecision.REJECTED,
    }:
        raise ValueError("A final approval decision is required")
    if not can_approve_gate(actor, locked.run, locked.gate):
        raise PermissionError("Approval permission is required")
    if decision != ApprovalDecision.APPROVED and not comment.strip():
        raise ValueError("A comment is required when approval is not granted")
    locked.decision = decision
    locked.reviewed_by = actor
    locked.decided_at = timezone.now()
    locked.comment = comment.strip()
    locked.save(update_fields=["decision", "reviewed_by", "decided_at", "comment", "updated_at"])
    if locked.artifact_id:
        Artifact.objects.filter(pk=locked.artifact_id).update(
            review_status=ReviewStatus.APPROVED
            if decision == ApprovalDecision.APPROVED
            else ReviewStatus.REVISION_REQUESTED,
            approved_at=timezone.now() if decision == ApprovalDecision.APPROVED else None,
        )
    run.version += 1
    run.save(update_fields=["version", "updated_at"])
    record_event(
        event_type="approval.decided",
        actor=actor,
        request=request,
        object_instance=run,
        payload={"approval_id": str(locked.pk), "gate": locked.gate, "decision": decision},
    )
    return locked
