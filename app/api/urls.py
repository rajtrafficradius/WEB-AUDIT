from django.urls import path

from .views import (
    ApprovalDecisionView,
    ArtifactDownloadView,
    ArtifactListView,
    ProjectDetailView,
    ProjectListCreateView,
    RunDetailView,
    RunListCreateView,
    RunTransitionView,
    TemporaryPasswordResetView,
)

urlpatterns = [
    path(
        "users/<uuid:user_id>/temporary-password/",
        TemporaryPasswordResetView.as_view(),
        name="api-user-temporary-password",
    ),
    path("projects/", ProjectListCreateView.as_view(), name="api-project-list"),
    path("projects/<uuid:project_id>/", ProjectDetailView.as_view(), name="api-project-detail"),
    path("projects/<uuid:project_id>/runs/", RunListCreateView.as_view(), name="api-run-list"),
    path("runs/<uuid:run_id>/", RunDetailView.as_view(), name="api-run-detail"),
    path("runs/<uuid:run_id>/transition/", RunTransitionView.as_view(), name="api-run-transition"),
    path("runs/<uuid:run_id>/artifacts/", ArtifactListView.as_view(), name="api-artifact-list"),
    path(
        "artifacts/<uuid:artifact_id>/download/",
        ArtifactDownloadView.as_view(),
        name="api-artifact-download",
    ),
    path(
        "approvals/<uuid:approval_id>/decision/",
        ApprovalDecisionView.as_view(),
        name="api-approval-decision",
    ),
]
