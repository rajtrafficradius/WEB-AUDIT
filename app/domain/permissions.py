"""Central server-side project and workflow authorization rules."""

from __future__ import annotations

from django.db.models import Q

from .constants import UserRole
from .models import Artifact, AuditRun, Membership, Project


def accessible_projects(user):
    queryset = Project.objects.select_related("client")
    if not getattr(user, "is_authenticated", False):
        return queryset.none()
    if user.is_superuser or user.role == UserRole.AGENCY_ADMIN:
        return queryset
    return queryset.filter(
        Q(memberships__user=user, memberships__is_active=True)
        | Q(
            client__memberships__user=user,
            client__memberships__project__isnull=True,
            client__memberships__is_active=True,
        )
    ).distinct()


def can_access_project(user, project: Project) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser or user.role == UserRole.AGENCY_ADMIN:
        return True
    return (
        Membership.objects.filter(user=user, client=project.client, is_active=True)
        .filter(Q(project=project) | Q(project__isnull=True))
        .exists()
    )


def can_manage_project(user, project: Project) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser or user.role == UserRole.AGENCY_ADMIN:
        return True
    if user.role != UserRole.ANALYST:
        return False
    return (
        Membership.objects.filter(
            user=user,
            client=project.client,
            is_active=True,
            access_role=UserRole.ANALYST,
        )
        .filter(Q(project=project) | Q(project__isnull=True))
        .exists()
    )


def can_review_project(user, project: Project) -> bool:
    return can_access_project(user, project) and user.role in {
        UserRole.AGENCY_ADMIN,
        UserRole.ANALYST,
        UserRole.CLIENT_REVIEWER,
    }


def can_approve_gate(user, run: AuditRun, gate: str) -> bool:
    if not can_review_project(user, run.project):
        return False
    if gate == "high_risk":
        return user.is_superuser or user.role == UserRole.AGENCY_ADMIN
    return user.role in {UserRole.AGENCY_ADMIN, UserRole.CLIENT_REVIEWER} or user.is_superuser


def can_download_artifact(user, artifact: Artifact) -> bool:
    if not can_access_project(user, artifact.run.project):
        return False
    if user.role == UserRole.CLIENT_REVIEWER:
        return artifact.review_status == "approved"
    return True


try:
    from rest_framework.permissions import BasePermission
except ImportError:  # pragma: no cover - optional dependency
    BasePermission = object


class HasProjectAccess(BasePermission):
    def has_object_permission(self, request, view, obj):
        project = obj if isinstance(obj, Project) else getattr(obj, "project", None)
        if project is None and hasattr(obj, "run"):
            project = obj.run.project
        return bool(project and can_access_project(request.user, project))


class CanManageProject(HasProjectAccess):
    def has_object_permission(self, request, view, obj):
        project = obj if isinstance(obj, Project) else getattr(obj, "project", None)
        if project is None and hasattr(obj, "run"):
            project = obj.run.project
        return bool(project and can_manage_project(request.user, project))
