"""SEMrush semicolon-delimited API transport with DNS-pinned requests."""

from __future__ import annotations

import csv
from io import StringIO
from typing import Any

from audit_engine.crawler import PinnedHTTPTransport, SafeTransport
from audit_engine.urls import SSRFGuard

from .base import AdapterFailure, FailureKind


class PinnedSemrushTransport:
    """Expose the SEMrush CSV wire format through the JSON-adapter protocol."""

    def __init__(
        self,
        *,
        http_transport: SafeTransport | None = None,
        guard: SSRFGuard | None = None,
    ) -> None:
        self.guard = guard or SSRFGuard(("api.semrush.com",))
        self.http_transport = http_transport or PinnedHTTPTransport()

    def request(self, request: Any) -> dict[str, Any]:
        response = self.http_transport.fetch(
            self.guard.validate(request.url),
            method="GET",
            headers={"Accept": "text/csv"},
            timeout=request.timeout_seconds,
            max_bytes=request.max_response_bytes,
        )
        if response.status_code in {401, 403}:
            raise AdapterFailure(
                FailureKind.AUTHENTICATION,
                "SEMrush credentials were rejected.",
                retryable=False,
            )
        if response.status_code == 429:
            raise AdapterFailure(
                FailureKind.RATE_LIMIT,
                "SEMrush rate limit was reached.",
                retryable=True,
            )
        if response.status_code >= 500:
            raise AdapterFailure(
                FailureKind.UPSTREAM,
                "SEMrush returned a temporary server error.",
                retryable=True,
            )
        if response.status_code >= 300:
            raise AdapterFailure(
                FailureKind.VALIDATION,
                "SEMrush rejected the request.",
                retryable=False,
            )
        try:
            text = response.body.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise AdapterFailure(
                FailureKind.MALFORMED_RESPONSE,
                "SEMrush returned invalid text encoding.",
                retryable=False,
            ) from exc
        notice = text.lstrip().upper()
        if notice.startswith("ERROR 50"):
            return {"columns": [], "rows": [], "availability": "available_no_rows"}
        if notice.startswith("ERROR"):
            raise AdapterFailure(
                FailureKind.VALIDATION,
                "SEMrush rejected the report request.",
                retryable=False,
            )
        reader = csv.DictReader(StringIO(text), delimiter=";")
        columns = tuple(reader.fieldnames or ())
        if (
            not columns
            or any(not value or len(value) > 255 for value in columns)
            or len(set(columns)) != len(columns)
        ):
            raise AdapterFailure(
                FailureKind.MALFORMED_RESPONSE,
                "SEMrush returned malformed report columns.",
                retryable=False,
            )
        rows: list[dict[str, str]] = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise AdapterFailure(
                    FailureKind.MALFORMED_RESPONSE,
                    "SEMrush returned a malformed report row.",
                    retryable=False,
                )
            rows.append({key: value for key, value in row.items() if key is not None})
            if len(rows) > 100_000:
                raise AdapterFailure(
                    FailureKind.MALFORMED_RESPONSE,
                    "SEMrush report exceeds the configured row limit.",
                    retryable=False,
                )
        return {"columns": list(columns), "rows": rows, "availability": "available"}
