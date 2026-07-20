"""SEMrush Analytics report layer: typed requests, unit accounting, CSV mapping.

This module is deliberately free of Django imports so the request/parse/cost
rules can be unit-tested and reasoned about without a settings module.  It sits
on top of the DNS-pinned HTTP transport used by the crawler and exposes the raw
response body, because SEMrush reports failures as an HTTP 200 body beginning
``ERROR <code> :: <MESSAGE>`` and the numeric code decides whether the caller
may keep spending API units.
"""

from __future__ import annotations

import csv
import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import StringIO
from typing import Any, Protocol
from urllib.parse import urlencode

from audit_engine.crawler import PinnedHTTPTransport, SafeTransport
from audit_engine.urls import SSRFGuard

from .base import AdapterFailure, FailureKind

API_ENDPOINT = "https://api.semrush.com/"
API_HOST = "api.semrush.com"
MAX_DISPLAY_LIMIT = 100_000
MAX_RESPONSE_BYTES = 10_000_000

#: ``ERROR 50 :: NOTHING FOUND`` is an empty result, not a failure.
EMPTY_RESULT_CODE = 50
#: Codes that make every further request in the run pointless or harmful.
CIRCUIT_BREAKING_CODES = frozenset({120, 131, 132, 134})

ERROR_PATTERN = re.compile(r"^ERROR\s+(\d+)\s*::\s*(.*)$", re.IGNORECASE)


