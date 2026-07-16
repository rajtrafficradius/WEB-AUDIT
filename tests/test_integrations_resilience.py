from __future__ import annotations

import socket
from datetime import date

from audit_engine.crawler import FetchResponse
from audit_engine.urls import SSRFGuard
from integrations.base import (
    AdapterFailure,
    AdapterStatus,
    CircuitBreaker,
    CircuitState,
    FailureKind,
    ResilientExecutor,
    RetryPolicy,
)
from integrations.semrush import PinnedSemrushTransport
from integrations.sources import (
    GA4Adapter,
    JSONRequest,
    PageSpeedAdapter,
    ReplayAdapter,
    SearchConsoleAdapter,
)


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def test_executor_retries_retryable_failure_then_succeeds() -> None:
    attempts = 0
    delays: list[float] = []

    def operation() -> dict[str, bool]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise AdapterFailure(FailureKind.RATE_LIMIT, "Rate limited.", retryable=True)
        return {"ok": True}

    executor = ResilientExecutor(
        retry_policy=RetryPolicy(3, 0.1, 1, 0),
        sleeper=delays.append,
        random_source=lambda: 0,
    )
    result = executor.call(operation, source="fixture")
    assert result.status is AdapterStatus.AVAILABLE
    assert result.data == {"ok": True}
    assert result.attempts == 3
    assert delays == [0.1, 0.2]


def test_executor_does_not_retry_non_retryable_failure_or_leak_exception_text() -> None:
    calls = 0

    def operation() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("secret-token-value")

    result = ResilientExecutor(sleeper=lambda _: None).call(operation, source="fixture")
    assert result.status is AdapterStatus.UNAVAILABLE
    assert calls == 1
    assert "secret-token-value" not in result.errors[0].message


def test_circuit_breaker_opens_and_allows_one_half_open_probe() -> None:
    clock = Clock()
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout_seconds=10, clock=clock)
    breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert not breaker.allow_call()
    clock.value = 11
    assert breaker.allow_call()
    assert breaker.state is CircuitState.HALF_OPEN
    assert not breaker.allow_call()
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED


class RecordingTransport:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {"rows": []}
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        return self.payload


def test_missing_credentials_produce_truthful_unavailable_states_without_network() -> None:
    transport = RecordingTransport()
    assert (
        PageSpeedAdapter(None, transport).collect("https://example.com/").status
        is AdapterStatus.UNAVAILABLE
    )
    assert (
        SearchConsoleAdapter(None, transport)
        .collect(
            "https://example.com/",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )
        .status
        is AdapterStatus.UNAVAILABLE
    )
    assert (
        GA4Adapter(None, transport)
        .collect(
            "123",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            dimensions=("landingPage",),
            metrics=("sessions",),
        )
        .status
        is AdapterStatus.UNAVAILABLE
    )
    assert transport.requests == []


def test_ga4_validates_provider_fields_before_request() -> None:
    transport = RecordingTransport()
    result = GA4Adapter("token", transport).collect(
        "not-a-property",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        dimensions=("landingPage",),
        metrics=("sessions",),
    )
    assert result.status is AdapterStatus.UNAVAILABLE
    assert result.errors[0].kind is FailureKind.VALIDATION
    assert not transport.requests


def test_replay_adapter_is_network_free_and_deterministic() -> None:
    first = ReplayAdapter({"source": "synthetic-test-fixture"}).collect()
    second = ReplayAdapter({"source": "synthetic-test-fixture"}).collect()
    assert first.status is AdapterStatus.AVAILABLE
    assert first.data == second.data


def test_semrush_transport_parses_wire_format_without_inventing_values() -> None:
    class StaticHTTP:
        def fetch(self, target, **kwargs):
            del kwargs
            return FetchResponse(
                200,
                {"content-type": "text/csv"},
                b"Domain;Organic Keywords\nexample.com;12\n",
                target.normalized_url,
            )

    def resolver(host: str, port: int, family: int, kind: int):
        del host, family
        return [(socket.AF_INET, kind, 6, "", ("93.184.216.34", port))]

    transport = PinnedSemrushTransport(
        http_transport=StaticHTTP(),
        guard=SSRFGuard(("api.semrush.com",), resolver=resolver),
    )
    result = transport.request(JSONRequest("GET", "https://api.semrush.com/?type=test", {}))
    assert result["columns"] == ["Domain", "Organic Keywords"]
    assert result["rows"] == [{"Domain": "example.com", "Organic Keywords": "12"}]
