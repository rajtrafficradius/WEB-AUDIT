from django.contrib import admin
from django.urls import include, path

from . import health, views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", health.healthz, name="healthz"),
    path("readyz/", health.readyz, name="readyz"),
    path("auth/csrf/", views.csrf_cookie, name="csrf-cookie"),
    path("auth/login/", views.login_view, name="login"),
    path("auth/logout/", views.logout_view, name="logout"),
    path("auth/change-password/", views.change_password_view, name="change-password"),
    path("auth/me/", views.current_user_view, name="current-user"),
    path("", views.dashboard_view, name="dashboard"),
    path("projects/", views.dashboard_view, name="project-list"),
    path("projects/new/", views.project_create_view, name="project-create"),
    path("projects/<uuid:project_id>/", views.project_detail_view, name="project-detail"),
    path(
        "projects/<uuid:project_id>/intake/",
        views.project_intake_view,
        name="project-intake",
    ),
    path(
        "projects/<uuid:project_id>/sources/",
        views.project_sources_view,
        name="project-sources",
    ),
    path(
        "projects/<uuid:project_id>/sources/connect/",
        views.source_connect_view,
        name="source-connect",
    ),
    path(
        "projects/<uuid:project_id>/sources/upload/",
        views.source_upload_view,
        name="source-upload",
    ),
    path(
        "projects/<uuid:project_id>/sources/<uuid:source_id>/",
        views.source_detail_view,
        name="source-detail",
    ),
    path(
        "projects/<uuid:project_id>/sources/<uuid:source_id>/refresh/",
        views.source_refresh_view,
        name="source-refresh",
    ),
    path(
        "projects/<uuid:project_id>/imports/<uuid:import_id>/",
        views.source_import_detail_view,
        name="source-import-detail",
    ),
    path(
        "projects/<uuid:project_id>/findings/",
        views.project_findings_view,
        name="project-findings",
    ),
    path(
        "projects/<uuid:project_id>/findings/<uuid:finding_id>/",
        views.finding_detail_view,
        name="finding-detail",
    ),
    path(
        "projects/<uuid:project_id>/actions/",
        views.action_plan_view,
        name="action-plan",
    ),
    path(
        "projects/<uuid:project_id>/actions/new/",
        views.action_create_view,
        name="action-create",
    ),
    path(
        "projects/<uuid:project_id>/actions/<uuid:action_id>/",
        views.action_detail_view,
        name="action-detail",
    ),
    path(
        "projects/<uuid:project_id>/approvals/",
        views.project_approvals_view,
        name="project-approvals",
    ),
    path(
        "projects/<uuid:project_id>/approvals/<uuid:approval_id>/decide/",
        views.approval_decide_view,
        name="approval-decide",
    ),
    path("projects/<uuid:project_id>/content/", views.content_list_view, name="content-list"),
    path(
        "projects/<uuid:project_id>/content/<uuid:content_id>/",
        views.content_detail_view,
        name="content-detail",
    ),
    path(
        "projects/<uuid:project_id>/content/<uuid:content_id>/approve/",
        views.content_approve_view,
        name="content-approve",
    ),
    path(
        "projects/<uuid:project_id>/content/<uuid:content_id>/revision/",
        views.content_revision_view,
        name="content-revision",
    ),
    path("runs/<uuid:run_id>/", views.run_detail_view, name="run-detail"),
    path("runs/<uuid:run_id>/cancel/", views.run_cancel_view, name="run-cancel"),
    path("runs/<uuid:run_id>/resume/", views.run_resume_view, name="run-resume"),
    path("projects/<uuid:project_id>/exports/", views.export_qa_view, name="export-qa"),
    path(
        "projects/<uuid:project_id>/exports/download-latest/",
        views.audit_results_download_view,
        name="audit-results-download",
    ),
    path(
        "projects/<uuid:project_id>/exports/build/",
        views.export_build_view,
        name="export-build",
    ),
    path(
        "projects/<uuid:project_id>/exports/<uuid:package_id>/download/",
        views.export_download_view,
        name="export-download",
    ),
    path("api/v1/", include("app.api.urls")),
]
