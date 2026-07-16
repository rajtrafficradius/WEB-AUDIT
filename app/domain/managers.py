from __future__ import annotations

from django.contrib.auth.models import UserManager as DjangoUserManager

from .constants import UserRole


class UserManager(DjangoUserManager):
    use_in_migrations = True

    def _create_user(self, username, email, password, **extra_fields):
        if not username:
            raise ValueError("A username is required")
        username = self.model.normalize_username(username.strip())
        email = self.normalize_email(email)
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", UserRole.AGENCY_ADMIN)
        extra_fields.setdefault("must_change_password", False)
        if not extra_fields.get("is_staff") or not extra_fields.get("is_superuser"):
            raise ValueError("Superusers must have is_staff=True and is_superuser=True")
        return self._create_user(username, email, password, **extra_fields)