class SemrushReportError(RuntimeError):
    """A SEMrush API-level error carried in a 200 response body."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"SEMrush ERROR {code}: {message}")
        self.code = code
        self.message = message

    @property
    def breaks_circuit(self) -> bool:
        return self.code in CIRCUIT_BREAKING_CODES


class SemrushUsageError(ValueError):
    """A caller-side misuse that would risk uncontrolled unit spend."""


@dataclass(frozen=True, slots=True)
class ReportSpec:
    """Cost and shape of one SEMrush Analytics report."""

    report_type: str
    target_param: str
    columns: tuple[str, ...]
    per_line_units: int = 0
    flat_units: int = 0
    uses_database: bool = True
    uses_target_type: bool = False

    @property
    def is_flat_rate(self) -> bool:
        return self.flat_units > 0

    def estimate_units(self, display_limit: int | None) -> int:
        if self.is_flat_rate:
            return self.flat_units
        if display_limit is None:
            raise SemrushUsageError(
                f"{self.report_type} is billed per returned line; an explicit "
                "display_limit is mandatory (the API default is 10,000 lines)."
            )
        return self.per_line_units * display_limit


REPORTS: Mapping[str, ReportSpec] = {
    "domain_ranks": ReportSpec(
        "domain_ranks",
        "domain",
        ("Db", "Dn", "Rk", "Or", "Ot", "Oc", "Ad", "At", "Ac"),
        per_line_units=10,
    ),
    "domain_organic": ReportSpec(
        "domain_organic",
        "domain",
        ("Ph", "Po", "Pp", "Nq", "Cp", "Co", "Nr", "Tr", "Tc", "Td", "Ur"),
        per_line_units=10,
    ),
    "domain_organic_organic": ReportSpec(
        "domain_organic_organic",
        "domain",
        ("Dn", "Cr", "Np", "Or", "Ot", "Oc", "Ad"),
        per_line_units=40,
    ),
    "domain_adwords": ReportSpec(
        "domain_adwords",
        "domain",
        ("Ph", "Po", "Pp", "Nq", "Cp", "Co", "Nr", "Tr", "Tc", "Ur"),
        per_line_units=20,
    ),
    "url_organic": ReportSpec(
        "url_organic",
        "url",
        ("Ph", "Po", "Nq", "Cp", "Co", "Tr", "Tc"),
        per_line_units=10,
    ),
    "phrase_this": ReportSpec(
        "phrase_this",
        "phrase",
        ("Ph", "Nq", "Cp", "Co", "Nr", "Td"),
        per_line_units=10,
    ),
    "phrase_all": ReportSpec(
        "phrase_all",
        "phrase",
        ("Ph", "Nq", "Cp", "Co", "Nr"),
        per_line_units=10,
    ),
    "phrase_kdi": ReportSpec(
        "phrase_kdi",
        "phrase",
        ("Ph", "Kd"),
        per_line_units=50,
    ),
    "backlinks_overview": ReportSpec(
        "backlinks_overview",
        "target",
        ("ascore", "total", "domains_num", "urls_num", "ips_num", "follows_num", "nofollows_num"),
        flat_units=40,
        uses_database=False,
        uses_target_type=True,
    ),
    "backlinks_refdomains": ReportSpec(
        "backlinks_refdomains",
        "target",
        ("domain_ascore", "domain", "backlinks_num", "country", "first_seen", "last_seen"),
        per_line_units=40,
        uses_database=False,
        uses_target_type=True,
    ),
}


@dataclass(frozen=True, slots=True)
class SemrushRequest:
    """A fully-formed, costed SEMrush request."""

    report_type: str
    url: str
    cache_key: str
    estimated_units: int
    display_limit: int | None
    params: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReportResponse:
    """Parsed report body.  ``error_code`` is only set for non-empty failures."""

    report_type: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, str], ...]
    empty_reason: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.rows


class RawTextTransport(Protocol):
    """Return the decoded response body for an already-validated SEMrush URL."""

    def fetch_text(self, url: str, *, timeout_seconds: float = 20.0) -> str: ...


class PinnedSemrushTextTransport:
    """DNS-pinned SEMrush transport that preserves the raw body.

    ``PinnedSemrushTransport`` parses CSV eagerly and collapses every API error
    into one opaque failure.  The budget logic needs the numeric error code, so
    this transport returns text and lets the report layer classify it.
    """

    def __init__(
        self,
        *,
        http_transport: SafeTransport | None = None,
        guard: SSRFGuard | None = None,
    ) -> None:
        self.guard = guard or SSRFGuard((API_HOST,))
        self.http_transport = http_transport or PinnedHTTPTransport()

    def fetch_text(self, url: str, *, timeout_seconds: float = 20.0) -> str:
        response = self.http_transport.fetch(
            self.guard.validate(url),
            method="GET",
            headers={"Accept": "text/csv"},
            timeout=timeout_seconds,
            max_bytes=MAX_RESPONSE_BYTES,
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
            return response.body.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise AdapterFailure(
                FailureKind.MALFORMED_RESPONSE,
                "SEMrush returned invalid text encoding.",
                retryable=False,
            ) from exc


def _clean(value: str) -> str:
    return value.replace("﻿", "").strip()


def cache_key_for_url(url: str) -> str:
    """SHA-256 of the request URL with the API key removed.

    The key must never enter a cache key, a log line, or an on-disk artifact.
    """

    stripped = re.sub(r"([?&])key=[^&]*&?", r"\1", url)
    stripped = stripped.rstrip("?&")
    return hashlib.sha256(stripped.encode("utf-8")).hexdigest()


def build_request(
    report_type: str,
    *,
    api_key: str,
    target: str,
    database: str | None = None,
    display_limit: int | None = None,
    display_sort: str | None = None,
    display_filter: str | None = None,
    export_columns: Sequence[str] | None = None,
    target_type: str = "root_domain",
) -> SemrushRequest:
    """Build a costed SEMrush request, refusing any unbounded per-line call."""

    try:
        spec = REPORTS[report_type]
    except KeyError as exc:
        raise SemrushUsageError(f"Unknown SEMrush report type: {report_type}") from exc
    if not api_key or not api_key.strip():
        raise SemrushUsageError("A SEMrush API key is required to build a request.")
    if not target or not target.strip():
        raise SemrushUsageError("A SEMrush request target is required.")
    if not spec.is_flat_rate:
        # The API default of 10,000 lines turns one forgotten parameter into a
        # 100,000-unit charge.  Refuse rather than guess.
        if display_limit is None:
            raise SemrushUsageError(
                f"{report_type} requires an explicit display_limit; the SEMrush "
                "default of 10,000 lines would be billed in full."
            )
        if not isinstance(display_limit, int) or isinstance(display_limit, bool):
            raise SemrushUsageError("display_limit must be an integer.")
        if not 1 <= display_limit <= MAX_DISPLAY_LIMIT:
            raise SemrushUsageError(
                f"display_limit must be between 1 and {MAX_DISPLAY_LIMIT}."
            )
    columns = tuple(export_columns) if export_columns else spec.columns
    if any(not re.fullmatch(r"[A-Za-z_]{1,40}", column) for column in columns):
        raise SemrushUsageError("export_columns contains an unsupported column name.")

    params: dict[str, str] = {
        "type": spec.report_type,
        spec.target_param: target.strip(),
        "export_columns": ",".join(columns),
        "export_escape": "1",
    }
    if spec.uses_database:
        code = (database or "").strip().casefold()
        if not re.fullmatch(r"[a-z]{2,8}", code):
            raise SemrushUsageError("A valid SEMrush database code is required.")
        params["database"] = code
    if spec.uses_target_type:
        if target_type not in {"root_domain", "domain", "url"}:
            raise SemrushUsageError("target_type must be root_domain, domain, or url.")
        params["target_type"] = target_type
    if display_limit is not None:
        params["display_limit"] = str(display_limit)
    if display_sort:
        if not re.fullmatch(r"[a-z_]{2,30}", display_sort):
            raise SemrushUsageError("display_sort contains an unsupported value.")
        params["display_sort"] = display_sort
    if display_filter:
        params["display_filter"] = display_filter

    ordered = dict(sorted(params.items()))
    signed = {"key": api_key.strip(), **ordered}
    url = f"{API_ENDPOINT}?{urlencode(signed)}"
    unsigned_url = f"{API_ENDPOINT}?{urlencode(ordered)}"
    return SemrushRequest(
        report_type=spec.report_type,
        url=url,
        cache_key=cache_key_for_url(unsigned_url),
        estimated_units=spec.estimate_units(display_limit),
        display_limit=display_limit,
        params=ordered,
    )


def parse_response(report_type: str, body: str) -> ReportResponse:
    """Parse a SEMrush body, distinguishing empty results from real failures."""

    text = _clean(body)
    if not text:
        return ReportResponse(report_type, (), (), "SEMrush returned an empty body.")
    first_line = text.splitlines()[0].strip()
    match = ERROR_PATTERN.match(first_line)
    if match:
        code = int(match.group(1))
        message = match.group(2).strip() or "no message"
        if code == EMPTY_RESULT_CODE:
            return ReportResponse(
                report_type,
                (),
                (),
                "SEMrush holds no data for this target in the selected database.",
            )
        raise SemrushReportError(code, message)
    reader = csv.DictReader(StringIO(text), delimiter=";")
    columns = tuple(reader.fieldnames or ())
    if not columns or len(set(columns)) != len(columns):
        raise SemrushReportError(0, "SEMrush returned malformed report columns.")
    rows: list[dict[str, str]] = []
    for row in reader:
        if None in row:
            raise SemrushReportError(0, "SEMrush returned a malformed report row.")
        rows.append({key: (value or "") for key, value in row.items() if key is not None})
    return ReportResponse(report_type, columns, tuple(rows))


def _text(row: Mapping[str, str], key: str) -> str | None:
    value = _clean(row.get(key, ""))
    return value or None


def _int(row: Mapping[str, str], key: str) -> int | None:
    value = _clean(row.get(key, ""))
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _float(row: Mapping[str, str], key: str) -> float | None:
    value = _clean(row.get(key, ""))
    if not value:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    return parsed


def _date(row: Mapping[str, str], key: str) -> str | None:
    value = _clean(row.get(key, ""))
    if not value:
        return None
    if value.isdigit():
        try:
            return datetime.fromtimestamp(int(value), tz=UTC).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    candidate = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate).date().isoformat()
    except ValueError:
        return value[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", value) else None


def map_domain_ranks(row: Mapping[str, str]) -> dict[str, Any]:
    return {
        "database": _text(row, "Db"),
        "domain": _text(row, "Dn"),
        "rank": _int(row, "Rk"),
        "organic_keywords": _int(row, "Or"),
        "organic_traffic": _int(row, "Ot"),
        "organic_cost": _float(row, "Oc"),
        "adwords_keywords": _int(row, "Ad"),
        "adwords_traffic": _int(row, "At"),
        "adwords_cost": _float(row, "Ac"),
    }


def map_domain_organic(row: Mapping[str, str]) -> dict[str, Any]:
    return {
        "phrase": _text(row, "Ph"),
        "position": _int(row, "Po"),
        "previous_position": _int(row, "Pp"),
        "search_volume": _int(row, "Nq"),
        "cpc": _float(row, "Cp"),
        "competition": _float(row, "Co"),
        "results_count": _int(row, "Nr"),
        "traffic_share": _float(row, "Tr"),
        "traffic_cost_share": _float(row, "Tc"),
        "trend": _text(row, "Td"),
        "landing_url": _text(row, "Ur"),
    }


def map_domain_organic_organic(row: Mapping[str, str]) -> dict[str, Any]:
    return {
        "domain": _text(row, "Dn"),
        "relevance": _float(row, "Cr"),
        "common_keywords": _int(row, "Np"),
        "organic_keywords": _int(row, "Or"),
        "organic_traffic": _int(row, "Ot"),
        "organic_cost": _float(row, "Oc"),
        "adwords_keywords": _int(row, "Ad"),
    }


def map_backlinks_overview(row: Mapping[str, str]) -> dict[str, Any]:
    return {
        "authority_score": _int(row, "ascore"),
        "backlinks_total": _int(row, "total"),
        "referring_domains": _int(row, "domains_num"),
        "referring_urls": _int(row, "urls_num"),
        "referring_ips": _int(row, "ips_num"),
        "follow_links": _int(row, "follows_num"),
        "nofollow_links": _int(row, "nofollows_num"),
    }


def map_backlinks_refdomains(row: Mapping[str, str]) -> dict[str, Any]:
    return {
        "domain": _text(row, "domain"),
        "authority_score": _int(row, "domain_ascore"),
        "backlinks": _int(row, "backlinks_num"),
        "country": _text(row, "country"),
        "first_seen": _date(row, "first_seen"),
        "last_seen": _date(row, "last_seen"),
    }


ROW_MAPPERS: Mapping[str, Any] = {
    "domain_ranks": map_domain_ranks,
    "domain_organic": map_domain_organic,
    "domain_organic_organic": map_domain_organic_organic,
    "backlinks_overview": map_backlinks_overview,
    "backlinks_refdomains": map_backlinks_refdomains,
}


def map_rows(response: ReportResponse) -> list[dict[str, Any]]:
    mapper = ROW_MAPPERS.get(response.report_type)
    if mapper is None:
        return [dict(row) for row in response.rows]
    return [mapper(row) for row in response.rows]
