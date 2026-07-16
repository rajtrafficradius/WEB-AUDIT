"""Timeout, retry, circuit-breaker, and unavailable-state primitives."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

T = TypeVar("T")


class AdapterStatus(StrEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class FailureKind(StrEnum):
    CONFIGURATION = "configuration"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    VALIDATION = "validation"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    UPSTREAM = "upstream"
    CIRCUIT_OPEN = "circuit_open"
    MALFORMED_RESPONSE = "malformed_response"
    INTERNAL = "internal"


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class AdapterFailure(RuntimeError):
    def __init__(self, kind: FailureKind, safe_message: str, *, retryable: bool) -> None:
        super().__init__(safe_message)
        self.kind = kind
        self.safe_message = safe_message
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class AdapterError:
    kind: FailureKind
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class AdapterResult(Generic[T]):
    status: AdapterStatus
    data: T | None
    errors: tuple[AdapterError, ...] = ()
    attempts: int = 0
    source: str | None = None

    @classmethod
    def unavailable(
        cls,
        kind: FailureKind,
        message: str,
        *,
        retryable: bool = False,
        attempts: int = 0,
        source: str | None = None,
    ) -> AdapterResult[T]:
        return cls(
            AdapterStatus.UNAVAILABLE,
            None,
            (AdapterError(kind, message, retryable),),
            attempts,
            source,
        )


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 4.0
    jitter_ratio: float = 0.10

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays cannot be negative")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between zero and one")

    def delay(self, completed_attempt: int, random_value: float) -> float:
        base = min(
            self.max_delay_seconds, self.base_delay_seconds * (2 ** max(0, completed_attempt - 1))
        )
        jitter = base * self.jitter_ratio * max(0.0, min(1.0, random_value))
        return base + jitter


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        reset_timeout_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1 or reset_timeout_seconds <= 0:
            raise ValueError("Circuit breaker settings are invalid")
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = reset_timeout_seconds
        self.clock = clock
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    def allow_call(self) -> bool:
        with self._lock:
            if self._state is CircuitState.OPEN:
                assert self._opened_at is not None
                if self.clock() - self._opened_at >= self.reset_timeout_seconds:
                    self._state = CircuitState.HALF_OPEN
                    return True
                return False
            # One half-open probe only. Concurrent probes fail closed.
            return self._state is not CircuitState.HALF_OPEN

    def record_success(self) -> None:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = self.clock()


class ResilientExecutor:
    def __init__(
        self,
        *,
        retry_policy: RetryPolicy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        random_source: Callable[[], float] = random.random,
    ) -> None:
        self.retry_policy = retry_policy or RetryPolicy()
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.sleeper = sleeper
        self.random_source = random_source

    def call(self, operation: Callable[[], T], *, source: str) -> AdapterResult[T]:
        if not self.circuit_breaker.allow_call():
            return AdapterResult.unavailable(
                FailureKind.CIRCUIT_OPEN,
                "The upstream source is temporarily paused after repeated failures.",
                retryable=True,
                source=source,
            )
        errors: list[AdapterError] = []
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                value = operation()
            except AdapterFailure as exc:
                failure = exc
            except TimeoutError:
                failure = AdapterFailure(
                    FailureKind.TIMEOUT,
                    "The upstream source did not respond before the timeout.",
                    retryable=True,
                )
            except (ConnectionError, OSError):
                failure = AdapterFailure(
                    FailureKind.UPSTREAM,
                    "The upstream source could not be reached.",
                    retryable=True,
                )
            except Exception:
                # Provider exception text is intentionally not returned; it can
                # contain credentials, request bodies, or internal stack data.
                failure = AdapterFailure(
                    FailureKind.INTERNAL,
                    "The source adapter failed safely.",
                    retryable=False,
                )
            else:
                self.circuit_breaker.record_success()
                return AdapterResult(AdapterStatus.AVAILABLE, value, tuple(errors), attempt, source)
            errors.append(AdapterError(failure.kind, failure.safe_message, failure.retryable))
            if not failure.retryable or attempt >= self.retry_policy.max_attempts:
                self.circuit_breaker.record_failure()
                return AdapterResult(
                    AdapterStatus.UNAVAILABLE, None, tuple(errors), attempt, source
                )
            self.sleeper(self.retry_policy.delay(attempt, self.random_source()))
        raise AssertionError("Retry loop must return")
