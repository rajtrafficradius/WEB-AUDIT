from __future__ import annotations

import json
from pathlib import Path

from audit_engine.urls import require_allowed_url

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "replay" / "kakawa_public_snapshot.json"


def test_kakawa_fixture_is_domain_bounded_and_evidence_only() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    domains = tuple(payload["project"]["approved_domains"])
    assert domains == ("kakawachocolates.com.au",)
    assert payload["as_of_date"] == "2026-07-15"
    assert payload["observations"]
    for observation in payload["observations"]:
        assert require_allowed_url(observation["source_url"], domains)
        assert observation["source"] == "official_public_webpage"
        assert 0 <= observation["confidence"] <= 1


def test_kakawa_fixture_never_substitutes_missing_provider_metrics() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    expected = {"gsc", "ga4", "semrush", "pagespeed", "openai"}
    assert set(payload["sources"]) == expected
    assert all(source["status"] == "unavailable" for source in payload["sources"].values())
    serialized = json.dumps(payload).casefold()
    for forbidden in ("organic_traffic", "ranking_forecast", "domain_authority"):
        assert forbidden not in serialized
