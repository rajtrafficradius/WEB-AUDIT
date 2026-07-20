"""Budgeted SEMrush market-data collection.

Every call is costed *before* it is issued and charged against a hard per-run
unit ceiling.  Nothing is estimated, extrapolated, or invented: when a report is
skipped, fails, or returns no rows, the reason is recorded and the corresponding
deliverable stays "Unavailable".
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from app.domain.constants import AvailabilityStatus
from app.domain.crypto import decrypt_credentials
from app.domain.models import AuditRun, Backlink, Keyword, MetricObservation, SourceSnapshot
from audit_engine.urls import canonical_host

from . import semrush_reports as reports
from .base import AdapterFailure, AdapterStatus, ResilientExecutor
from .semrush_reports import (
    PinnedSemrushTextTransport,
    RawTextTransport,
    SemrushReportError,
    SemrushUsageError,
)

logger = logging.getLogger(__name__)

SOURCE_TYPE = "semrush"
CACHE_NAMESPACE = "semrush:v1"
MAX_CACHE_DAYS = 30
KEYWORD_PERSIST_CAP = 500
REFDOMAIN_PERSIST_CAP = 500

TRANSACTIONAL_TOKENS = frozenset(
    {
        "buy", "price", "prices", "pricing", "cost", "cheap", "hire", "quote",
        "quotes", "order", "sale", "shop", "booking", "book", "delivery",
    }
)
COMMERCIAL_TOKENS = frozenset(
    {"best", "top", "review", "reviews", "compare", "comparison", "vs", "alternatives"}
)
INFORMATIONAL_TOKENS = frozenset(
    {"how", "what", "why", "when", "guide", "tutorial", "ideas", "tips", "meaning"}
)


@dataclass(frozen=True, slots=True)
class PlannedCall:
    """One report in a tier plan."""

    report_type: str
    purpose: str
    display_limit: int | None = None
    display_sort: str | None = None
    gates_on_data: bool = False


PLANS: dict[str, tuple[PlannedCall, ...]] = {
    "lite": (
        PlannedCall("domain_ranks", "domain authority and organic totals", 1, gates_on_data=True),
        PlannedCall("domain_organic", "ranking keywords", 50, "tr_desc"),
        PlannedCall("domain_organic_organic", "organic competitors", 3),
        PlannedCall("backlinks_overview", "backlink profile totals"),
    ),
    "standard": (
        PlannedCall("domain_ranks", "domain authority and organic totals", 1, gates_on_data=True),
        PlannedCall("domain_organic", "ranking keywords", 100, "tr_desc"),
        PlannedCall("domain_organic_organic", "organic competitors", 5),
        PlannedCall("backlinks_overview", "backlink profile totals"),
        PlannedCall("backlinks_refdomains", "top referring domains", 10),
    ),
    "deep": (
        PlannedCall("domain_ranks", "domain authority and organic totals", 1, gates_on_data=True),
        PlannedCall("domain_organic", "ranking keywords", 200, "tr_desc"),
        PlannedCall("domain_organic_organic", "organic competitors", 5),
        PlannedCall("backlinks_overview", "backlink profile totals"),
        PlannedCall("backlinks_refdomains", "top referring domains", 5),
    ),
}


@dataclass(slots=True)
class CallRecord:
    """Audit trail for one planned call, issued or not."""

    report: str
    purpose: str
    status: str
    units: int = 0
    rows: int = 0
    cached: bool = False
    reason: str | None = None


@dataclass(slots=True)
class MarketDataResult:
    """Structured, always-returned outcome.  Never raises into the caller."""

    status: str
    tier: str
    database: str
    units_spent: int = 0
    unavailable_reason: str | None = None
    fetched_at: str | None = None
    snapshot_id: str | None = None
    calls: list[CallRecord] = field(default_factory=list)
    keywords_persisted: int = 0
    competitors_persisted: int = 0
    referring_domains_persisted: int = 0

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["calls"] = [asdict(item) for item in self.calls]
        return payload


def _decimal(value: float | int | None, places: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal(places))
    except (InvalidOperation, ValueError):
        return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _derive_intent(phrase: str) -> str:
    """Deterministic modifier-based intent.  Blank when no signal is present."""

    tokens = {token for token in phrase.casefold().replace("-", " ").split() if token}
    if not tokens:
        return ""
    if tokens & TRANSACTIONAL_TOKENS or "near me" in phrase.casefold():
        return "transactional"
    if tokens & COMMERCIAL_TOKENS:
        return "commercial"
    if tokens & INFORMATIONAL_TOKENS:
        return "informational"
    return ""


def _cache_days() -> int:
    raw = int(getattr(settings, "SEMRUSH_CACHE_DAYS", MAX_CACHE_DAYS) or 0)
    return max(0, min(MAX_CACHE_DAYS, raw))


class MarketDataService:
    """Collect, cache, budget, and persist SEMrush market data for one run."""

    def __init__(
        self,
        run: AuditRun,
        *,
        transport: RawTextTransport | None = None,
        api_key: str | None = None,
        tier: str | None = None,
        unit_budget: int | None = None,
        database: str | None = None,
        executor: ResilientExecutor | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.run = run
        self.transport = transport or PinnedSemrushTextTransport()
        raw_key = api_key if api_key is not None else self._project_api_key(run)
        self.api_key = (raw_key or "").strip()
        requested_tier = (tier or getattr(settings, "SEMRUSH_PLAN_TIER", "lite") or "lite").strip()
        self.tier = requested_tier if requested_tier in PLANS else "lite"
        self.unit_budget = max(
            0,
            int(
                unit_budget
                if unit_budget is not None
                else getattr(settings, "SEMRUSH_UNIT_BUDGET", 700)
            ),
        )
        self.database = self._resolve_database(database)
        self.executor = executor or ResilientExecutor()
        self.enabled = (
            enabled
            if enabled is not None
            else bool(getattr(settings, "MARKET_DATA_ENABLED", False))
        )
        self._units_spent = 0
        self._circuit_open_reason: str | None = None

    # -- configuration -------------------------------------------------

    @staticmethod
    def _project_api_key(run: AuditRun) -> str:
        """Prefer the key an administrator stored against this project.

        A per-project credential lets one deployment serve clients on separate
        SEMrush subscriptions; the deployment-wide environment key remains the
        fallback so existing installs keep working.
        """

        connection = (
            run.project.connections.filter(provider="semrush")
            .exclude(encrypted_credentials="")
            .order_by("-updated_at")
            .first()
        )
        if connection is not None:
            try:
                credentials = decrypt_credentials(
                    connection.encrypted_credentials, connection.encryption_key_id
                )
            except (ImproperlyConfigured, ValueError):
                logger.warning(
                    "Stored SEMrush credential for project %s could not be decrypted; "
                    "falling back to the environment key.",
                    run.project_id,
                )
            else:
                stored = str(credentials.get("api_key") or "").strip()
                if stored:
                    return stored
        return str(getattr(settings, "SEMRUSH_API_KEY", "") or "")

    def _resolve_database(self, database: str | None) -> str:
        candidate = (database or "").strip().casefold()
        if not candidate:
            candidate = (self.run.project.country_code or "").strip().casefold()
        if not candidate:
            candidate = str(getattr(settings, "SEMRUSH_DATABASE", "au")).strip().casefold()
        return candidate or "au"

    @property
    def budget_remaining(self) -> int:
        return max(0, self.unit_budget - self._units_spent)

    # -- collection ----------------------------------------------------

    def collect(self) -> MarketDataResult:
        result = MarketDataResult(
            status="unavailable", tier=self.tier, database=self.database
        )
        try:
            return self._collect(result)
        except Exception:  # noqa: BLE001 - fail open; the package must still build
            logger.exception("SEMrush market-data collection failed", extra={"run": str(self.run.pk)})
            result.status = "unavailable"
            result.unavailable_reason = (
                "SEMrush market data could not be collected because the collector "
                "failed unexpectedly. No market metrics are reported for this run."
            )
            self._persist_snapshot(result, payloads={})
            return result

    def _collect(self, result: MarketDataResult) -> MarketDataResult:
        if not self.enabled:
            result.unavailable_reason = (
                "Market data collection is disabled for this deployment "
                "(MARKET_DATA_ENABLED is off)."
            )
            self._persist_snapshot(result, payloads={})
            return result
        if not self.api_key:
            result.unavailable_reason = (
                "No SEMrush API key is configured, so no keyword, competitor, or "
                "backlink metrics were requested."
            )
            self._persist_snapshot(result, payloads={})
            return result
        try:
            target = canonical_host(self.run.project.primary_domain)
        except ValueError:
            result.unavailable_reason = (
                "The project's primary domain is not a valid host, so SEMrush "
                "reports could not be requested."
            )
            self._persist_snapshot(result, payloads={})
            return result

        payloads: dict[str, list[dict[str, Any]]] = {}
        skip_reason: str | None = None
        for planned in PLANS[self.tier]:
            if self._circuit_open_reason:
                result.calls.append(
                    CallRecord(planned.report_type, planned.purpose, "halted",
                               reason=self._circuit_open_reason)
                )
                continue
            if skip_reason:
                result.calls.append(
                    CallRecord(planned.report_type, planned.purpose, "skipped", reason=skip_reason)
                )
                continue
            record, rows = self._run_call(planned, target)
            result.calls.append(record)
            if record.status == "ok":
                payloads[planned.report_type] = rows
                if planned.gates_on_data and not rows:
                    skip_reason = (
                        "SEMrush holds no data for this domain, so the remaining "
                        "reports were not requested and no units were spent on them."
                    )
            elif record.status == "halted":
                self._circuit_open_reason = record.reason

        result.units_spent = self._units_spent
        has_data = any(payloads.values())
        if has_data:
            result.status = "available"
            result.fetched_at = timezone.now().isoformat()
        else:
            result.status = "unavailable"
            result.unavailable_reason = self._circuit_open_reason or skip_reason or (
                "SEMrush returned no rows for this domain, so market, keyword, "
                "competitor, and backlink metrics are unavailable."
            )
        snapshot = self._persist_snapshot(result, payloads=payloads)
        if snapshot is not None and has_data:
            self._persist_rows(snapshot, payloads, result)
        return result

    def _run_call(self, planned: PlannedCall, target: str) -> tuple[CallRecord, list[dict[str, Any]]]:
        record = CallRecord(planned.report_type, planned.purpose, "skipped")
        try:
            request = reports.build_request(
                planned.report_type,
                api_key=self.api_key,
                target=target,
                database=self.database,
                display_limit=planned.display_limit,
                display_sort=planned.display_sort,
            )
        except SemrushUsageError as exc:
            record.reason = str(exc)
            record.status = "invalid"
            return record, []

        cache_key = f"{CACHE_NAMESPACE}:{request.cache_key}"
        cached_body = cache.get(cache_key) if _cache_days() else None
        if cached_body is None:
            if request.estimated_units > self.budget_remaining:
                record.reason = (
                    f"Skipped: this report costs {request.estimated_units} API units "
                    f"but only {self.budget_remaining} of the {self.unit_budget}-unit "
                    "per-run budget remained."
                )
                record.status = "skipped_over_budget"
                return record, []
            outcome = self.executor.call(
                lambda url=request.url: self.transport.fetch_text(url), source=SOURCE_TYPE
            )
            if outcome.status is not AdapterStatus.AVAILABLE or outcome.data is None:
                message = outcome.errors[0].message if outcome.errors else "unknown transport error"
                record.status = "failed"
                record.reason = f"SEMrush request failed: {message}"
                return record, []
            body = str(outcome.data)
            self._units_spent += request.estimated_units
            record.units = request.estimated_units
        else:
            body = str(cached_body)
            record.cached = True

        try:
            response = reports.parse_response(planned.report_type, body)
        except SemrushReportError as exc:
            record.status = "halted" if exc.breaks_circuit else "failed"
            record.reason = (
                f"SEMrush returned error {exc.code} ({exc.message}); "
                + (
                    "all further SEMrush requests for this run were stopped."
                    if exc.breaks_circuit
                    else "this report is unavailable."
                )
            )
            return record, []
        except AdapterFailure as exc:  # defensive: transport-shaped failure
            record.status = "failed"
            record.reason = exc.safe_message
            return record, []

        if not record.cached and _cache_days():
            cache.set(cache_key, body, timeout=_cache_days() * 86_400)
        rows = reports.map_rows(response)
        record.status = "ok"
        record.rows = len(rows)
        if not rows:
            record.reason = response.empty_reason or "SEMrush returned no rows."
        return record, rows

    # -- persistence ---------------------------------------------------

    def _persist_snapshot(
        self, result: MarketDataResult, *, payloads: dict[str, list[dict[str, Any]]]
    ) -> SourceSnapshot | None:
        available = result.status == "available"
        record_count = sum(len(rows) for rows in payloads.values())
        try:
            snapshot = SourceSnapshot.objects.create(
                run=self.run,
                source_type=SOURCE_TYPE,
                availability=(
                    AvailabilityStatus.AVAILABLE if available else AvailabilityStatus.UNAVAILABLE
                ),
                unavailable_reason="" if available else (result.unavailable_reason or "unknown"),
                record_count=record_count,
                captured_at=timezone.now(),
                locale=self.run.project.locale,
                scope=f"SEMrush {self.database} database; {self.tier} plan",
                confidence=Decimal("1") if available else Decimal("0"),
                metadata={
                    "units_spent": result.units_spent,
                    "tier": result.tier,
                    "database": result.database,
                    "unit_budget": self.unit_budget,
                    "calls": [asdict(item) for item in result.calls],
                },
            )
        except Exception:  # noqa: BLE001 - persistence must not break the run
            logger.exception("Could not persist the SEMrush source snapshot")
            return None
        result.snapshot_id = str(snapshot.pk)
        return snapshot

    def _persist_rows(
        self,
        snapshot: SourceSnapshot,
        payloads: dict[str, list[dict[str, Any]]],
        result: MarketDataResult,
    ) -> None:
        captured = timezone.now()
        ranks = (payloads.get("domain_ranks") or [{}])[0]
        overview = (payloads.get("backlinks_overview") or [{}])[0]
        scalars = {
            "semrush.organic_keywords": ranks.get("organic_keywords"),
            "semrush.organic_traffic": ranks.get("organic_traffic"),
            "semrush.organic_cost": ranks.get("organic_cost"),
            "semrush.adwords_keywords": ranks.get("adwords_keywords"),
            "semrush.rank": ranks.get("rank"),
            "semrush.authority_score": overview.get("authority_score"),
            "semrush.backlinks_total": overview.get("backlinks_total"),
            "semrush.referring_domains": overview.get("referring_domains"),
            "semrush.referring_ips": overview.get("referring_ips"),
            "semrush.follow_links": overview.get("follow_links"),
            "semrush.nofollow_links": overview.get("nofollow_links"),
        }
        for metric_key, value in scalars.items():
            numeric = _decimal(value, "0.000001")
            if numeric is None:
                continue
            self._metric(snapshot, metric_key, captured, numeric_value=numeric)

        keywords = payloads.get("domain_organic") or []
        if keywords:
            self._metric(
                snapshot,
                "semrush.keywords",
                captured,
                json_value=keywords[:KEYWORD_PERSIST_CAP],
            )
            result.keywords_persisted = self._persist_keywords(snapshot, keywords, captured)

        competitors = payloads.get("domain_organic_organic") or []
        if competitors:
            self._metric(snapshot, "semrush.competitors", captured, json_value=competitors)
            result.competitors_persisted = len(competitors)

        refdomains = payloads.get("backlinks_refdomains") or []
        if refdomains:
            self._metric(
                snapshot,
                "semrush.referring_domain_list",
                captured,
                json_value=refdomains[:REFDOMAIN_PERSIST_CAP],
            )
            result.referring_domains_persisted = self._persist_backlinks(
                snapshot, refdomains, captured
            )

    def _metric(
        self,
        snapshot: SourceSnapshot,
        metric_key: str,
        captured: datetime,
        *,
        numeric_value: Decimal | None = None,
        json_value: Any = None,
    ) -> None:
        if numeric_value is None and json_value is None:
            return
        MetricObservation.objects.create(
            run=self.run,
            source_snapshot=snapshot,
            metric_key=metric_key,
            numeric_value=numeric_value,
            json_value=json_value,
            availability=AvailabilityStatus.AVAILABLE,
            captured_at=captured,
            locale=self.run.project.locale,
            scope=f"SEMrush {self.database} database",
            confidence=Decimal("1"),
        )

    def _persist_keywords(
        self, snapshot: SourceSnapshot, rows: list[dict[str, Any]], captured: datetime
    ) -> int:
        country = (self.run.project.country_code or "").strip().upper()[:2]
        locale = self.run.project.locale
        seen: set[str] = set()
        created = 0
        for row in rows[:KEYWORD_PERSIST_CAP]:
            phrase = (row.get("phrase") or "").strip()
            if not phrase:
                continue
            normalized = " ".join(phrase.casefold().split())[:500]
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            volume = row.get("search_volume")
            Keyword.objects.update_or_create(
                run=self.run,
                normalized_phrase=normalized,
                country_code=country,
                locale=locale,
                defaults={
                    "source_snapshot": snapshot,
                    "phrase": phrase[:500],
                    "intent": _derive_intent(phrase),
                    "search_volume": volume if isinstance(volume, int) and volume >= 0 else None,
                    "cpc": _decimal(row.get("cpc"), "0.0001"),
                    "position": _decimal(row.get("position"), "0.01"),
                    "availability": AvailabilityStatus.AVAILABLE,
                    "unavailable_reason": "",
                    "captured_at": captured,
                    "scope": f"SEMrush {self.database} organic positions",
                    "confidence": Decimal("1"),
                },
            )
            created += 1
        return created

    def _persist_backlinks(
        self, snapshot: SourceSnapshot, rows: list[dict[str, Any]], captured: datetime
    ) -> int:
        target_url = f"https://{self.run.project.primary_domain}/"
        created = 0
        seen: set[str] = set()
        for row in rows[:REFDOMAIN_PERSIST_CAP]:
            domain = (row.get("domain") or "").strip().casefold()
            if not domain or domain in seen or len(domain) > 253:
                continue
            seen.add(domain)
            backlink_count = row.get("backlinks")
            Backlink.objects.update_or_create(
                run=self.run,
                source_url=f"https://{domain}/",
                target_url=target_url,
                defaults={
                    "source_snapshot": snapshot,
                    "referring_domain": domain,
                    "authority_score": _decimal(row.get("authority_score"), "0.01"),
                    "link_type": "referring_domain",
                    "first_seen": _parse_date(row.get("first_seen")),
                    "last_seen": _parse_date(row.get("last_seen")),
                    "availability": AvailabilityStatus.AVAILABLE,
                    "unavailable_reason": "",
                    "captured_at": captured,
                    "scope": (
                        f"SEMrush referring domain; {backlink_count} backlinks"
                        if isinstance(backlink_count, int)
                        else "SEMrush referring domain"
                    )[:255],
                    "confidence": Decimal("1"),
                },
            )
            created += 1
        return created


def collect_market_data_for_run(run: AuditRun, **kwargs: Any) -> MarketDataResult:
    """Convenience entry point used by the Celery task and management tooling."""

    return MarketDataService(run, **kwargs).collect()
