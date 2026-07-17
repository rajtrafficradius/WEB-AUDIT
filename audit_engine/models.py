"""Canonical, framework-independent evidence and audit value objects.

These objects deliberately contain no ORM or provider behaviour.  They are the
stable contract shared by collectors, deterministic rules, generation, and
exporters.  All timestamps are timezone-aware and every assertion derived from
external data carries provenance.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID


class ContractError(ValueError):
    """Raised when a canonical record would violate evidence invariants."""


class Availability(StrEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class SourceKind(StrEnum):
    CRAWL = "crawl"
    GSC = "gsc"
    GA4 = "ga4"
    SEMRUSH = "semrush"
    PAGESPEED = "pagespeed"
    AHREFS_IMPORT = "ahrefs_import"
    SCREAMING_FROG_IMPORT = "screaming_frog_import"
    BRIGHTLOCAL_IMPORT = "brightlocal_import"
    GBP_IMPORT = "gbp_import"
    MAPPED_IMPORT = "mapped_import"
    HUMAN_VERIFIED = "human_verified"
    DERIVED = "derived"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class RiskClass(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    DANGEROUS = "dangerous"


class BusinessProfile(StrEnum):
    SERVICE_SAAS = "service_saas"
    LOCAL = "local"
    ECOMMERCE = "ecommerce"
    HYBRID = "hybrid"


class RunProfile(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    ENTERPRISE = "enterprise"


@dataclass(frozen=True, slots=True)
class RunLimits:
    page_budget: int
    pagespeed_sample_budget: int
    analytics_months: int
    competitor_budget: int
    deep_dive_budget: int
    content_asset_cap: int

    def __post_init__(self) -> None:
        if any(
            value < 0
            for value in (
                self.page_budget,
                self.pagespeed_sample_budget,
                self.analytics_months,
                self.competitor_budget,
                self.deep_dive_budget,
                self.content_asset_cap,
            )
        ):
            raise ContractError("Run limits cannot be negative")
        if self.deep_dive_budget > self.competitor_budget:
            raise ContractError("Deep-dive budget cannot exceed competitor budget")


RUN_LIMITS: Mapping[RunProfile, RunLimits] = MappingProxyType(
    {
        RunProfile.QUICK: RunLimits(250, 10, 0, 0, 0, 0),
        RunProfile.STANDARD: RunLimits(2_500, 50, 16, 10, 3, 10),
        RunProfile.ENTERPRISE: RunLimits(25_000, 200, 16, 10, 3, 20),
    }
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(f"{name} must be timezone-aware")


def _nonempty(value: str, name: str, *, max_length: int = 2_000) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ContractError(f"{name} cannot be empty")
    if len(cleaned) > max_length:
        raise ContractError(f"{name} exceeds {max_length} characters")
    return cleaned


def _uuid(value: str, name: str) -> None:
    try:
        UUID(value)
    except (ValueError, TypeError) as exc:
        raise ContractError(f"{name} must be a UUID string") from exc


def _json_safe(value: Any, name: str) -> None:
    try:
        json.dumps(value, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{name} must be finite JSON data") from exc


@dataclass(frozen=True, slots=True)
class Provenance:
    """How, when, and with what confidence a value was observed or derived."""

    source: SourceKind
    captured_at: datetime
    availability: Availability = Availability.AVAILABLE
    locale: str | None = None
    device: str | None = None
    scope: str | None = None
    rule_version: str | None = None
    confidence: float = 1.0
    unavailable_reason: str | None = None
    source_snapshot_id: str | None = None

    def __post_init__(self) -> None:
        _aware(self.captured_at, "captured_at")
        if not 0 <= self.confidence <= 1:
            raise ContractError("confidence must be between 0 and 1")
        if self.availability is Availability.UNAVAILABLE and not self.unavailable_reason:
            raise ContractError("Unavailable evidence requires unavailable_reason")
        if self.availability is Availability.AVAILABLE and self.unavailable_reason:
            raise ContractError("Available evidence cannot have unavailable_reason")
        if self.source is SourceKind.DERIVED and not self.rule_version:
            raise ContractError("Derived evidence requires rule_version")
        if self.source_snapshot_id is not None:
            _uuid(self.source_snapshot_id, "source_snapshot_id")


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    id: str
    project_id: str
    source: SourceKind
    captured_at: datetime
    content_sha256: str
    object_key: str
    availability: Availability
    unavailable_reason: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _uuid(self.project_id, "project_id")
        _aware(self.captured_at, "captured_at")
        if len(self.content_sha256) != 64 or any(
            c not in "0123456789abcdef" for c in self.content_sha256.lower()
        ):
            raise ContractError("content_sha256 must be a 64-character hexadecimal digest")
        _nonempty(self.object_key, "object_key", max_length=1_024)
        if self.availability is Availability.UNAVAILABLE and not self.unavailable_reason:
            raise ContractError("Unavailable snapshot requires unavailable_reason")
        _json_safe(dict(self.metadata), "metadata")


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    id: str
    project_id: str
    key: str
    value: Any
    provenance: Provenance
    unit: str | None = None
    source_url: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _uuid(self.project_id, "project_id")
        _nonempty(self.key, "key", max_length=255)
        _json_safe(self.value, "value")
        if self.source_url:
            parsed = urlsplit(self.source_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ContractError("source_url must be an absolute HTTP(S) URL")


@dataclass(frozen=True, slots=True)
class MetricObservation:
    id: str
    project_id: str
    name: str
    value: float | int | None
    provenance: Provenance
    evidence_ids: tuple[str, ...]
    unit: str | None = None
    dimensions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _uuid(self.project_id, "project_id")
        _nonempty(self.name, "name", max_length=255)
        for evidence_id in self.evidence_ids:
            _uuid(evidence_id, "evidence_id")
        if self.provenance.availability is Availability.AVAILABLE and self.value is None:
            raise ContractError("Available metric requires a value")
        if self.value is not None:
            _json_safe(self.value, "value")
        _json_safe(dict(self.dimensions), "dimensions")


@dataclass(frozen=True, slots=True)
class PageSnapshot:
    id: str
    project_id: str
    original_url: str
    normalized_url: str
    status_code: int | None
    captured_at: datetime
    evidence_id: str
    title: str | None = None
    meta_description: str | None = None
    h1: tuple[str, ...] = ()
    canonical_url: str | None = None
    robots_directives: tuple[str, ...] = ()
    content_type: str | None = None
    body_sha256: str | None = None
    links: tuple[str, ...] = ()
    word_count: int | None = None
    body_bytes: int = 0
    response_ms: int | None = None
    images_total: int = 0
    images_missing_alt: int = 0
    schema_types: tuple[str, ...] = ()
    h2: tuple[str, ...] = ()
    external_links: tuple[str, ...] = ()
    og_title: bool = False
    og_description: bool = False
    lang: str | None = None
    viewport: bool = False
    hreflang_count: int = 0
    analytics_tags: tuple[str, ...] = ()
    url_depth: int = 0
    redirect_chain: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _uuid(self.project_id, "project_id")
        _uuid(self.evidence_id, "evidence_id")
        _aware(self.captured_at, "captured_at")
        if self.status_code is not None and not 100 <= self.status_code <= 599:
            raise ContractError("status_code must be a valid HTTP status")
        for name, url in (
            ("original_url", self.original_url),
            ("normalized_url", self.normalized_url),
        ):
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ContractError(f"{name} must be an absolute HTTP(S) URL")
        if self.body_sha256 is not None and len(self.body_sha256) != 64:
            raise ContractError("body_sha256 must be a SHA-256 hexadecimal digest")
        for name in ("body_bytes", "images_total", "images_missing_alt", "hreflang_count",
                     "url_depth"):
            if getattr(self, name) < 0:
                raise ContractError(f"{name} cannot be negative")
        for name in ("word_count", "response_ms"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ContractError(f"{name} cannot be negative")
        if self.images_missing_alt > self.images_total:
            raise ContractError("images_missing_alt cannot exceed images_total")


@dataclass(frozen=True, slots=True)
class Finding:
    id: str
    project_id: str
    category: str
    rule_id: str
    rule_version: str
    severity: Severity
    title: str
    description: str
    evidence_ids: tuple[str, ...]
    affected_urls: tuple[str, ...] = ()
    affected_share: float = 0.0
    confidence: float = 1.0
    risk: RiskClass = RiskClass.LOW

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _uuid(self.project_id, "project_id")
        _nonempty(self.category, "category", max_length=80)
        _nonempty(self.rule_id, "rule_id", max_length=120)
        _nonempty(self.rule_version, "rule_version", max_length=40)
        _nonempty(self.title, "title", max_length=300)
        _nonempty(self.description, "description", max_length=4_000)
        if self.severity is not Severity.INFO and not self.evidence_ids:
            raise ContractError("Non-informational findings require evidence")
        for evidence_id in self.evidence_ids:
            _uuid(evidence_id, "evidence_id")
        if not 0 <= self.affected_share <= 1:
            raise ContractError("affected_share must be between 0 and 1")
        if not 0 <= self.confidence <= 1:
            raise ContractError("confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class Recommendation:
    id: str
    project_id: str
    finding_ids: tuple[str, ...]
    title: str
    rationale: str
    implementation: str
    evidence_ids: tuple[str, ...]
    risk: RiskClass
    requires_admin_approval: bool

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _uuid(self.project_id, "project_id")
        if not self.finding_ids or not self.evidence_ids:
            raise ContractError("Recommendations require findings and evidence")
        for value in (*self.finding_ids, *self.evidence_ids):
            _uuid(value, "linked id")
        _nonempty(self.title, "title", max_length=300)
        _nonempty(self.rationale, "rationale", max_length=4_000)
        _nonempty(self.implementation, "implementation", max_length=10_000)
        if self.risk in {RiskClass.HIGH, RiskClass.DANGEROUS} and not self.requires_admin_approval:
            raise ContractError("High-risk recommendations require admin approval")


@dataclass(frozen=True, slots=True)
class ActionCandidate:
    id: str
    recommendation_id: str
    title: str
    impact: float
    evidence_confidence: float
    reach: float
    business_criticality: float
    dependency_urgency: float
    effort: float
    owner_role: str | None = None
    dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _uuid(self.recommendation_id, "recommendation_id")
        _nonempty(self.title, "title", max_length=300)
        for name in (
            "impact",
            "evidence_confidence",
            "reach",
            "business_criticality",
            "dependency_urgency",
            "effort",
        ):
            value = getattr(self, name)
            if not 0 <= value <= 100:
                raise ContractError(f"{name} must be between 0 and 100")
        for dependency in self.dependencies:
            _uuid(dependency, "dependency")


@dataclass(frozen=True, slots=True)
class VerifiedFact:
    """A fact explicitly approved for use in model-generated prose."""

    key: str
    value: Any
    evidence_ids: tuple[str, ...]
    as_of: datetime

    def __post_init__(self) -> None:
        _nonempty(self.key, "key", max_length=255)
        _json_safe(self.value, "value")
        if not self.evidence_ids:
            raise ContractError("Verified facts require evidence")
        for evidence_id in self.evidence_ids:
            _uuid(evidence_id, "evidence_id")
        _aware(self.as_of, "as_of")


def require_evidence_references(records: Sequence[EvidenceRecord], ids: Sequence[str]) -> None:
    """Fail closed if a derived record references evidence outside its fact pack."""

    available = {record.id for record in records}
    missing = sorted(set(ids) - available)
    if missing:
        raise ContractError(f"Unknown evidence references: {', '.join(missing)}")
