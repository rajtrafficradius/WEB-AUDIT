"""SEMrush market-data collection: budget, caching, error handling, persistence.

Every test drives a fake transport.  No test may open a socket or reach the
real SEMrush API.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from django.core.cache import cache
from django.test import TestCase

from app.domain.constants import AvailabilityStatus, RunProfile, StageStatus
from app.domain.models import (
    AuditRun,
    Backlink,
    Client,
    Keyword,
    MetricObservation,
    Project,
    RunStage,
    SourceSnapshot,
)
from integrations.base import ResilientExecutor, RetryPolicy
from integrations.market_data import MarketDataService, _derive_intent
from integrations.semrush_reports import (
    SemrushReportError,
    SemrushUsageError,
    build_request,
    cache_key_for_url,
    map_rows,
    parse_response,
)

API_KEY = "test-key-not-a-real-credential"  # noqa: S105 - fixture value

DOMAIN_RANKS_CSV = (
    "Db;Dn;Rk;Or;Ot;Oc;Ad;At;Ac\n"
    "au;example.com.au;123456;412;3100;5400.25;12;80;220.5\n"
)
DOMAIN_ORGANIC_CSV = (
    "Ph;Po;Pp;Nq;Cp;Co;Nr;Tr;Tc;Td;Ur\n"
    "chocolate gift box;3;5;1900;1.25;0.42;1200000;12.5;8.1;0.5,0.6;"
    "https://example.com.au/gifts\n"
    "buy dark chocolate;8;9;720;0.95;0.61;540000;4.2;3.3;0.4,0.5;"
    "https://example.com.au/dark\n"
)
COMPETITORS_CSV = (
    "Dn;Cr;Np;Or;Ot;Oc;Ad\n"
    "rival.com.au;0.72;140;980;5400;9100.5;30\n"
    "second-rival.com.au;0.55;90;610;2200;3400.75;8\n"
)
BACKLINKS_OVERVIEW_CSV = (
    "ascore;total;domains_num;urls_num;ips_num;follows_num;nofollows_num\n"
    "41;15230;620;9800;540;13100;2130\n"
)
REFDOMAINS_CSV = (
    "domain_ascore;domain;backlinks_num;country;first_seen;last_seen\n"
    "55;news.example.org;42;au;1600000000;1700000000\n"
    "33;blog.example.net;7;nz;1610000000;1710000000\n"
)

FULL_BODIES = {
    "domain_ranks": DOMAIN_RANKS_CSV,
    "domain_organic": DOMAIN_ORGANIC_CSV,
    "domain_organic_organic": COMPETITORS_CSV,
    "backlinks_overview": BACKLINKS_OVERVIEW_CSV,
    "backlinks_refdomains": REFDOMAINS_CSV,
}


class FakeTransport:
    """Records every URL it is handed and replays a canned body per report."""

    def __init__(self, bodies: dict[str, str]) -> None:
        self.bodies = dict(bodies)
        self.calls: list[str] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def report_types(self) -> list[str]:
        return [parse_qs(urlsplit(url).query)["type"][0] for url in self.calls]

    def fetch_text(self, url: str, *, timeout_seconds: float = 20.0) -> str:
        self.calls.append(url)
        report_type = parse_qs(urlsplit(url).query)["type"][0]
        return self.bodies.get(report_type, "ERROR 50 :: NOTHING FOUND")


def _executor() -> ResilientExecutor:
    return ResilientExecutor(
        retry_policy=RetryPolicy(max_attempts=1), sleeper=lambda _seconds: None
    )


# --------------------------------------------------------------------------
# Pure report layer
# --------------------------------------------------------------------------


def test_per_line_report_without_display_limit_raises() -> None:
    with pytest.raises(SemrushUsageError):
        build_request(
            "domain_organic", api_key=API_KEY, target="example.com.au", database="au"
        )


def test_flat_rate_report_needs_no_display_limit() -> None:
    request = build_request("backlinks_overview", api_key=API_KEY, target="example.com.au")
    assert request.estimated_units == 40
    assert "display_limit" not in request.params


def test_estimated_units_match_documented_costs() -> None:
    organic = build_request(
        "domain_organic", api_key=API_KEY, target="example.com.au", database="au", display_limit=50
    )
    competitors = build_request(
        "domain_organic_organic",
        api_key=API_KEY,
        target="example.com.au",
        database="au",
        display_limit=3,
    )
    assert organic.estimated_units == 500
    assert competitors.estimated_units == 120


def test_cache_key_excludes_the_api_key() -> None:
    first = build_request(
        "domain_ranks", api_key="key-one", target="example.com.au", database="au", display_limit=1
    )
    second = build_request(
        "domain_ranks", api_key="key-two", target="example.com.au", database="au", display_limit=1
    )
    assert first.cache_key == second.cache_key
    assert "key-one" not in first.cache_key
    assert cache_key_for_url("https://api.semrush.com/?key=abc&type=domain_ranks") == (
        cache_key_for_url("https://api.semrush.com/?type=domain_ranks")
    )


def test_error_50_is_empty_not_an_error() -> None:
    response = parse_response("domain_organic", "ERROR 50 :: NOTHING FOUND")
    assert response.is_empty
    assert response.empty_reason


def test_error_132_raises_a_circuit_breaking_error() -> None:
    with pytest.raises(SemrushReportError) as excinfo:
        parse_response("domain_organic", "ERROR 132 :: API UNITS BALANCE IS ZERO")
    assert excinfo.value.code == 132
    assert excinfo.value.breaks_circuit is True


def test_error_120_and_134_break_the_circuit() -> None:
    for body, code in (
        ("ERROR 120 :: WRONG KEY", 120),
        ("ERROR 134 :: TOTAL LIMIT EXCEEDED", 134),
    ):
        with pytest.raises(SemrushReportError) as excinfo:
            parse_response("domain_ranks", body)
        assert excinfo.value.code == code
        assert excinfo.value.breaks_circuit is True


def test_row_mapping_types_and_dates() -> None:
    keywords = map_rows(parse_response("domain_organic", DOMAIN_ORGANIC_CSV))
    assert keywords[0]["phrase"] == "chocolate gift box"
    assert keywords[0]["search_volume"] == 1900
    assert keywords[0]["cpc"] == pytest.approx(1.25)
    assert keywords[0]["landing_url"] == "https://example.com.au/gifts"
    refdomains = map_rows(parse_response("backlinks_refdomains", REFDOMAINS_CSV))
    assert refdomains[0]["domain"] == "news.example.org"
    assert refdomains[0]["first_seen"] == "2020-09-13"
    overview = map_rows(parse_response("backlinks_overview", BACKLINKS_OVERVIEW_CSV))[0]
    assert overview["referring_domains"] == 620


def test_intent_is_blank_without_a_signal() -> None:
    assert _derive_intent("buy dark chocolate") == "transactional"
    assert _derive_intent("best chocolate gifts") == "commercial"
    assert _derive_intent("how to temper chocolate") == "informational"
    assert _derive_intent("chocolate gift box") == ""


# --------------------------------------------------------------------------
# Service behaviour
# --------------------------------------------------------------------------


class MarketDataServiceTests(TestCase):
    def setUp(self) -> None:
        cache.clear()
        self.client_org = Client.objects.create(name="Sample Retailer", slug="sample-retailer")
        self.project = Project.objects.create(
            client=self.client_org,
            name="Enterprise SEO",
            slug="enterprise-seo",
            primary_domain="example.com.au",
            approved_domains=["example.com.au"],
            business_type=Project.BusinessType.ECOMMERCE,
        )
        self.run = AuditRun.objects.create(
            project=self.project,
            profile=RunProfile.STANDARD,
            idempotency_key="market-data-test",
            rule_version="2026.07",
        )

    def tearDown(self) -> None:
        cache.clear()

    def _service(self, transport: FakeTransport, **kwargs: object) -> MarketDataService:
        options: dict[str, object] = {
            "transport": transport,
            "api_key": API_KEY,
            "tier": "lite",
            "unit_budget": 700,
            "database": "au",
            "enabled": True,
            "executor": _executor(),
        }
        options.update(kwargs)
        return MarketDataService(self.run, **options)

    def test_missing_api_key_is_unavailable_without_any_network_call(self) -> None:
        transport = FakeTransport(FULL_BODIES)
        result = self._service(transport, api_key="").collect()

        self.assertEqual(result.status, "unavailable")
        self.assertIn("No SEMrush API key", result.unavailable_reason or "")
        self.assertEqual(transport.call_count, 0)
        snapshot = SourceSnapshot.objects.get(run=self.run, source_type="semrush")
        self.assertEqual(snapshot.availability, AvailabilityStatus.UNAVAILABLE)
        self.assertTrue(snapshot.unavailable_reason)

    def test_disabled_market_data_is_unavailable_without_any_network_call(self) -> None:
        transport = FakeTransport(FULL_BODIES)
        result = self._service(transport, enabled=False).collect()

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(transport.call_count, 0)

    def test_budget_ceiling_skips_a_call_it_cannot_afford(self) -> None:
        transport = FakeTransport(FULL_BODIES)
        result = self._service(transport, unit_budget=600).collect()

        by_report = {record.report: record for record in result.calls}
        # ranks (10) + organic (500) = 510; competitors (120) would breach 600.
        self.assertEqual(by_report["domain_organic_organic"].status, "skipped_over_budget")
        self.assertIn("600-unit", by_report["domain_organic_organic"].reason or "")
        self.assertEqual(by_report["backlinks_overview"].status, "ok")
        self.assertEqual(result.units_spent, 550)
        self.assertLessEqual(result.units_spent, 600)
        self.assertNotIn("domain_organic_organic", transport.report_types())

    def test_lite_plan_spends_the_documented_unit_total(self) -> None:
        transport = FakeTransport(FULL_BODIES)
        result = self._service(transport).collect()

        self.assertEqual(result.status, "available")
        self.assertEqual(result.units_spent, 670)
        self.assertEqual(transport.call_count, 4)

    def test_units_balance_error_trips_the_circuit_breaker(self) -> None:
        transport = FakeTransport({"domain_ranks": "ERROR 132 :: API UNITS BALANCE IS ZERO"})
        result = self._service(transport).collect()

        self.assertEqual(result.status, "unavailable")
        self.assertIn("132", result.unavailable_reason or "")
        self.assertEqual(transport.call_count, 1)
        halted = [record for record in result.calls if record.status == "halted"]
        self.assertEqual(len(halted), 4)
        snapshot = SourceSnapshot.objects.get(run=self.run, source_type="semrush")
        self.assertEqual(snapshot.availability, AvailabilityStatus.UNAVAILABLE)
        self.assertIn("132", snapshot.unavailable_reason)

    def test_nothing_found_is_empty_and_stops_speculative_calls(self) -> None:
        transport = FakeTransport({"domain_ranks": "ERROR 50 :: NOTHING FOUND"})
        result = self._service(transport).collect()

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(result.units_spent, 10)
        skipped = [record for record in result.calls if record.status == "skipped"]
        self.assertEqual(len(skipped), 3)
        self.assertIn("no data", (result.unavailable_reason or "").casefold())
        self.assertEqual(Keyword.objects.filter(run=self.run).count(), 0)

    def test_rows_persist_into_keyword_backlink_and_metric_tables(self) -> None:
        transport = FakeTransport(FULL_BODIES)
        result = self._service(transport, tier="standard", unit_budget=2_000).collect()

        self.assertEqual(result.status, "available")
        self.assertEqual(result.units_spent, 1_650)

        snapshot = SourceSnapshot.objects.get(run=self.run, source_type="semrush")
        self.assertEqual(snapshot.availability, AvailabilityStatus.AVAILABLE)
        self.assertEqual(snapshot.unavailable_reason, "")
        self.assertEqual(snapshot.metadata["units_spent"], 1_650)
        self.assertEqual(snapshot.metadata["tier"], "standard")
        self.assertEqual(snapshot.metadata["database"], "au")

        keywords = {item.normalized_phrase: item for item in Keyword.objects.filter(run=self.run)}
        self.assertEqual(len(keywords), 2)
        gift = keywords["chocolate gift box"]
        self.assertEqual(gift.search_volume, 1900)
        self.assertEqual(float(gift.cpc), 1.25)
        self.assertEqual(float(gift.position), 3.0)
        self.assertEqual(gift.availability, AvailabilityStatus.AVAILABLE)
        self.assertEqual(gift.source_snapshot_id, snapshot.pk)
        self.assertEqual(keywords["buy dark chocolate"].intent, "transactional")
        # domain_organic does not expose Kd, so difficulty stays unknown.
        self.assertIsNone(gift.difficulty)

        backlinks = {item.referring_domain: item for item in Backlink.objects.filter(run=self.run)}
        self.assertEqual(set(backlinks), {"news.example.org", "blog.example.net"})
        self.assertEqual(float(backlinks["news.example.org"].authority_score), 55.0)
        self.assertEqual(backlinks["news.example.org"].first_seen.isoformat(), "2020-09-13")

        metrics = {
            item.metric_key: item for item in MetricObservation.objects.filter(run=self.run)
        }
        self.assertEqual(int(metrics["semrush.organic_keywords"].numeric_value), 412)
        self.assertEqual(int(metrics["semrush.referring_domains"].numeric_value), 620)
        self.assertEqual(int(metrics["semrush.authority_score"].numeric_value), 41)
        self.assertEqual(len(metrics["semrush.competitors"].json_value), 2)
        self.assertEqual(
            metrics["semrush.competitors"].json_value[0]["domain"], "rival.com.au"
        )
        self.assertEqual(len(metrics["semrush.keywords"].json_value), 2)
        self.assertEqual(result.keywords_persisted, 2)
        self.assertEqual(result.competitors_persisted, 2)
        self.assertEqual(result.referring_domains_persisted, 2)

    def test_cache_prevents_a_second_identical_request(self) -> None:
        transport = FakeTransport(FULL_BODIES)
        first = self._service(transport).collect()
        self.assertEqual(first.status, "available")
        self.assertEqual(transport.call_count, 4)

        second = self._service(transport).collect()
        self.assertEqual(second.status, "available")
        self.assertEqual(transport.call_count, 4)
        self.assertEqual(second.units_spent, 0)
        self.assertTrue(all(record.cached for record in second.calls))

    def test_collector_never_raises_on_transport_failure(self) -> None:
        class ExplodingTransport:
            calls = 0

            def fetch_text(self, url: str, *, timeout_seconds: float = 20.0) -> str:
                type(self).calls += 1
                raise ConnectionError("network down")

        result = self._service(ExplodingTransport()).collect()  # type: ignore[arg-type]
        self.assertEqual(result.status, "unavailable")
        self.assertTrue(result.unavailable_reason)
        self.assertEqual(result.units_spent, 0)


class MarketDataTaskTests(TestCase):
    def setUp(self) -> None:
        cache.clear()
        self.client_org = Client.objects.create(name="Task Retailer", slug="task-retailer")
        self.project = Project.objects.create(
            client=self.client_org,
            name="Enterprise SEO",
            slug="enterprise-seo",
            primary_domain="example.com.au",
            approved_domains=["example.com.au"],
            business_type=Project.BusinessType.SERVICE,
        )
        self.run = AuditRun.objects.create(
            project=self.project,
            profile=RunProfile.QUICK,
            idempotency_key="market-data-task",
            rule_version="2026.07",
        )

    def tearDown(self) -> None:
        cache.clear()

    def test_task_lands_on_the_consumed_analysis_queue(self) -> None:
        from integrations.tasks import collect_market_data

        self.assertEqual(collect_market_data.name, "studio.analysis.collect_market_data")

    def test_task_records_the_enriching_stage_and_never_raises(self) -> None:
        from integrations.tasks import collect_market_data

        with self.settings(MARKET_DATA_ENABLED=False, SEMRUSH_API_KEY=""):
            payload = collect_market_data.apply(args=[str(self.run.pk)]).get()

        self.assertEqual(payload["status"], "unavailable")
        stage = RunStage.objects.get(run=self.run, name="enriching")
        self.assertEqual(stage.status, StageStatus.SKIPPED)
        self.assertIsNotNone(stage.heartbeat_at)

    def test_task_is_idempotent_when_a_fresh_snapshot_exists(self) -> None:
        from integrations.tasks import collect_market_data

        with self.settings(MARKET_DATA_ENABLED=False, SEMRUSH_API_KEY=""):
            collect_market_data.apply(args=[str(self.run.pk)]).get()
            second = collect_market_data.apply(args=[str(self.run.pk)]).get()

        self.assertTrue(second["idempotent"])
        self.assertEqual(
            SourceSnapshot.objects.filter(run=self.run, source_type="semrush").count(), 1
        )

    def test_task_tolerates_an_unknown_run(self) -> None:
        from integrations.tasks import collect_market_data

        payload = collect_market_data.apply(
            args=["00000000-0000-0000-0000-000000000000"]
        ).get()
        self.assertEqual(payload["reason"], "run_not_found")
