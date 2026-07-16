"""Create the first local agency administrator without enabling public signup."""

from __future__ import annotations

import os
from datetime import timedelta

from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.domain.constants import UserRole
from app.domain.models import User
from app.domain.services import _temporary_password


class Command(BaseCommand):
    help = "Create a local agency administrator and show its one-time initial password once."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--admin-id", required=True, help="Administrator login ID")
        parser.add_argument("--email", default="", help="Optional administrator email")
        parser.add_argument(
            "--password-env",
            default="SEO_STUDIO_BOOTSTRAP_PASSWORD",
            help="Environment variable containing the initial password",
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        environment = os.getenv("DJANGO_ENV", "development").strip().casefold()
        if environment in {"staging", "production"}:
            raise CommandError(
                "bootstrap_demo is disabled in staging and production; use the controlled admin runbook."
            )

        username = str(options["admin_id"]).strip()
        if not username or len(username) > User._meta.get_field("username").max_length:
            raise CommandError("--admin-id must be a valid non-empty login ID")
        if User.objects.filter(username__iexact=username).exists():
            raise CommandError("That login ID already exists; no password was changed.")

        password_env = str(options["password_env"]).strip()
        password = os.getenv(password_env, "") if password_env else ""
        generated = not password
        if generated:
            password = _temporary_password()

        user = User(
            username=username,
            email=str(options["email"]).strip(),
            role=UserRole.AGENCY_ADMIN,
            is_staff=True,
            must_change_password=True,
            temporary_password_expires_at=timezone.now() + timedelta(minutes=30),
        )
        try:
            validate_password(password, user=user)
        except Exception as exc:
            raise CommandError(f"Initial password failed validation: {exc}") from exc
        user.set_password(password)
        user.save()

        self.stdout.write(self.style.SUCCESS(f"Created agency administrator: {username}"))
        if generated:
            self.stdout.write("One-time initial password (shown once; expires in 30 minutes):")
            self.stdout.write(password)
        else:
            self.stdout.write(
                f"Initial password read from {password_env}; it was not printed and expires in 30 minutes."
            )
        self.stdout.write("The administrator must replace it on first sign-in.")
