"""Security-sensitive domain services."""

from __future__ import annotations

import secrets
import string
from datetime import timedelta

from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone

from .audit import record_event
from .constants import UserRole
from .models import User


def _temporary_password(length: int = 20) -> str:
    if length < 16:
        raise ValueError("Temporary passwords must be at least 16 characters")
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*_-+"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in value)
            and any(c.isupper() for c in value)
            and any(c.isdigit() for c in value)
        ):
            return value


@transaction.atomic
def issue_temporary_password(
    *, target: User, issued_by: User, request=None, valid_minutes: int = 30
) -> str:
    if not (issued_by.is_superuser or issued_by.role == UserRole.AGENCY_ADMIN):
        raise PermissionError("Only an agency administrator can reset another account")
    if valid_minutes < 5 or valid_minutes > 1440:
        raise ValueError("Temporary-password lifetime must be between 5 and 1440 minutes")
    password = _temporary_password()
    validate_password(password, user=target)
    target.set_password(password)
    target.must_change_password = True
    target.temporary_password_expires_at = timezone.now() + timedelta(minutes=valid_minutes)
    target.password_changed_at = timezone.now()
    target.save(
        update_fields=[
            "password",
            "must_change_password",
            "temporary_password_expires_at",
            "password_changed_at",
        ]
    )
    record_event(
        event_type="auth.temporary_password_issued",
        actor=issued_by,
        request=request,
        object_instance=target,
        payload={"valid_minutes": valid_minutes},
    )
    # Return exactly once; the password is never stored in plaintext or written to logs.
    return password
