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

@pytest.mark.django_db
def test_deployed_admin_bootstrap_is_create_only(monkeypatch) -> None:
    monkeypatch.setenv("SEO_STUDIO_BOOTSTRAP_ADMIN_ID", "agency.admin")
    monkeypatch.setenv("SEO_STUDIO_BOOTSTRAP_PASSWORD", TEST_PASSWORD)

    call_command("bootstrap_admin_from_env", stdout=StringIO())
    user = User.objects.get(username="agency.admin")
    original_password_hash = user.password

    monkeypatch.setenv("SEO_STUDIO_BOOTSTRAP_PASSWORD", "Different-Quartz-River-8462!")
    call_command("bootstrap_admin_from_env", stdout=StringIO())
    user.refresh_from_db()

    assert user.password == original_password_hash
    assert user.role == UserRole.AGENCY_ADMIN
    assert user.is_superuser is True
    assert user.must_change_password is True


@pytest.mark.django_db
def test_deployed_admin_bootstrap_refuses_to_bypass_an_existing_account(
    monkeypatch,
) -> None:
    User.objects.create_user(
        username="existing",
        password=TEST_PASSWORD,
        must_change_password=False,
    )
    monkeypatch.setenv("SEO_STUDIO_BOOTSTRAP_ADMIN_ID", "agency.admin")
    monkeypatch.setenv("SEO_STUDIO_BOOTSTRAP_PASSWORD", TEST_PASSWORD)

    with pytest.raises(CommandError, match="first account already exists"):
        call_command("bootstrap_admin_from_env", stdout=StringIO())

    assert not User.objects.filter(username="agency.admin").exists()
