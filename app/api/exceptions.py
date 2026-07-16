"""DRF exception adapter for the stable API error envelope."""

from __future__ import annotations

try:
    from rest_framework.views import exception_handler as drf_exception_handler
except ImportError:  # pragma: no cover
    drf_exception_handler = None


def exception_handler(exc, context):
    if drf_exception_handler is None:  # pragma: no cover
        return None
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    request = context.get("request")
    details = response.data
    code = getattr(exc, "default_code", "request_error")
    if isinstance(details, dict) and "detail" in details:
        message = str(details["detail"])
        field_errors = None
    else:
        message = "The request could not be processed."
        field_errors = details if isinstance(details, dict) else None
    response.data = {
        "error": {
            "code": str(code),
            "message": message,
            "request_id": getattr(request, "request_id", ""),
            "retryable": response.status_code in {429, 502, 503, 504},
            **({"field_errors": field_errors} if field_errors else {}),
        }
    }
    return response
