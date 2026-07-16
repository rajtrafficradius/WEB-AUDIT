"""Create the first deployed administrator from explicit environment variables."""

from __future__ import annotations

import os
from datetime import timedelta

from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.domain.constants import UserRole
from app.domain.models import User


class Command(BaseCommand):
    help = "Create, but never reset, the first administrator from deployment variables."

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        username = os.getenv("SEO_STUDIO_BOOTSTRAP_ADMIN_ID", "").strip()
        password = os.getenv("SEO_STUDIO_BOOTSTRAP_PASSWORD", "")
        email = os.getenv("SEO_STUDIO_BOOTSTRAP_ADMIN_EMAIL", "").strip()

        if not username or not password:
            raise CommandError(
                "SEO_STUDIO_BOOTSTRAP_ADMIN_ID and SEO_STUDIO_BOOTSTRAP_PASSWORD are required."
            )
        if len(username) > User._meta.get_field("username").max_length:
            raise CommandError("SEO_STUDIO_BOOTSTRAP_ADMIN_ID is too long.")

        existing = User.objects.filter(username__iexact=username).first()
        if existing is not None:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Administrator {existing.username} already exists; no credentials changed."
                )
            )
            return
        if User.objects.exists():
            raise CommandError(
                "The first account already exists; create further users through controlled administration."
            )

        user = User(
            username=username,
            email=email,
            role=UserRole.AGENCY_ADMIN,
            is_staff=True,
            is_superuser=True,
            must_change_password=True,
            temporary_password_expires_at=timezone.now() + timedelta(hours=2),
        )
        try:
            validate_password(password, user=user)
        except Exception as exc:
            raise CommandError(f"Bootstrap password failed validation: {exc}") from exc
        user.set_password(password)
        user.save()
        self.stdout.write(
            self.style.SUCCESS(
                f"Created production administrator {username}; password was not printed."
            )
        )
