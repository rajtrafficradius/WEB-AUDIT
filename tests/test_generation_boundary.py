from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from audit_engine.models import VerifiedFact
from generation.openai_boundary import (
    DEFAULT_EXTRACTION_MODEL,
    DEFAULT_FINAL_MODEL,
    GenerationPurpose,
    GenerationStatus,
    OpenAIBoundary,
)
from generation.schemas import FactPack, strategy_schema
from integrations.base import ResilientExecutor, RetryPolicy


def uid() -> str:
    return str(uuid4())


def fact_pack() -> FactPack:
    evidence_id = uid()
    return FactPack(
        project_id=uid(),
        approved_domains=("example.com",),
        facts=(
            VerifiedFact(
                "homepage_title",
                "Evidence-led example",
                (evidence_id,),
                datetime.now(UTC),
            ),
        ),
        available_evidence_ids=frozenset({evidence_id}),
        known_url_statuses={"https://example.com/": 200},
        unavailable_sources={"gsc": "credential_not_configured"},
    )


def valid_document(pack: FactPack) -> dict:
    fact = pack.facts[0]
    return {
        "title": "Evidence-led strategy",
        "executive_summary": "The approved evidence supports a focused review.",
        "recommendations": [
            {
                "title": "Review the homepage title",
                "rationale": "The observed title is recorded in crawl evidence.",
                "implementation": "Review and approve an evidence-aligned title.",
                "evidence_ids": list(fact.evidence_ids),
                "risk": "low",
            }
        ],
        "claims": [
            {
                "text": "The observed homepage title is Evidence-led example.",
                "fact_keys": [fact.key],
                "evidence_ids": list(fact.evidence_ids),
            }
        ],
        "unavailable_items": [{"source": "gsc", "reason": "credential_not_configured"}],
    }


class FakeResponses:
    def __init__(self, response=None, failures: int = 0) -> None:
        self.response = response
        self.failures = failures
        self.requests = []

    def create(self, **request):
        self.requests.append(request)
        if self.failures:
            self.failures -= 1
            rate_error = type("RateLimitError", (Exception,), {})
            raise rate_error("provider details must not escape")
        return self.response


class FakeClient:
    def __init__(self, responses: FakeResponses) -> None:
        self.responses = responses


def response_for(document: dict, model: str = DEFAULT_FINAL_MODEL):
    return SimpleNamespace(
        output_text=json.dumps(document),
        output=[],
        model=model,
        usage=SimpleNamespace(input_tokens=123, output_tokens=45),
    )


def test_no_api_key_returns_clean_unavailable_state(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = OpenAIBoundary(api_key=None).generate_structured(
        task="Draft only from evidence.",
        fact_pack=fact_pack(),
        schema_name="seo_strategy",
        schema=strategy_schema(),
    )
    assert result.status is GenerationStatus.UNAVAILABLE
    assert result.ledger.attempts == 0
    assert result.ledger.response_sha256 is None
    assert "key" in (result.unavailable_reason or "").casefold()


def test_responses_request_uses_strict_schema_and_requested_default_model() -> None:
    pack = fact_pack()
    fake = FakeResponses(response_for(valid_document(pack)))
    result = OpenAIBoundary(client=FakeClient(fake)).generate_structured(
        task="Draft only from evidence.",
        fact_pack=pack,
        schema_name="seo_strategy",
        schema=strategy_schema(),
    )
    assert result.status is GenerationStatus.AVAILABLE
    request = fake.requests[0]
    assert request["model"] == DEFAULT_FINAL_MODEL
    assert request["text"]["format"]["strict"] is True
    assert request["store"] is False
    source_text = request["input"][1]["content"][0]["text"]
    assert "SOURCE_DATA_BEGIN" in source_text
    assert "credential_not_configured" in source_text
    assert result.ledger.returned_model == DEFAULT_FINAL_MODEL
    assert result.ledger.input_tokens == 123
    assert result.ledger.cost_usd is None


def test_extraction_purpose_selects_luna_model() -> None:
    pack = fact_pack()
    fake = FakeResponses(response_for(valid_document(pack), DEFAULT_EXTRACTION_MODEL))
    result = OpenAIBoundary(client=FakeClient(fake)).generate_structured(
        task="Extract approved evidence.",
        fact_pack=pack,
        schema_name="seo_strategy",
        schema=strategy_schema(),
        purpose=GenerationPurpose.EXTRACTION,
    )
    assert result.status is GenerationStatus.AVAILABLE
    assert fake.requests[0]["model"] == DEFAULT_EXTRACTION_MODEL


def test_rate_limit_is_retried_with_safe_error_contract() -> None:
    pack = fact_pack()
    fake = FakeResponses(response_for(valid_document(pack)), failures=1)
    executor = ResilientExecutor(retry_policy=RetryPolicy(2, 0, 0, 0), sleeper=lambda _: None)
    result = OpenAIBoundary(client=FakeClient(fake), executor=executor).generate_structured(
        task="Draft.",
        fact_pack=pack,
        schema_name="seo_strategy",
        schema=strategy_schema(),
    )
    assert result.status is GenerationStatus.AVAILABLE
    assert result.ledger.attempts == 2


def test_invalid_local_schema_is_rejected_even_if_provider_returns_json() -> None:
    pack = fact_pack()
    fake = FakeResponses(response_for({"title": "Incomplete"}))
    result = OpenAIBoundary(client=FakeClient(fake)).generate_structured(
        task="Draft.",
        fact_pack=pack,
        schema_name="seo_strategy",
        schema=strategy_schema(),
    )
    assert result.status is GenerationStatus.INVALID
    assert result.data is None


def test_refusal_is_recorded_without_treating_it_as_content() -> None:
    refusal = SimpleNamespace(
        output_text="",
        output=[SimpleNamespace(content=[SimpleNamespace(type="refusal", refusal="no")])],
        model=DEFAULT_FINAL_MODEL,
        usage=SimpleNamespace(input_tokens=10, output_tokens=1),
    )
    result = OpenAIBoundary(client=FakeClient(FakeResponses(refusal))).generate_structured(
        task="Draft.",
        fact_pack=fact_pack(),
        schema_name="seo_strategy",
        schema=strategy_schema(),
    )
    assert result.status is GenerationStatus.REFUSED
    assert result.data is None
