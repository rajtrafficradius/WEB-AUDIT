"""Versioned, deterministic scoring and prioritisation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .models import BusinessProfile, Finding, Severity

SCORING_VERSION = "1.0.0"

SEVERITY_PENALTIES: Mapping[Severity, float] = MappingProxyType(
    {
        Severity.CRITICAL: 30.0,
        Severity.HIGH: 18.0,
        Severity.MEDIUM: 8.0,
        Severity.LOW: 3.0,
        Severity.INFO: 0.0,
    }
)

CATEGORY_WEIGHTS: Mapping[BusinessProfile, Mapping[str, float]] = MappingProxyType(
    {
        BusinessProfile.SERVICE_SAAS: MappingProxyType(
            {
                "technical": 25,
                "on_page": 20,
                "performance": 10,
                "analytics": 10,
                "keyword_architecture": 15,
                "authority": 10,
                "cro": 5,
                "geo_aeo": 5,
            }
        ),
        BusinessProfile.LOCAL: MappingProxyType(
            {
                "technical": 20,
                "on_page": 15,
                "performance": 10,
                "analytics": 10,
                "keyword_architecture": 10,
                "authority": 10,
                "cro": 5,
                "local": 15,
                "geo_aeo": 5,
            }
        ),
        BusinessProfile.ECOMMERCE: MappingProxyType(
            {
                "technical": 20,
                "on_page": 15,
                "performance": 10,
                "analytics": 10,
                "keyword_architecture": 10,
                "authority": 10,
                "cro": 10,
                "ecommerce": 10,
                "geo_aeo": 5,
            }
        ),
        BusinessProfile.HYBRID: MappingProxyType(
            {
                "technical": 15,
                "on_page": 15,
                "performance": 10,
                "analytics": 10,
                "keyword_architecture": 10,
                "authority": 10,
                "cro": 10,
                "local": 7.5,
                "ecommerce": 7.5,
                "geo_aeo": 5,
            }
        ),
    }
)


@dataclass(frozen=True, slots=True)
class CategoryScore:
    category: str
    score: float
    penalty: float
    evidence_coverage: float
    finding_count: int


@dataclass(frozen=True, slots=True)
class Scorecard:
    version: str
    business_profile: BusinessProfile
    categories: tuple[CategoryScore, ...]
    weighted_coverage: float
    overall_score: float | None
    overall_unavailable_reason: str | None


@dataclass(frozen=True, slots=True)
class PriorityResult:
    score: float
    band: str
    version: str = SCORING_VERSION


def _bounded(value: float, name: str) -> float:
    if not 0 <= value <= 100:
        raise ValueError(f"{name} must be between 0 and 100")
    return float(value)


def category_score(
    category: str,
    findings: Iterable[Finding],
    *,
    evidence_coverage: float,
    rule_multipliers: Mapping[str, float] | None = None,
) -> CategoryScore:
    if not 0 <= evidence_coverage <= 1:
        raise ValueError("evidence_coverage must be between 0 and 1")
    multipliers = rule_multipliers or {}
    selected = [finding for finding in findings if finding.category == category]
    penalty = 0.0
    for finding in selected:
        multiplier = float(multipliers.get(finding.rule_id, 1.0))
        if not 0 <= multiplier <= 3:
            raise ValueError(f"Rule multiplier for {finding.rule_id} is outside 0..3")
        penalty += SEVERITY_PENALTIES[finding.severity] * finding.affected_share * multiplier
    capped = min(100.0, penalty)
    return CategoryScore(
        category=category,
        score=round(100.0 - capped, 2),
        penalty=round(capped, 2),
        evidence_coverage=round(evidence_coverage, 4),
        finding_count=len(selected),
    )


def scorecard(
    business_profile: BusinessProfile,
    findings: Iterable[Finding],
    evidence_coverage: Mapping[str, float],
    *,
    rule_multipliers: Mapping[str, float] | None = None,
    publication_threshold: float = 0.70,
) -> Scorecard:
    """Score categories and withhold the overall number when evidence is sparse."""

    if not 0 <= publication_threshold <= 1:
        raise ValueError("publication_threshold must be between 0 and 1")
    findings_tuple = tuple(findings)
    weights = CATEGORY_WEIGHTS[business_profile]
    if abs(sum(weights.values()) - 100.0) > 0.001:
        raise RuntimeError("Category weight matrix must total 100")
    categories: list[CategoryScore] = []
    weighted_coverage = 0.0
    weighted_score = 0.0
    for category, weight in weights.items():
        coverage = float(evidence_coverage.get(category, 0.0))
        result = category_score(
            category,
            findings_tuple,
            evidence_coverage=coverage,
            rule_multipliers=rule_multipliers,
        )
        categories.append(result)
        weighted_coverage += coverage * weight / 100.0
        weighted_score += result.score * weight / 100.0
    publish = weighted_coverage >= publication_threshold
    return Scorecard(
        version=SCORING_VERSION,
        business_profile=business_profile,
        categories=tuple(categories),
        weighted_coverage=round(weighted_coverage, 4),
        overall_score=round(weighted_score, 2) if publish else None,
        overall_unavailable_reason=None
        if publish
        else (
            f"Weighted evidence coverage {weighted_coverage:.1%} is below the "
            f"{publication_threshold:.0%} publication threshold"
        ),
    )


def priority_score(
    *,
    impact: float,
    evidence_confidence: float,
    reach: float,
    business_criticality: float,
    dependency_urgency: float,
    effort: float,
) -> PriorityResult:
    values = {
        "impact": _bounded(impact, "impact"),
        "evidence_confidence": _bounded(evidence_confidence, "evidence_confidence"),
        "reach": _bounded(reach, "reach"),
        "business_criticality": _bounded(business_criticality, "business_criticality"),
        "dependency_urgency": _bounded(dependency_urgency, "dependency_urgency"),
        "effort": _bounded(effort, "effort"),
    }
    score = (
        values["impact"] * 0.30
        + values["evidence_confidence"] * 0.20
        + values["reach"] * 0.15
        + values["business_criticality"] * 0.15
        + values["dependency_urgency"] * 0.10
        + (100.0 - values["effort"]) * 0.10
    )
    if score >= 75:
        band = "P1"
    elif score >= 55:
        band = "P2"
    elif score >= 35:
        band = "P3"
    else:
        band = "P4"
    return PriorityResult(round(score, 2), band)
