from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.conf import settings

from app.celery import app as celery_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT_ROOT = PROJECT_ROOT / "deployment"


def _config(name: str) -> dict:
    return json.loads((DEPLOYMENT_ROOT / name).read_text(encoding="utf-8"))


@pytest.mark.deployment
@pytest.mark.parametrize(
    ("environment", "filename", "replicas"),
    [
        ("staging", "railway.staging.json", 1),
        ("production", "railway.production.json", 2),
    ],
)
def test_web_manifests_fail_closed_and_run_one_predeploy_migration(
    environment: str, filename: str, replicas: int
) -> None:
    deploy = _config(filename)["deploy"]

    assert deploy["startCommand"] == (
        f"env DJANGO_ENV={environment} /app/deployment/entrypoint.sh web"
    )
    assert deploy["preDeployCommand"] == [
        f"env DJANGO_ENV={environment} /app/deployment/entrypoint.sh release"
    ]
    assert deploy["healthcheckPath"] == "/readyz/"
    assert deploy["numReplicas"] == replicas


@pytest.mark.deployment
@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("process", ["analysis", "render", "scheduler"])
def test_worker_manifests_have_explicit_environment_and_process(
    environment: str, process: str
) -> None:
    config = _config(f"railway.{environment}.{process}.json")
    deploy = config["deploy"]
    entrypoint_process = {"analysis": "analysis-worker", "render": "render-worker"}.get(
        process, process
    )

    assert deploy["startCommand"] == (
        f"env DJANGO_ENV={environment} /app/deployment/entrypoint.sh {entrypoint_process}"
    )
    assert "preDeployCommand" not in deploy
    if process == "scheduler":
        assert deploy["numReplicas"] == 1


@pytest.mark.deployment
def test_web_startup_never_runs_migrations_and_image_defaults_to_production() -> None:
    entrypoint = (DEPLOYMENT_ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    release_block = entrypoint.split("release)", 1)[1].split(";;", 1)[0]
    web_block = entrypoint.split("web)", 1)[1].split(";;", 1)[0]
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "manage.py migrate --noinput" in release_block
    assert "manage.py migrate --check" in release_block
    assert "manage.py migrate" not in web_block
    assert "DJANGO_ENV=production" in dockerfile
    assert "manage.py collectstatic --noinput" in dockerfile
    assert "/app/.local-media/quarantine" in dockerfile
    assert "chown -R studio:studio /app/.local-media" in dockerfile


@pytest.mark.deployment
def test_scheduler_and_render_tasks_are_registered_and_routed() -> None:
    celery_app.loader.import_default_modules()

    assert "studio.scheduler.mark_stale_stages" in celery_app.tasks
    assert "studio.render.run_summary_html" in celery_app.tasks
    assert settings.CELERY_TASK_ROUTES["studio.scheduler.*"] == {"queue": "analysis"}
    assert settings.CELERY_TASK_ROUTES["studio.render.*"] == {"queue": "render"}
    schedule = settings.CELERY_BEAT_SCHEDULE["mark-stale-run-stages"]
    assert schedule["task"] == "studio.scheduler.mark_stale_stages"
    assert schedule["schedule"] >= 60
    assert schedule["options"]["queue"] == "analysis"
