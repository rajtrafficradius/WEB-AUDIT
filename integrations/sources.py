# ruff: noqa: E501
"""Evidence-source adapters.  Missing credentials degrade explicitly."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from urllib.parse import quote, urlencode

from audit_engine.crawler import PinnedHTTPTransport, SafeTransport
from audit_engine.urls import SSRFGuard, canonical_host, normalize_url

from .base import (
    AdapterFailure,
    AdapterResult,
    AdapterStatus,
    FailureKind,
    ResilientExecutor,
)
from .semrush import PinnedSemrushTransport


@dataclass(frozen=True, slots=True)
class JSONRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    payload: Mapping[str, Any] | None = None
    timeout_seconds: float = 20.0
    max_response_bytes: int = 10_000_000


class JSONTransport(Protocol):
    def request(self, request: JSONRequest) -> Mapping[str, Any]: ...


class PinnedJSONTransport:
    """JSON transport that permits only configured provider origins."""

    def __init__(
        self,
        allowed_provider_hosts: tuple[str, ...],
        *,
        http_transport: SafeTransport | None = None,
    ) -> None:
        self.guard = SSRFGuard(allowed_provider_hosts)
        self.http_transport = http_transport or PinnedHTTPTransport()

    def request(self, request: JSONRequest) -> Mapping[str, Any]:
        method = request.method.upper()
        if method not in {"GET", "POST"}:
            raise AdapterFailure(
                FailureKind.VALIDATION, "Provider request method is invalid.", retryable=False
            )
        body = None
        headers = {"Accept": "application/json", **dict(request.headers)}
        if request.payload is not None:
            try:
                body = json.dumps(request.payload, allow_nan=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            except (TypeError, ValueError) as exc:
                raise AdapterFailure(
                    FailureKind.VALIDATION, "Provider payload is not valid JSON.", retryable=False
                ) from exc
            headers["Content-Type"] = "application/json"
        response = self.http_transport.fetch(
            self.guard.validate(request.url),
            method=method,
            headers=headers,
            body=body,
            timeout=request.timeout_seconds,
            max_bytes=request.max_response_bytes,
        )
        if response.status_code in {401, 403}:
            raise AdapterFailure(
                FailureKind.AUTHENTICATION, "Provider credentials were rejected.", retryable=False
            )
        if response.status_code == 429:
            raise AdapterFailure(
                FailureKind.RATE_LIMIT, "Provider rate limit was reached.", retryable=True
            )
        if response.status_code >= 500:
            raise AdapterFailure(
                FailureKind.UPSTREAM, "Provider returned a temporary server error.", retryable=True
            )
        if 300 <= response.status_code < 400:
            # Provider redirects are not followed, preventing credential forwarding.
            raise AdapterFailure(
                FailureKind.UPSTREAM, "Unexpected provider redirect was blocked.", retryable=False
            )
        if response.status_code >= 400:
            raise AdapterFailure(
                FailureKind.VALIDATION, "Provider rejected the request.", retryable=False
            )
        try:
            value = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdapterFailure(
                FailureKind.MALFORMED_RESPONSE, "Provider returned malformed JSON.", retryable=False
            ) from exc
        if not isinstance(value, dict):
            raise AdapterFailure(
                FailureKind.MALFORMED_RESPONSE,
                "Provider response must be a JSON object.",
                retryable=False,
            )
        return value


class SourceAdapter:
    source_name = "source"

    def __init__(
        self, transport: JSONTransport, *, executor: ResilientExecutor | None = None
    ) -> None:
        self.transport = transport
        self.executor = executor or ResilientExecutor()

    def _missing(self, credential_name: str) -> AdapterResult[Mapping[str, Any]]:
        return AdapterResult.unavailable(
            FailureKind.CONFIGURATION,
            f"{credential_name} is not configured; this source is unavailable.",
            source=self.source_name,
        )

    def _execute(self, request: JSONRequest) -> AdapterResult[Mapping[str, Any]]:
        return self.executor.call(lambda: self.transport.request(request), source=self.source_name)


class PageSpeedAdapter(SourceAdapter):
    source_name = "pagespeed"
    ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

    def __init__(
        self, api_key: str | None, transport: JSONTransport | None = None, **kwargs: Any
    ) -> None:
        super().__init__(transport or PinnedJSONTransport(("www.googleapis.com",)), **kwargs)
        self.api_key = api_key.strip() if api_key else None

    def collect(
        self, page_url: str, *, strategy: str = "mobile"
    ) -> AdapterResult[Mapping[str, Any]]:
        if not self.api_key:
            return self._missing("PageSpeed API key")
        if strategy not in {"mobile", "desktop"}:
            return AdapterResult.unavailable(
                FailureKind.VALIDATION,
                "PageSpeed strategy must be mobile or desktop.",
                source=self.source_name,
            )
        normalized = normalize_url(page_url)
        query = urlencode({"url": normalized, "strategy": strategy, "key": self.api_key})
        return self._execute(JSONRequest("GET", f"{self.ENDPOINT}?{query}", {}))


class SearchConsoleAdapter(SourceAdapter):
    source_name = "gsc"
    HOST = "searchconsole.googleapis.com"

    def __init__(
        self, access_token: str | None, transport: JSONTransport | None = None, **kwargs: Any
    ) -> None:
        super().__init__(transport or PinnedJSONTransport((self.HOST,)), **kwargs)
        self.access_token = access_token.strip() if access_token else None

    def collect(
        self,
        site_url: str,
        *,
        start_date: date,
        end_date: date,
        dimensions: tuple[str, ...] = ("query", "page"),
        row_limit: int = 25_000,
    ) -> AdapterResult[Mapping[str, Any]]:
        if not self.access_token:
            return self._missing("Google Search Console access token")
        if start_date > end_date or not 1 <= row_limit <= 25_000:
            return AdapterResult.unavailable(
                FailureKind.VALIDATION,
                "Search Console date range or row limit is invalid.",
                source=self.source_name,
            )
        if any(
            value not in {"query", "page", "country", "device", "date", "searchAppearance"}
            for value in dimensions
        ):
            return AdapterResult.unavailable(
                FailureKind.VALIDATION,
                "Search Console dimensions contain an unsupported value.",
                source=self.source_name,
            )
        endpoint = f"https://{self.HOST}/webmasters/v3/sites/{quote(site_url, safe='')}/searchAnalytics/query"
        payload = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": list(dimensions),
            "rowLimit": row_limit,
        }
        return self._execute(
            JSONRequest("POST", endpoint, {"Authorization": f"Bearer {self.access_token}"}, payload)
        )


class GA4Adapter(SourceAdapter):
    source_name = "ga4"
    HOST = "analyticsdata.googleapis.com"

    def __init__(
        self, access_token: str | None, transport: JSONTransport | None = None, **kwargs: Any
    ) -> None:
        super().__init__(transport or PinnedJSONTransport((self.HOST,)), **kwargs)
        self.access_token = access_token.strip() if access_token else None

    def collect(
        self,
        property_id: str,
        *,
        start_date: date,
        end_date: date,
        dimensions: tuple[str, ...],
        metrics: tuple[str, ...],
        row_limit: int = 100_000,
    ) -> AdapterResult[Mapping[str, Any]]:
        if not self.access_token:
            return self._missing("Google Analytics access token")
        if (
            not re.fullmatch(r"\d{1,30}", property_id)
            or start_date > end_date
            or not 1 <= row_limit <= 250_000
        ):
            return AdapterResult.unavailable(
                FailureKind.VALIDATION,
                "GA4 property, date range, or row limit is invalid.",
                source=self.source_name,
            )
        if not dimensions or not metrics or len(dimensions) > 9 or len(metrics) > 10:
            return AdapterResult.unavailable(
                FailureKind.VALIDATION,
                "GA4 dimensions and metrics are invalid.",
                source=self.source_name,
            )
        valid_name = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,79}$")
        if any(not valid_name.fullmatch(value) for value in (*dimensions, *metrics)):
            return AdapterResult.unavailable(
                FailureKind.VALIDATION, "GA4 field names are invalid.", source=self.source_name
            )
        endpoint = f"https://{self.HOST}/v1beta/properties/{property_id}:runReport"
        payload = {
            "dateRanges": [{"startDate": start_date.isoformat(), "endDate": end_date.isoformat()}],
            "dimensions": [{"name": value} for value in dimensions],
            "metrics": [{"name": value} for value in metrics],
            "limit": str(row_limit),
        }
        return self._execute(
            JSONRequest("POST", endpoint, {"Authorization": f"Bearer {self.access_token}"}, payload)
        )


class SemrushAdapter(SourceAdapter):
    source_name = "semrush"
    ENDPOINT = "https://api.semrush.com/"

    def __init__(
        self, api_key: str | None, transport: JSONTransport | None = None, **kwargs: Any
    ) -> None:
        super().__init__(transport or PinnedSemrushTransport(), **kwargs)
        self.api_key = api_key.strip() if api_key else None

    def collect(
        self,
        domain: str,
        *,
        database: str,
        report_type: str = "domain_organic",
        display_limit: int = 10_000,
    ) -> AdapterResult[Mapping[str, Any]]:
        if not self.api_key:
            return self._missing("SEMrush API key")
        try:
            safe_domain = canonical_host(domain)
        except ValueError:
            return AdapterResult.unavailable(
                FailureKind.VALIDATION, "SEMrush domain is invalid.", source=self.source_name
            )
        if not re.fullmatch(r"[a-z]{2,8}", database.casefold()) or not re.fullmatch(
            r"[a-z_]{3,40}", report_type
        ):
            return AdapterResult.unavailable(
                FailureKind.VALIDATION,
                "SEMrush report parameters are invalid.",
                source=self.source_name,
            )
        if not 1 <= display_limit <= 100_000:
            return AdapterResult.unavailable(
                FailureKind.VALIDATION, "SEMrush display limit is invalid.", source=self.source_name
            )
        query = urlencode(
            {
                "key": self.api_key,
                "type": report_type,
                "domain": safe_domain,
                "database": database.casefold(),
                "display_limit": display_limit,
                "export_escape": "1",
            }
        )
        return self._execute(JSONRequest("GET", f"{self.ENDPOINT}?{query}", {"Accept": "text/csv"}))


class ReplayAdapter(SourceAdapter):
    """Deterministic integration-test adapter; never opens the network."""

    source_name = "replay"

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = dict(payload)

    def collect(self) -> AdapterResult[Mapping[str, Any]]:
        return AdapterResult(
            status=AdapterStatus.AVAILABLE,
            data=self.payload,
            source=self.source_name,
            attempts=1,
        )
