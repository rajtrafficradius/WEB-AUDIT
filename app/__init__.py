"""Traffic Radius Enterprise SEO Studio Django project."""

try:  # Celery remains optional for management commands and lightweight tooling.
    from .celery import app as celery_app
except ImportError:  # pragma: no cover - dependency availability is environment-specific
    celery_app = None

__all__ = ("celery_app",)
