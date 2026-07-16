# ruff: noqa: S106
from django.test import TestCase

from app.domain.constants import (
    ApprovalDecision,
    ApprovalGate,
    RunProfile,
    RunState,
    Severity,
    UserRole,
)
from app.domain.models import Approval, AuditRun, Client, Membership, Project, QAResult, User
from app.domain.workflow import (
    ApprovalRequired,
    QualityGateFailed,
    TransitionConflict,
    create_run_idempotent,
    transition_run,
)


class WorkflowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="workflow-admin",
            password="A-secure-workflow-admin-2026!",
            role=UserRole.AGENCY_ADMIN,
            must_change_password=False,
        )
        self.reviewer = User.objects.create_user(
            username="workflow-client",
            password="A-secure-workflow-reviewer-2026!",
            role=UserRole.CLIENT_REVIEWER,
            must_change_password=False,
        )
        client = Client.objects.create(name="Workflow Client", slug="workflow-client")
        self.project = Project.objects.create(
            client=client,
            name="Workflow",
            slug="workflow",
            primary_domain="example.com",
            approved_domains=["example.com"],
            business_type=Project.BusinessType.SERVICE,
        )
        Membership.objects.create(
            user=self.reviewer,
            client=client,
            project=self.project,
            access_role=UserRole.CLIENT_REVIEWER,
        )

    def test_run_creation_is_idempotent(self):
        first, created = create_run_idempotent(
            project=self.project,
            profile=RunProfile.ENTERPRISE,
            idempotency_key="request-1",
            rule_version="2026.07.1",
            actor=self.admin,
        )
        second, created_again = create_run_idempotent(
            project=self.project,
            profile=RunProfile.ENTERPRISE,
            idempotency_key="request-1",
            rule_version="2026.07.1",
            actor=self.admin,
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)

    def test_gate_one_blocks_planning_until_approved(self):
        run = AuditRun.objects.create(
            project=self.project,
            profile=RunProfile.ENTERPRISE,
            state=RunState.GATE_1_REVIEW,
            idempotency_key="gate-1",
            rule_version="2026.07.1",
            created_by=self.admin,
        )
        with self.assertRaises(ApprovalRequired):
            transition_run(
                run=run, to_state=RunState.PLANNING, actor=self.admin, expected_version=1
            )
        Approval.objects.create(
            run=run,
            gate=ApprovalGate.GATE_1,
            decision=ApprovalDecision.APPROVED,
            requested_by=self.admin,
            reviewed_by=self.reviewer,
        )
        updated = transition_run(
            run=run, to_state=RunState.PLANNING, actor=self.admin, expected_version=1
        )
        self.assertEqual(updated.state, RunState.PLANNING)
        self.assertEqual(updated.version, 2)

    def test_optimistic_version_conflict(self):
        run = AuditRun.objects.create(
            project=self.project,
            profile=RunProfile.QUICK,
            idempotency_key="stale",
            rule_version="2026.07.1",
            created_by=self.admin,
        )
        with self.assertRaises(TransitionConflict):
            transition_run(
                run=run, to_state=RunState.COLLECTING, actor=self.admin, expected_version=99
            )

    def test_high_qa_failure_blocks_packaging(self):
        run = AuditRun.objects.create(
            project=self.project,
            profile=RunProfile.ENTERPRISE,
            state=RunState.FINAL_QA,
            idempotency_key="qa-block",
            rule_version="2026.07.1",
            created_by=self.admin,
        )
        QAResult.objects.create(
            run=run,
            check_code="wrong_domain",
            check_version="1",
            severity=Severity.HIGH,
            status=QAResult.Status.FAIL,
            message="A wrong-domain URL remains.",
        )
        with self.assertRaises(QualityGateFailed):
            transition_run(
                run=run, to_state=RunState.PACKAGED, actor=self.admin, expected_version=1
            )
