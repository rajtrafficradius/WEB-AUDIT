from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from app.domain.constants import UserRole
from app.domain.models import User

TEST_PASSWORD = "Violet-River-Quartz-7429!"  # noqa: S105 - test credential


@pytest.mark.django_db
def test_bootstrap_demo_creates_forced_change_admin(monkeypatch) -> None:
    monkeypatch.setenv("DJANGO_ENV", "development")
    monkeypatch.setenv("SEO_STUDIO_BOOTSTRAP_PASSWORD", TEST_PASSWORD)
    output = StringIO()

    call_command("bootstrap_demo", admin_id="agency.admin", stdout=output)

    user = User.objects.get(username="agency.admin")
    assert user.role == UserRole.AGENCY_ADMIN
    assert user.is_staff is True
    assert user.must_change_password is True
    assert user.temporary_password_expires_at is not None
    assert user.check_password(TEST_PASSWORD)
    assert TEST_PASSWORD not in output.getvalue()


@pytest.mark.django_db
def test_bootstrap_demo_is_idempotently_refused(monkeypatch) -> None:
    monkeypatch.setenv("DJANGO_ENV", "development")
    monkeypatch.setenv("SEO_STUDIO_BOOTSTRAP_PASSWORD", TEST_PASSWORD)
    call_command("bootstrap_demo", admin_id="agency.admin", stdout=StringIO())

    with pytest.raises(CommandError, match="already exists"):
        call_command("bootstrap_demo", admin_id="agency.admin", stdout=StringIO())


@pytest.mark.django_db
def test_bootstrap_demo_is_disabled_in_production(monkeypatch) -> None:
    monkeypatch.setenv("DJANGO_ENV", "production")

    with pytest.raises(CommandError, match="disabled"):
        call_command("bootstrap_demo", admin_id="agency.admin", stdout=StringIO())

    assert not User.objects.exists()
