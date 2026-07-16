"""Celery application shared by analysis, render, and scheduler processes."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")

try:
    from celery import Celery
except ImportError:  # pragma: no cover
    app = None
else:
    app = Celery("traffic_radius_seo_studio")
    app.config_from_object("django.conf:settings", namespace="CELERY")
    app.autodiscover_tasks()
