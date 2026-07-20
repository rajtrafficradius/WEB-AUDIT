"""Storing provider API keys from the interface, encrypted and never displayed."""

from __future__ import annotations

import base64

import pytest
from django.urls import reverse

from app.domain.constants import AvailabilityStatus, UserRole
from app.domain.crypto import decrypt_credentials
from app.domain.models import AuditRun, Client, Connection, Project, User

PASSWORD = "Source-credential-test-1!"  # noqa: S105 - test credential
API_KEY = "semrush-live-key-abcdef123456"  # noqa: S105 - fake key for tests
ENCRYPTION_KEYS = "test-v1:" + base64.urlsafe_b64encode(b"k" * 32).decode("ascii")


@pytest.fixture
def crypto_settings(settings):
    settings.CREDENTIAL_ENCRYPTION_KEYS = ENCRYPTION_KEYS
    settings.CREDENTIAL_ENCRYPTION_ACTIVE_KEY = "test-v1"
    return settings


@pytest.fixture
def project_admin(db):
    user = User.objects.create_user(
        username="source-admin",
        password=PASSWORD,
        role=UserRole.AGENCY_ADMIN,
        must_change_password=False,
    )
    client = Client.objects.create(name="Harbour Lane Ceramics", slug="harbour-lane")
    project = Project.objects.create(
        client=client,
        name="Harbour Lane SEO",
        slug="harbour-lane",
        primary_domain="harbourlane.com.au",
        approved_domains=["harbourlane.com.au"],
        business_type=Project.BusinessType.ECOMMERCE,
    )
    return user, project


@pytest.mark.django_db
def test_connect_form_is_offered_with_an_api_key_field(client, project_admin):
    user, project = project_admin
    assert client.login(username=user.username, password=PASSWORD)

    response = client.get(reverse("source-connect", args=(project.pk,)), secure=True)

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert 'name="api_key"' in body
    assert 'type="password"' in body


@pytest.mark.django_db
def test_saving_a_key_encrypts_it_and_marks_the_source_available(
    client, project_admin, crypto_settings
):
    user, project = project_admin
    assert client.login(username=user.username, password=PASSWORD)

    response = client.post(
        reverse("source-connect", args=(project.pk,)),
        {"provider": "semrush", "label": "", "api_key": API_KEY, "unavailable_reason": ""},
        secure=True,
    )

    assert response.status_code == 302
    connection = Connection.objects.get(project=project, provider="semrush")
    assert connection.availability == AvailabilityStatus.AVAILABLE
    assert connection.unavailable_reason == ""
    # Stored encrypted, recoverable only through the configured key.
    assert API_KEY not in connection.encrypted_credentials
    assert decrypt_credentials(
        connection.encrypted_credentials, connection.encryption_key_id
    ) == {"api_key": API_KEY}
    # Only a recognisable tail is retained for humans.
    assert connection.external_account_id.endswith(API_KEY[-4:])
    assert API_KEY not in connection.external_account_id


@pytest.mark.django_db
def test_the_stored_key_is_never_rendered_back_to_the_page(client, project_admin, crypto_settings):
    user, project = project_admin
    assert client.login(username=user.username, password=PASSWORD)
    client.post(
        reverse("source-connect", args=(project.pk,)),
        {"provider": "semrush", "label": "", "api_key": API_KEY, "unavailable_reason": ""},
        secure=True,
    )

    response = client.get(reverse("project-sources", args=(project.pk,)), secure=True)

    body = response.content.decode("utf-8")
    assert API_KEY not in body
    assert "Key stored" in body


@pytest.mark.django_db
def test_a_source_without_a_key_still_requires_a_written_reason(client, project_admin):
    user, project = project_admin
    assert client.login(username=user.username, password=PASSWORD)

    rejected = client.post(
        reverse("source-connect", args=(project.pk,)),
        {"provider": "gsc", "label": "", "api_key": "", "unavailable_reason": ""},
        secure=True,
    )
    assert rejected.status_code == 400
    assert not Connection.objects.filter(project=project, provider="gsc").exists()

    accepted = client.post(
        reverse("source-connect", args=(project.pk,)),
        {
            "provider": "gsc",
            "label": "",
            "api_key": "",
            "unavailable_reason": "The client has not granted property access yet.",
        },
        secure=True,
    )
    assert accepted.status_code == 302
    connection = Connection.objects.get(project=project, provider="gsc")
    assert connection.availability == AvailabilityStatus.UNAVAILABLE
    assert "property access" in connection.unavailable_reason


@pytest.mark.django_db
def test_removing_the_credential_reverts_the_source_to_unavailable(
    client, project_admin, crypto_settings
):
    user, project = project_admin
    assert client.login(username=user.username, password=PASSWORD)
    client.post(
        reverse("source-connect", args=(project.pk,)),
        {"provider": "semrush", "label": "", "api_key": API_KEY, "unavailable_reason": ""},
        secure=True,
    )
    connection = Connection.objects.get(project=project, provider="semrush")

    response = client.post(
        reverse("source-disconnect", args=(project.pk, connection.pk)), secure=True
    )

    assert response.status_code == 302
    connection.refresh_from_db()
    assert connection.encrypted_credentials == ""
    assert connection.availability == AvailabilityStatus.UNAVAILABLE
    assert connection.unavailable_reason


@pytest.mark.django_db
def test_market_data_prefers_the_project_key_over_the_environment(
    project_admin, crypto_settings, settings
):
    from integrations.market_data import MarketDataService

    user, project = project_admin
    settings.SEMRUSH_API_KEY = "environment-fallback-key"
    run = AuditRun.objects.create(
        project=project,
        profile="quick",
        idempotency_key="credential-run",
        rule_version="1.1.0",
        created_by=user,
    )

    # With no stored connection the environment key is used.
    assert MarketDataService(run).api_key == "environment-fallback-key"

    from app.domain.crypto import encrypt_credentials

    token, key_id = encrypt_credentials({"api_key": API_KEY})
    Connection.objects.create(
        project=project,
        provider="semrush",
        label="",
        availability=AvailabilityStatus.AVAILABLE,
        encrypted_credentials=token,
        encryption_key_id=key_id,
    )

    assert MarketDataService(run).api_key == API_KEY
