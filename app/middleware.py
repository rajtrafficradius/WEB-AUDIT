"""Request correlation and account-security middleware."""

from __future__ import annotations

import re
import uuid
from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from .errors import error_response

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class RequestIDMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        supplied = request.headers.get("X-Request-ID", "")
        request.request_id = (
            supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else str(uuid.uuid4())
        )
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        return response


class ForcePasswordChangeMiddleware:
    """Prevent temporary-password accounts from accessing application data."""

    ALLOWED_PREFIXES = (
        "/auth/change-password/",
        "/auth/logout/",
        "/healthz/",
        "/readyz/",
        "/static/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and getattr(user, "must_change_password", False)
            and not request.path.startswith(self.ALLOWED_PREFIXES)
        ):
            if request.path.startswith("/api/") or request.content_type == "application/json":
                return error_response(
                    request,
                    "password_change_required",
                    "Change the temporary password before continuing.",
                    status=403,
                )
            destination = reverse("change-password")
            query = urlencode({"next": request.get_full_path()})
            return redirect(f"{destination}?{query}")
        return self.get_response(request)
