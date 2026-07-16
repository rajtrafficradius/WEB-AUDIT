"""Project-scoped API with a Django fallback when DRF is not installed."""

from __future__ import annotations

import json
from pathlib import PurePosixPath

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View

from app.domain.audit import record_event
from app.domain.constants import RunProfile, UserRole
from app.domain.models import Approval, Artifact, AuditRun, Client, Project, User
from app.domain.permissions import (
    accessible_projects,
    can_access_project,
    can_download_artifact,
    can_manage_project,
)
from app.domain.services import issue_temporary_password
from app.domain.workflow import (
    ApprovalRequired,
    InvalidTransition,
    QualityGateFailed,
    TransitionConflict,
    create_run_idempotent,
    decide_approval,
    transition_run,
)
from app.errors import error_payload, error_response

try:
    from rest_framework import status
    from rest_framework.permissions import IsAuthenticated
    from rest_framework.response import Response
    from rest_framework.views import APIView
except ImportError:  # pragma: no cover - fallback is covered independently
    APIView = None

from .serializers import (
    ApprovalSerializer,
    ArtifactSerializer,
    AuditRunSerializer,
    ProjectSerializer,
)


def _json(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _project_dict(project: Project) -> dict:
    return {
        "id": str(project.pk),
        "client": str(project.client_id),
        "client_name": project.client.name,
        "name": project.name,
        "slug": project.slug,
        "primary_domain": project.primary_domain,
        "approved_domains": project.approved_domains,
        "locale": project.locale,
        "country_code": project.country_code,
        "business_type": project.business_type,
        "default_profile": project.default_profile,
        "status": project.status,
    }


def _run_dict(run: AuditRun) -> dict:
    return {
        "id": str(run.pk),
        "project": str(run.project_id),
        "profile": run.profile,
        "state": run.state,
        "version": run.version,
        "rule_version": run.rule_version,
        "evidence_coverage": str(run.evidence_coverage),
        "confidence": str(run.confidence),
        "health_score": str(run.health_score) if run.health_score is not None else None,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


def _workflow_error(request, exc):
    if isinstance(exc, TransitionConflict):
        code, http_status = exc.code, 409
    elif isinstance(exc, ApprovalRequired | QualityGateFailed | InvalidTransition):
        code, http_status = exc.code, 422
    elif isinstance(exc, PermissionError):
        code, http_status = "forbidden", 403
    else:
        code, http_status = "validation_error", 400
    return error_response(request, code, str(exc), status=http_status)


if APIView is not None:

    class TemporaryPasswordResetView(APIView):
        permission_classes = (IsAuthenticated,)

        def post(self, request, user_id):
            if request.user.role != UserRole.AGENCY_ADMIN and not request.user.is_superuser:
                return Response(
                    error_payload(
                        "forbidden",
                        "Agency administrator permission is required.",
                        request_id=request.request_id,
                    ),
                    status=403,
                )
            target = get_object_or_404(User, pk=user_id, is_active=True)
            try:
                valid_minutes = int(request.data.get("valid_minutes", 30))
                temporary_password = issue_temporary_password(
                    target=target,
                    issued_by=request.user,
                    request=request,
                    valid_minutes=valid_minutes,
                )
            except (ValueError, ValidationError) as exc:
                return Response(
                    error_payload("validation_error", str(exc), request_id=request.request_id),
                    status=400,
                )
            target.refresh_from_db(fields=["temporary_password_expires_at"])
            return Response(
                {
                    "user_id": str(target.pk),
                    "temporary_password": temporary_password,
                    "expires_at": target.temporary_password_expires_at.isoformat(),
                    "must_change_password": True,
                }
            )

    class ProjectListCreateView(APIView):
        permission_classes = (IsAuthenticated,)

        def get(self, request):
            projects = accessible_projects(request.user).order_by("client__name", "name")
            return Response(ProjectSerializer(projects, many=True).data)

        def post(self, request):
            if request.user.role != UserRole.AGENCY_ADMIN and not request.user.is_superuser:
                return Response(
                    error_payload(
                        "forbidden",
                        "Agency administrator permission is required.",
                        request_id=request.request_id,
                    ),
                    status=403,
                )
            serializer = ProjectSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            project = serializer.save()
            record_event(
                event_type="project.created",
                actor=request.user,
                request=request,
                object_instance=project,
            )
            return Response(ProjectSerializer(project).data, status=status.HTTP_201_CREATED)

    class ProjectDetailView(APIView):
        permission_classes = (IsAuthenticated,)

        def _get(self, request, project_id):
            project = get_object_or_404(Project.objects.select_related("client"), pk=project_id)
            if not can_access_project(request.user, project):
                return None
            return project

        def get(self, request, project_id):
            project = self._get(request, project_id)
            if project is None:
                return Response(
                    error_payload("not_found", "Project not found.", request_id=request.request_id),
                    status=404,
                )
            return Response(ProjectSerializer(project).data)

        def patch(self, request, project_id):
            project = self._get(request, project_id)
            if project is None:
                return Response(
                    error_payload("not_found", "Project not found.", request_id=request.request_id),
                    status=404,
                )
            if not can_manage_project(request.user, project):
                return Response(
                    error_payload(
                        "forbidden",
                        "Project management permission is required.",
                        request_id=request.request_id,
                    ),
                    status=403,
                )
            serializer = ProjectSerializer(project, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            project = serializer.save()
            record_event(
                event_type="project.updated",
                actor=request.user,
                request=request,
                object_instance=project,
            )
            return Response(serializer.data)

    class RunListCreateView(APIView):
        permission_classes = (IsAuthenticated,)

        def get(self, request, project_id):
            project = get_object_or_404(Project, pk=project_id)
            if not can_access_project(request.user, project):
                return Response(
                    error_payload("not_found", "Project not found.", request_id=request.request_id),
                    status=404,
                )
            runs = project.audit_runs.all()
            return Response(AuditRunSerializer(runs, many=True).data)

        def post(self, request, project_id):
            project = get_object_or_404(Project, pk=project_id)
            profile = str(request.data.get("profile", project.default_profile))
            if profile not in RunProfile.values:
                return Response(
                    error_payload(
                        "validation_error",
                        "Unknown run profile.",
                        request_id=request.request_id,
                        field_errors={"profile": ["Invalid choice."]},
                    ),
                    status=400,
                )
            try:
                run, created = create_run_idempotent(
                    project=project,
                    profile=profile,
                    idempotency_key=request.headers.get("Idempotency-Key", ""),
                    rule_version=str(request.data.get("rule_version", "2026.07.1")),
                    actor=request.user,
                    request=request,
                )
            except (PermissionError, ValueError) as exc:
                return _workflow_error(request, exc)
            return Response(AuditRunSerializer(run).data, status=201 if created else 200)

    class RunDetailView(APIView):
        permission_classes = (IsAuthenticated,)

        def get(self, request, run_id):
            run = get_object_or_404(AuditRun.objects.select_related("project"), pk=run_id)
            if not can_access_project(request.user, run.project):
                return Response(
                    error_payload("not_found", "Run not found.", request_id=request.request_id),
                    status=404,
                )
            return Response(AuditRunSerializer(run).data)

    class RunTransitionView(APIView):
        permission_classes = (IsAuthenticated,)

        def post(self, request, run_id):
            run = get_object_or_404(AuditRun, pk=run_id)
            try:
                updated = transition_run(
                    run=run,
                    to_state=str(request.data.get("to_state", "")),
                    actor=request.user,
                    expected_version=int(request.data.get("expected_version")),
                    reason=str(request.data.get("reason", "")),
                    request=request,
                )
            except (
                TransitionConflict,
                ApprovalRequired,
                QualityGateFailed,
                InvalidTransition,
                PermissionError,
                ValueError,
                TypeError,
            ) as exc:
                return _workflow_error(request, exc)
            return Response(AuditRunSerializer(updated).data)

    class ApprovalDecisionView(APIView):
        permission_classes = (IsAuthenticated,)

        def post(self, request, approval_id):
            approval = get_object_or_404(Approval, pk=approval_id)
            try:
                updated = decide_approval(
                    approval=approval,
                    decision=str(request.data.get("decision", "")),
                    actor=request.user,
                    expected_run_version=int(request.data.get("expected_run_version")),
                    comment=str(request.data.get("comment", "")),
                    request=request,
                )
            except (
                TransitionConflict,
                ApprovalRequired,
                QualityGateFailed,
                InvalidTransition,
                PermissionError,
                ValueError,
                TypeError,
            ) as exc:
                return _workflow_error(request, exc)
            return Response(ApprovalSerializer(updated).data)

    class ArtifactListView(APIView):
        permission_classes = (IsAuthenticated,)

        def get(self, request, run_id):
            run = get_object_or_404(AuditRun.objects.select_related("project"), pk=run_id)
            if not can_access_project(request.user, run.project):
                return Response(
                    error_payload("not_found", "Run not found.", request_id=request.request_id),
                    status=404,
                )
            artifacts = run.artifacts.all()
            if request.user.role == UserRole.CLIENT_REVIEWER:
                artifacts = artifacts.filter(review_status="approved")
            return Response(ArtifactSerializer(artifacts, many=True).data)

    class ArtifactDownloadView(APIView):
        permission_classes = (IsAuthenticated,)

        def get(self, request, artifact_id):
            artifact = get_object_or_404(
                Artifact.objects.select_related("run__project"), pk=artifact_id
            )
            if not can_download_artifact(request.user, artifact):
                return Response(
                    error_payload(
                        "not_found", "Artifact not found.", request_id=request.request_id
                    ),
                    status=404,
                )
            path = PurePosixPath(artifact.storage_key)
            if path.is_absolute() or ".." in path.parts or not artifact.storage_key:
                return Response(
                    error_payload(
                        "artifact_unavailable",
                        "Artifact is unavailable.",
                        request_id=request.request_id,
                    ),
                    status=404,
                )
            if not default_storage.exists(artifact.storage_key):
                return Response(
                    error_payload(
                        "artifact_unavailable",
                        "Artifact is unavailable.",
                        request_id=request.request_id,
                    ),
                    status=404,
                )
            response = FileResponse(
                default_storage.open(artifact.storage_key, "rb"),
                as_attachment=True,
                filename=path.name,
                content_type=artifact.media_type,
            )
            response["X-Content-Type-Options"] = "nosniff"
            return response

    WorkflowErrorTypes = (
        TransitionConflict,
        ApprovalRequired,
        QualityGateFailed,
        InvalidTransition,
    )

else:
    WorkflowErrorTypes = (
        TransitionConflict,
        ApprovalRequired,
        QualityGateFailed,
        InvalidTransition,
    )

    class TemporaryPasswordResetView(LoginRequiredMixin, View):
        def post(self, request, user_id):
            if request.user.role != UserRole.AGENCY_ADMIN and not request.user.is_superuser:
                return error_response(
                    request, "forbidden", "Agency administrator permission is required.", status=403
                )
            target = get_object_or_404(User, pk=user_id, is_active=True)
            data = _json(request)
            try:
                temporary_password = issue_temporary_password(
                    target=target,
                    issued_by=request.user,
                    request=request,
                    valid_minutes=int(data.get("valid_minutes", 30)),
                )
            except (ValueError, ValidationError) as exc:
                return error_response(request, "validation_error", str(exc), status=400)
            target.refresh_from_db(fields=["temporary_password_expires_at"])
            return JsonResponse(
                {
                    "user_id": str(target.pk),
                    "temporary_password": temporary_password,
                    "expires_at": target.temporary_password_expires_at.isoformat(),
                    "must_change_password": True,
                }
            )

    class ProjectListCreateView(LoginRequiredMixin, View):
        def get(self, request):
            return JsonResponse(
                {"results": [_project_dict(p) for p in accessible_projects(request.user)]}
            )

        def post(self, request):
            if request.user.role != UserRole.AGENCY_ADMIN and not request.user.is_superuser:
                return error_response(
                    request, "forbidden", "Agency administrator permission is required.", status=403
                )
            data = _json(request)
            try:
                client = Client.objects.get(pk=data.get("client"))
                approved = sorted(
                    {str(v).strip().lower().rstrip(".") for v in data.get("approved_domains", [])}
                )
                primary = str(data.get("primary_domain", "")).strip().lower().rstrip(".")
                if primary not in approved:
                    raise ValueError("approved_domains must include primary_domain")
                project = Project.objects.create(
                    client=client,
                    name=data.get("name", ""),
                    slug=data.get("slug", ""),
                    primary_domain=primary,
                    approved_domains=approved,
                    locale=data.get("locale", "en-AU"),
                    country_code=data.get("country_code", "AU"),
                    business_type=data.get("business_type", "service"),
                    default_profile=data.get("default_profile", "standard"),
                )
                record_event(
                    event_type="project.created",
                    actor=request.user,
                    request=request,
                    object_instance=project,
                )
            except (Client.DoesNotExist, ValueError, ValidationError) as exc:
                return error_response(request, "validation_error", str(exc), status=400)
            return JsonResponse(_project_dict(project), status=201)

    class ProjectDetailView(LoginRequiredMixin, View):
        def get(self, request, project_id):
            project = get_object_or_404(Project.objects.select_related("client"), pk=project_id)
            if not can_access_project(request.user, project):
                return error_response(request, "not_found", "Project not found.", status=404)
            return JsonResponse(_project_dict(project))

        def patch(self, request, project_id):
            return error_response(
                request,
                "drf_required",
                "Install Django REST Framework to edit projects through the API.",
                status=501,
            )

    class RunListCreateView(LoginRequiredMixin, View):
        def get(self, request, project_id):
            project = get_object_or_404(Project, pk=project_id)
            if not can_access_project(request.user, project):
                return error_response(request, "not_found", "Project not found.", status=404)
            return JsonResponse({"results": [_run_dict(r) for r in project.audit_runs.all()]})

        def post(self, request, project_id):
            project = get_object_or_404(Project, pk=project_id)
            data = _json(request)
            try:
                run, created = create_run_idempotent(
                    project=project,
                    profile=str(data.get("profile", project.default_profile)),
                    idempotency_key=request.headers.get("Idempotency-Key", ""),
                    rule_version=str(data.get("rule_version", "2026.07.1")),
                    actor=request.user,
                    request=request,
                )
            except (PermissionError, ValueError) as exc:
                return _workflow_error(request, exc)
            return JsonResponse(_run_dict(run), status=201 if created else 200)

    class RunDetailView(LoginRequiredMixin, View):
        def get(self, request, run_id):
            run = get_object_or_404(AuditRun.objects.select_related("project"), pk=run_id)
            if not can_access_project(request.user, run.project):
                return error_response(request, "not_found", "Run not found.", status=404)
            return JsonResponse(_run_dict(run))

    class RunTransitionView(LoginRequiredMixin, View):
        def post(self, request, run_id):
            run = get_object_or_404(AuditRun, pk=run_id)
            data = _json(request)
            try:
                updated = transition_run(
                    run=run,
                    to_state=str(data.get("to_state", "")),
                    actor=request.user,
                    expected_version=int(data.get("expected_version")),
                    reason=str(data.get("reason", "")),
                    request=request,
                )
            except (
                TransitionConflict,
                ApprovalRequired,
                QualityGateFailed,
                InvalidTransition,
                PermissionError,
                ValueError,
                TypeError,
            ) as exc:
                return _workflow_error(request, exc)
            return JsonResponse(_run_dict(updated))

    class ApprovalDecisionView(LoginRequiredMixin, View):
        def post(self, request, approval_id):
            approval = get_object_or_404(Approval, pk=approval_id)
            data = _json(request)
            try:
                updated = decide_approval(
                    approval=approval,
                    decision=str(data.get("decision", "")),
                    actor=request.user,
                    expected_run_version=int(data.get("expected_run_version")),
                    comment=str(data.get("comment", "")),
                    request=request,
                )
            except (
                TransitionConflict,
                ApprovalRequired,
                QualityGateFailed,
                InvalidTransition,
                PermissionError,
                ValueError,
                TypeError,
            ) as exc:
                return _workflow_error(request, exc)
            return JsonResponse(
                {"id": str(updated.pk), "decision": updated.decision, "gate": updated.gate}
            )

    class ArtifactListView(LoginRequiredMixin, View):
        def get(self, request, run_id):
            run = get_object_or_404(AuditRun.objects.select_related("project"), pk=run_id)
            if not can_access_project(request.user, run.project):
                return error_response(request, "not_found", "Run not found.", status=404)
            values = run.artifacts.all()
            if request.user.role == UserRole.CLIENT_REVIEWER:
                values = values.filter(review_status="approved")
            return JsonResponse(
                {
                    "results": [
                        {
                            "id": str(a.pk),
                            "title": a.title,
                            "format": a.format,
                            "sha256": a.sha256,
                            "review_status": a.review_status,
                        }
                        for a in values
                    ]
                }
            )

    class ArtifactDownloadView(LoginRequiredMixin, View):
        def get(self, request, artifact_id):
            artifact = get_object_or_404(
                Artifact.objects.select_related("run__project"), pk=artifact_id
            )
            if not can_download_artifact(request.user, artifact):
                return error_response(request, "not_found", "Artifact not found.", status=404)
            path = PurePosixPath(artifact.storage_key)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not artifact.storage_key
                or not default_storage.exists(artifact.storage_key)
            ):
                return error_response(
                    request, "artifact_unavailable", "Artifact is unavailable.", status=404
                )
            response = FileResponse(
                default_storage.open(artifact.storage_key, "rb"),
                as_attachment=True,
                filename=path.name,
                content_type=artifact.media_type,
            )
            response["X-Content-Type-Options"] = "nosniff"
            return response
