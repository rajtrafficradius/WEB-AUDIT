from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from audit_engine.graph import (
    GraphIssueCode,
    RedirectEdge,
    validate_canonical_graph,
    validate_internal_links,
    validate_redirect_graph,
)
from audit_engine.models import BusinessProfile, Finding, PageSnapshot, Severity
from audit_engine.scoring import CATEGORY_WEIGHTS, priority_score, scorecard


def uid() -> str:
    return str(uuid4())


def finding(
    *, category: str = "technical", severity: Severity = Severity.HIGH, share: float = 0.5
) -> Finding:
    return Finding(
        id=uid(),
        project_id=uid(),
        category=category,
        rule_id="test.rule",
        rule_version="1.0.0",
        severity=severity,
        title="Observed issue",
        description="Observed in the frozen test evidence.",
        evidence_ids=(uid(),),
        affected_share=share,
    )


def page(
    url: str,
    *,
    status: int = 200,
    canonical: str | None = None,
    links: tuple[str, ...] = (),
) -> PageSnapshot:
    return PageSnapshot(
        id=uid(),
        project_id=uid(),
        original_url=url,
        normalized_url=url,
        status_code=status,
        captured_at=datetime.now(UTC),
        evidence_id=uid(),
        canonical_url=canonical,
        links=links,
    )


def test_all_weight_matrices_total_one_hundred() -> None:
    assert all(sum(weights.values()) == pytest.approx(100) for weights in CATEGORY_WEIGHTS.values())


def test_scorecard_withholds_overall_below_coverage_threshold() -> None:
    result = scorecard(
        BusinessProfile.ECOMMERCE,
        (finding(),),
        dict.fromkeys(CATEGORY_WEIGHTS[BusinessProfile.ECOMMERCE], 0.5),
    )
    assert result.overall_score is None
    assert result.weighted_coverage == pytest.approx(0.5)
    assert "below" in (result.overall_unavailable_reason or "")


def test_scorecard_is_deterministic_and_penalty_is_capped() -> None:
    findings = tuple(finding(severity=Severity.CRITICAL, share=1) for _ in range(5))
    coverage = dict.fromkeys(CATEGORY_WEIGHTS[BusinessProfile.SERVICE_SAAS], 1.0)
    first = scorecard(BusinessProfile.SERVICE_SAAS, findings, coverage)
    second = scorecard(BusinessProfile.SERVICE_SAAS, findings, coverage)
    assert first == second
    technical = next(item for item in first.categories if item.category == "technical")
    assert technical.score == 0
    assert first.overall_score is not None


@pytest.mark.parametrize(
    ("inputs", "band"),
    [
        ((100, 100, 100, 100, 100, 0), "P1"),
        ((60, 60, 60, 60, 60, 40), "P2"),
        ((40, 40, 40, 40, 40, 60), "P3"),
        ((10, 10, 10, 10, 10, 100), "P4"),
    ],
)
def test_priority_bands(inputs: tuple[int, ...], band: str) -> None:
    result = priority_score(
        impact=inputs[0],
        evidence_confidence=inputs[1],
        reach=inputs[2],
        business_criticality=inputs[3],
        dependency_urgency=inputs[4],
        effort=inputs[5],
    )
    assert result.band == band


def test_redirect_graph_detects_loop_chain_and_cross_domain_target() -> None:
    evidence = [uid() for _ in range(4)]
    edges = (
        RedirectEdge("https://example.com/a", "https://example.com/b", 301, evidence[0]),
        RedirectEdge("https://example.com/b", "https://example.com/a", 301, evidence[1]),
        RedirectEdge("https://example.com/c", "https://example.com/d", 302, evidence[2]),
        RedirectEdge("https://example.com/d", "https://other.test/e", 302, evidence[3]),
    )
    issues = validate_redirect_graph(edges, ("example.com",), max_hops=1)
    codes = {issue.code for issue in issues}
    assert GraphIssueCode.REDIRECT_LOOP in codes
    assert GraphIssueCode.REDIRECT_CHAIN in codes
    assert GraphIssueCode.REDIRECT_TARGET_UNSAFE in codes


def test_canonical_graph_detects_cycles_and_unsafe_target() -> None:
    pages = (
        page("https://example.com/a", canonical="https://example.com/b"),
        page("https://example.com/b", canonical="https://example.com/a"),
        page("https://example.com/c", canonical="https://attacker.test/"),
    )
    codes = {issue.code for issue in validate_canonical_graph(pages, ("example.com",))}
    assert codes == {
        GraphIssueCode.CANONICAL_LOOP,
        GraphIssueCode.CANONICAL_TARGET_UNSAFE,
    }


def test_internal_link_graph_reports_observed_broken_target_once() -> None:
    source = page(
        "https://example.com/",
        links=("https://example.com/missing", "https://example.com/missing"),
    )
    target = page("https://example.com/missing", status=404)
    issues = validate_internal_links((source, target), ("example.com",))
    assert len(issues) == 1
    assert issues[0].code is GraphIssueCode.BROKEN_INTERNAL_LINK
