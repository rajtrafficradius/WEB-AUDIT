"""Consistent JSON error responses used by browser and API endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.http import JsonResponse


def error_payload(
    code: str,
    message: str,
    *,
    request_id: str | None = None,
    field_errors: Mapping[str, Any] | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": request_id or "",
        "retryable": retryable,
    }
    if field_errors:
        error["field_errors"] = dict(field_errors)
    return {"error": error}


def error_response(
    request: Any,
    code: str,
    message: str,
    *,
    status: int,
    field_errors: Mapping[str, Any] | None = None,
    retryable: bool = False,
) -> JsonResponse:
    return JsonResponse(
        error_payload(
            code,
            message,
            request_id=getattr(request, "request_id", ""),
            field_errors=field_errors,
            retryable=retryable,
        ),
        status=status,
    )
