"""Live SEMrush status, unit balance, and admin key reveal."""

from __future__ import annotations

import base64

import pytest
from django.core.cache import cache
from django.urls import reverse

from app.domain.constants import AvailabilityStatus, UserRole
from app.domain.crypto import encrypt_credentials
from app.domain.models import (
    AuditRun,
    Client,
    ManagedCredential,
    Project,
    SourceSnapshot,
    User,
)
from integrations import semrush_status

PASSWORD = "Status-test-password-1!"  # noqa: S105 - test credential
API_KEY = "semrush-status-key-abcdef123456"  # noqa: S105 - fake key for tests
ENCRYPTION_KEYS = "test-v1:" + base64.urlsafe_b64encode(b"s" * 32).decode("ascii")


class FakeTransport:
    """Stand-in for the pinned HTTP transport used by the balance check."""

    def __init__(self, status_code=200, body=b"49330"):
        self.status_code = status_code
        self.body = body
        self.calls = 0

    def fetch(self, target, *, method="GET", headers=None, timeout=0.0, max_bytes=0):
        self.calls += 1

        class _Response:
            status_code = self.status_code
            body = self.body

        return _Response()


class FakeTarget:
    normalized_url = "https://www.semrush.com/users/countapiunits.html"
    hostname = "www.semrush.com"
    port = 443
    approved_ips = ("203.0.113.10",)


class FakeGuard:
    def validate(self, url):
        return FakeTarget()


@pytest.fixture
def crypto_settings(settings):
    settings.CREDENTIAL_ENCRYPTION_KEYS = ENCRYPTION_KEYS
    settings.CREDENTIAL_ENCRYPTION_ACTIVE_KEY = "test-v1"
    settings.SEMRUSH_API_KEY = ""
    return settings


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def test_balance_parses_the_plain_integer_body():
    transport = FakeTransport(body=b"49330\n")
    assert semrush_status.fetch_unit_balance(
        API_KEY, transport=transport, guard=FakeGuard()
    ) == 49330


def test_balance_returns_none_on_error_body():
    transport = FakeTransport(body=b"ERROR :: WRONG KEY")
    assert semrush_status.fetch_unit_balance(
        API_KEY, transport=transport, guard=FakeGuard()
    ) is None


@pytest.mark.django_db
def test_check_status_reports_no_key_when_nothing_is_configured(crypto_settings):
    result = semrush_status.check_status()
    assert result["status"] == "no_key"
    assert result["units_remaining"] is None


@pytest.mark.django_db
def test_check_status_working_and_cached(crypto_settings):
    token, key_id = encrypt_credentials({"api_key": API_KEY})
    ManagedCredential.objects.create(
        provider="semrush",
        encrypted_credentials=token,
        encryption_key_id=key_id,
        credential_hint=f"····{API_KEY[-4:]}",
        is_active=True,
    )
    transport = FakeTransport(body=b"50000")
    first = semrush_status.check_status(transport=transport)
    assert first["status"] == "working"
    assert first["units_remaining"] == 50000
    # Second call is served from cache — no second network hit.
    semrush_status.check_status(transport=transport)
    assert transport.calls == 1


@pytest.mark.django_db
def test_units_used_sums_recorded_snapshots(crypto_settings):
    user = User.objects.create_user(username="u", password=PASSWORD, role=UserRole.AGENCY_ADMIN)
    client = Client.objects.create(name="Harbour Lane", slug="harbour-lane")
    project = Project.objects.create(
        client=client, name="Harbour", slug="harbour",
        primary_domain="harbourlane.com.au", approved_domains=["harbourlane.com.au"],
        business_type=Project.BusinessType.ECOMMERCE,
    )
    run = AuditRun.objects.create(
        project=project, profile="quick", idempotency_key="k", rule_version="1.1.0",
        created_by=user,
    )
    for spent in (400, 270):
        SourceSnapshot.objects.create(
            run=run, source_type="semrush", availability=AvailabilityStatus.AVAILABLE,
            record_count=1, metadata={"units_spent": spent},
        )
    from integrations.market_data import studio_units_spent

    assert studio_units_spent() == 670


@pytest.mark.django_db
def test_status_endpoint_requires_login_and_returns_json(client, crypto_settings):
    User.objects.create_user(
        username="admin", password=PASSWORD, role=UserRole.AGENCY_ADMIN,
        must_change_password=False,
    )
    assert client.login(username="admin", password=PASSWORD)
    response = client.get(reverse("semrush-status"), secure=True)
    assert response.status_code == 200
    assert response.json()["status"] == "no_key"


@pytest.mark.django_db
def test_admin_can_reveal_a_stored_key_and_it_is_audit_logged(client, crypto_settings):
    from app.domain.models import AuditEvent

    User.objects.create_user(
        username="admin", password=PASSWORD, role=UserRole.AGENCY_ADMIN,
        must_change_password=False,
    )
    token, key_id = encrypt_credentials({"api_key": API_KEY})
    credential = ManagedCredential.objects.create(
        provider="semrush", encrypted_credentials=token, encryption_key_id=key_id,
        credential_hint=f"····{API_KEY[-4:]}", is_active=True,
    )
    assert client.login(username="admin", password=PASSWORD)

    response = client.post(reverse("credential-reveal", args=(credential.pk,)), secure=True)

    assert response.status_code == 200
    assert response.json()["api_key"] == API_KEY
    assert AuditEvent.objects.filter(event_type="managed_credential.revealed").exists()


@pytest.mark.django_db
def test_reviewers_cannot_reveal_keys(client, crypto_settings):
    User.objects.create_user(
        username="viewer", password=PASSWORD, role=UserRole.CLIENT_REVIEWER,
        must_change_password=False,
    )
    token, key_id = encrypt_credentials({"api_key": API_KEY})
    credential = ManagedCredential.objects.create(
        provider="semrush", encrypted_credentials=token, encryption_key_id=key_id,
        credential_hint="····3456", is_active=True,
    )
    assert client.login(username="viewer", password=PASSWORD)
    response = client.post(reverse("credential-reveal", args=(credential.pk,)), secure=True)
    assert response.status_code == 403
