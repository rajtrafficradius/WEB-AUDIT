"""Demo market-data mode: simulated SEMrush provider for demonstrations.

The demo transport must speak the REAL wire format (semicolon CSV, short
column codes) so the genuine report layer and persistence run unchanged, and
every persisted snapshot must be flagged ``simulated`` so the data can be
identified and replaced once a real key works.
"""

from __future__ import annotations

from django.core.cache import cache
from django.test import TestCase, override_settings

from app.domain.constants import AvailabilityStatus, RunProfile
from app.domain.models import (
    AuditRun,
    Client,
    Keyword,
    MetricObservation,
    PageSnapshot,
    Project,
    SourceSnapshot,
)
from integrations.demo_market import DemoSemrushTransport, collect_demo_market_data
from integrations.semrush_reports import map_rows, parse_response
from integrations.semrush_status import check_status
from integrations.tasks import collect_market_data


def _url(report_type: str, limit: int | None = None) -> str:
    suffix = f"&display_limit={limit}" if limit is not None else ""
    return f"https://api.semrush.com/?type={report_type}&key=demo{suffix}"


class DemoMarketBase(TestCase):
    def setUp(self) -> None:
        cache.clear()
        self.client_org = Client.objects.create(name="Demo Retailer", slug="demo-retailer")
        self.project = Project.objects.create(
            client=self.client_org,
            name="Demo SEO",
            slug="demo-seo",
            primary_domain="fardoulis.com.au",
            approved_domains=["fardoulis.com.au"],
            business_type=Project.BusinessType.ECOMMERCE,
        )
        self.run = AuditRun.objects.create(
            project=self.project,
            profile=RunProfile.QUICK,
            idempotency_key="demo-market-test",
            rule_version="2026.07",
        )
        for index, (title, h1) in enumerate(
            [
                ("Handmade Chocolate Gifts | Fardoulis", "Chocolate Gift Boxes"),
                ("Corporate Chocolate Hampers", "Corporate Hampers Sydney"),
                ("Dark Chocolate Range", "Premium Dark Chocolate"),
            ]
        ):
            PageSnapshot.objects.create(
                run=self.run,
                original_url=f"https://fardoulis.com.au/page-{index}",
                normalized_url=f"https://fardoulis.com.au/page-{index}",
                domain="fardoulis.com.au",
                approved_domain=True,
                status_code=200,
                title=title,
                h1=h1,
            )

    def tearDown(self) -> None:
        cache.clear()


class DemoTransportTests(DemoMarketBase):
    def test_reports_parse_with_the_real_report_layer(self) -> None:
        transport = DemoSemrushTransport(self.run)
        for report_type, limit in (
            ("domain_ranks", None),
            ("domain_organic", 25),
            ("domain_organic_organic", 5),
            ("backlinks_overview", None),
            ("backlinks_refdomains", 8),
        ):
            body = transport.fetch_text(_url(report_type, limit))
            rows = map_rows(parse_response(report_type, body))
            assert rows, f"{report_type} produced no rows"

    def test_keyword_phrases_derive_from_crawled_pages(self) -> None:
        transport = DemoSemrushTransport(self.run)
        body = transport.fetch_text(_url("domain_organic", 25))
        rows = map_rows(parse_response("domain_organic", body))
        phrases = " ".join(str(row["phrase"]) for row in rows)
        assert "chocolate" in phrases
        assert all(
            str(row["landing_url"]).startswith("https://fardoulis.com.au/") for row in rows
        )

    def test_output_is_deterministic_per_domain(self) -> None:
        first = DemoSemrushTransport(self.run).fetch_text(_url("domain_ranks"))
        second = DemoSemrushTransport(self.run).fetch_text(_url("domain_ranks"))
        assert first == second

    def test_unknown_report_returns_semrush_empty_error(self) -> None:
        body = DemoSemrushTransport(self.run).fetch_text(_url("nonexistent_report"))
        assert body.startswith("ERROR 50")


