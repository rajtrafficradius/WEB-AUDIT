"""Organisation-wide provider credentials set once in the admin Credentials page."""

from __future__ import annotations

import base64

import pytest
from django.urls import reverse

from app.domain.constants import UserRole
from app.domain.crypto import decrypt_credentials
from app.domain.models import AuditRun, Client, ManagedCredential, Project, User

PASSWORD = "Managed-credential-test-1!"  # noqa: S105 - test credential
API_KEY = "semrush-org-key-abcdef123456"  # noqa: S105 - fake key for tests
ENCRYPTION_KEYS = "test-v1:" + base64.urlsafe_b64encode(b"m" * 32).decode("ascii")


@pytest.fixture
def crypto_settings(settings):
    settings.CREDENTIAL_ENCRYPTION_KEYS = ENCRYPTION_KEYS
    settings.CREDENTIAL_ENCRYPTION_ACTIVE_KEY = "test-v1"
    return settings


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        username="agency.admin",
        password=PASSWORD,
        role=UserRole.AGENCY_ADMIN,
        must_change_password=False,
    )


@pytest.fixture
def reviewer_user(db):
    return User.objects.create_user(
        username="client.reviewer",
        password=PASSWORD,
        role=UserRole.CLIENT_REVIEWER,
        must_change_password=False,
    )


@pytest.mark.django_db
def test_only_agency_admins_reach_the_credentials_page(client, admin_user, reviewer_user):
    assert client.login(username="client.reviewer", password=PASSWORD)
    assert client.get(reverse("credentials"), secure=True).status_code == 403

    client.logout()
    assert client.login(username="agency.admin", password=PASSWORD)
    ok = client.get(reverse("credentials"), secure=True)
    assert ok.status_code == 200
    body = ok.content.decode("utf-8")
    assert 'name="api_key"' in body
    assert 'type="password"' in body


@pytest.mark.django_db
def test_saving_a_key_encrypts_it_and_applies_by_default(client, admin_user, crypto_settings):
    assert client.login(username="agency.admin", password=PASSWORD)

    response = client.post(
        reverse("credentials"),
        {"provider": "semrush", "api_key": API_KEY},
        secure=True,
    )

    assert response.status_code == 302
    credential = ManagedCredential.objects.get(provider="semrush")
    assert API_KEY not in credential.encrypted_credentials
    assert decrypt_credentials(
        credential.encrypted_credentials, credential.encryption_key_id
    ) == {"api_key": API_KEY}
    assert credential.credential_hint.endswith(API_KEY[-4:])
    assert credential.is_active

    # The stored secret is never rendered back to the page.
    page = client.get(reverse("credentials"), secure=True).content.decode("utf-8")
    assert API_KEY not in page
    assert credential.credential_hint in page


@pytest.mark.django_db
def test_saving_again_replaces_rather_than_duplicates(client, admin_user, crypto_settings):
    assert client.login(username="agency.admin", password=PASSWORD)
    for key in (API_KEY, "semrush-rotated-key-998877"):
        client.post(reverse("credentials"), {"provider": "semrush", "api_key": key}, secure=True)

    credentials = ManagedCredential.objects.filter(provider="semrush")
    assert credentials.count() == 1
    assert decrypt_credentials(
        credentials.first().encrypted_credentials, credentials.first().encryption_key_id
    ) == {"api_key": "semrush-rotated-key-998877"}


@pytest.mark.django_db
def test_market_data_falls_back_from_project_to_org_to_env(admin_user, crypto_settings, settings):
    from app.domain.crypto import encrypt_credentials
    from integrations.market_data import MarketDataService

    client_row = Client.objects.create(name="Harbour Lane Ceramics", slug="harbour-lane")
    project = Project.objects.create(
        client=client_row,
        name="Harbour Lane SEO",
        slug="harbour-lane",
        primary_domain="harbourlane.com.au",
        approved_domains=["harbourlane.com.au"],
        business_type=Project.BusinessType.ECOMMERCE,
    )
    run = AuditRun.objects.create(
        project=project,
        profile="quick",
        idempotency_key="managed-run",
        rule_version="1.1.0",
        created_by=admin_user,
    )

    # 1. Nothing configured -> the environment fallback.
    settings.SEMRUSH_API_KEY = "environment-key"
    assert MarketDataService.resolve_api_key(run) == "environment-key"
    assert MarketDataService.is_configured(run) is True

    # 2. An organisation credential overrides the environment.
    token, key_id = encrypt_credentials({"api_key": API_KEY})
    ManagedCredential.objects.create(
        provider="semrush",
        encrypted_credentials=token,
        encryption_key_id=key_id,
        credential_hint=f"····{API_KEY[-4:]}",
        is_active=True,
    )
    assert MarketDataService.resolve_api_key(run) == API_KEY

    # 3. A per-project key overrides the organisation credential.
    project_token, project_key_id = encrypt_credentials({"api_key": "project-specific-key-4321"})
    project.connections.create(
        provider="semrush",
        label="",
        encrypted_credentials=project_token,
        encryption_key_id=project_key_id,
        availability="available",
    )
    assert MarketDataService.resolve_api_key(run) == "project-specific-key-4321"

    # With no key anywhere, the service reports itself unconfigured.
    project.connections.all().delete()
    ManagedCredential.objects.all().delete()
    settings.SEMRUSH_API_KEY = ""
    assert MarketDataService.is_configured(run) is False