class DemoCollectionTests(DemoMarketBase):
    def test_collect_persists_flagged_simulated_snapshot(self) -> None:
        result = collect_demo_market_data(self.run)

        assert result.status == "available"
        assert result.units_spent == 0
        assert Keyword.objects.filter(run=self.run).exists()
        assert MetricObservation.objects.filter(
            run=self.run, metric_key="semrush.organic_keywords"
        ).exists()
        snapshot = SourceSnapshot.objects.get(pk=result.snapshot_id)
        assert snapshot.availability == AvailabilityStatus.AVAILABLE
        assert snapshot.metadata.get("simulated") is True
        assert snapshot.metadata.get("units_spent") == 0

    @override_settings(MARKET_DATA_DEMO_MODE=True, SEMRUSH_API_KEY="")
    def test_task_falls_back_to_demo_when_no_key_works(self) -> None:
        outcome = collect_market_data.apply(args=[str(self.run.pk)]).get()

        assert outcome["status"] == "available"
        assert outcome["units_spent"] == 0
        assert outcome["keywords"] > 0
        snapshot = (
            SourceSnapshot.objects.filter(run=self.run, source_type="semrush")
            .order_by("-created_at")
            .first()
        )
        assert snapshot is not None
        assert snapshot.metadata.get("simulated") is True

    @override_settings(MARKET_DATA_DEMO_MODE=False, SEMRUSH_API_KEY="")
    def test_task_stays_honest_when_demo_mode_off(self) -> None:
        outcome = collect_market_data.apply(args=[str(self.run.pk)]).get()

        assert outcome["status"] == "unavailable"
        assert not Keyword.objects.filter(run=self.run).exists()


class DemoStatusTests(TestCase):
    def setUp(self) -> None:
        cache.clear()

    def tearDown(self) -> None:
        cache.clear()

    @override_settings(MARKET_DATA_DEMO_MODE=True)
    def test_status_reads_working_in_demo_mode_without_a_key(self) -> None:
        payload = check_status(api_key="")
        assert payload["status"] == "working"
        assert payload.get("demo") is True
        assert payload["units_remaining"] is not None

    @override_settings(MARKET_DATA_DEMO_MODE=False)
    def test_status_stays_honest_with_demo_mode_off(self) -> None:
        payload = check_status(api_key="")
        assert payload["status"] == "no_key"


class DemoCompletenessTests(DemoMarketBase):
    def test_seed_fills_every_withheld_surface(self) -> None:
        from exporters.run_data import compile_run_data
        from integrations.demo_market import seed_demo_run_completeness

        collect_demo_market_data(self.run)
        seed_demo_run_completeness(self.run)
        data = compile_run_data(self.run, enrich=False)

        assert data["run"]["overall_score"] is not None
        assert all(row["score"] is not None for row in data["categories"])
        assert all(row["status"] == "available" for row in data["categories"])
        assert data["market"]["status"] == "available"
        assert data["keywords"]
        assert data["competitors"]
        assert data["backlinks"]["status"] == "available"
        assert all(
            row["baseline"] != "Unavailable" for row in data["measurement_plan"]
        )
        for kind in ("gsc", "ga4", "pagespeed"):
            snapshot = SourceSnapshot.objects.get(
                run=self.run, source_type=kind, availability=AvailabilityStatus.AVAILABLE
            )
            assert snapshot.metadata.get("simulated") is True

    def test_seed_is_idempotent_and_respects_real_scores(self) -> None:
        from integrations.demo_market import seed_demo_run_completeness

        seed_demo_run_completeness(self.run)
        first_score = self.run.health_score
        seed_demo_run_completeness(self.run)
        self.run.refresh_from_db()

        assert self.run.health_score == first_score
        assert (
            SourceSnapshot.objects.filter(run=self.run, source_type="gsc").count() == 1
        )


class DemoRefdomainTopUpTests(DemoMarketBase):
    def test_top_up_fills_backlinks_when_lite_plan_skipped_them(self) -> None:
        from app.domain.models import Backlink
        from integrations.demo_market import top_up_demo_refdomains

        snapshot = SourceSnapshot.objects.create(
            run=self.run,
            source_type="semrush",
            availability=AvailabilityStatus.AVAILABLE,
            scope="SEMrush au database; lite plan",
        )
        created = top_up_demo_refdomains(self.run)

        assert created > 0
        assert Backlink.objects.filter(run=self.run).count() == created
        snapshot.refresh_from_db()
        assert snapshot.metadata.get("refdomains_simulated") is True
        assert MetricObservation.objects.filter(
            run=self.run, metric_key="semrush.referring_domain_list"
        ).exists()

    def test_top_up_is_a_noop_when_backlinks_exist_or_no_snapshot(self) -> None:
        from integrations.demo_market import top_up_demo_refdomains

        assert top_up_demo_refdomains(self.run) == 0  # no semrush snapshot

        SourceSnapshot.objects.create(
            run=self.run,
            source_type="semrush",
            availability=AvailabilityStatus.AVAILABLE,
            scope="SEMrush au database; lite plan",
        )
        first = top_up_demo_refdomains(self.run)
        assert first > 0
        assert top_up_demo_refdomains(self.run) == 0  # already populated
