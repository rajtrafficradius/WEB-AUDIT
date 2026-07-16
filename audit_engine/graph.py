"""Redirect, canonical, and internal-link graph validation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from .models import PageSnapshot, Severity
from .urls import URLValidationError, normalize_url, require_allowed_url


class GraphIssueCode(StrEnum):
    REDIRECT_LOOP = "redirect_loop"
    REDIRECT_CHAIN = "redirect_chain"
    REDIRECT_TARGET_UNSAFE = "redirect_target_unsafe"
    CANONICAL_LOOP = "canonical_loop"
    CANONICAL_TARGET_UNSAFE = "canonical_target_unsafe"
    BROKEN_INTERNAL_LINK = "broken_internal_link"
    UNKNOWN_INTERNAL_TARGET = "unknown_internal_target"


@dataclass(frozen=True, slots=True)
class RedirectEdge:
    source: str
    target: str
    status_code: int
    evidence_id: str

    def __post_init__(self) -> None:
        if self.status_code not in {301, 302, 303, 307, 308}:
            raise ValueError("Redirect edges require a redirect HTTP status")


@dataclass(frozen=True, slots=True)
class GraphIssue:
    code: GraphIssueCode
    severity: Severity
    message: str
    urls: tuple[str, ...]
    evidence_ids: tuple[str, ...]


def _walk_single_edge(
    start: str,
    edges: Mapping[str, tuple[str, str]],
    *,
    step_limit: int,
) -> tuple[list[str], list[str], bool]:
    path = [start]
    evidence: list[str] = []
    seen = {start: 0}
    current = start
    while current in edges:
        target, evidence_id = edges[current]
        evidence.append(evidence_id)
        path.append(target)
        if target in seen:
            return path, evidence, True
        seen[target] = len(path) - 1
        current = target
        if len(path) > step_limit + 1:
            break
    return path, evidence, False


def validate_redirect_graph(
    redirects: Iterable[RedirectEdge],
    allowed_domains: Iterable[str],
    *,
    max_hops: int = 1,
) -> tuple[GraphIssue, ...]:
    edges: dict[str, tuple[str, str]] = {}
    issues: list[GraphIssue] = []
    for edge in redirects:
        source = normalize_url(edge.source)
        try:
            target = require_allowed_url(edge.target, allowed_domains)
        except URLValidationError:
            target = normalize_url(edge.target)
            issues.append(
                GraphIssue(
                    GraphIssueCode.REDIRECT_TARGET_UNSAFE,
                    Severity.HIGH,
                    "Redirect target is outside the approved project domains",
                    (source, target),
                    (edge.evidence_id,),
                )
            )
        if source in edges and edges[source][0] != target:
            raise ValueError(f"Redirect source has conflicting targets: {source}")
        edges[source] = (target, edge.evidence_id)
    emitted_loops: set[frozenset[str]] = set()
    emitted_chains: set[tuple[str, ...]] = set()
    for source in sorted(edges):
        path, evidence, looped = _walk_single_edge(source, edges, step_limit=len(edges) + 1)
        if looped:
            loop_key = frozenset(path[path.index(path[-1]) :])
            if loop_key not in emitted_loops:
                emitted_loops.add(loop_key)
                issues.append(
                    GraphIssue(
                        GraphIssueCode.REDIRECT_LOOP,
                        Severity.CRITICAL,
                        "Redirect loop detected",
                        tuple(path),
                        tuple(dict.fromkeys(evidence)),
                    )
                )
        elif len(path) - 1 > max_hops:
            key = tuple(path)
            if key not in emitted_chains:
                emitted_chains.add(key)
                issues.append(
                    GraphIssue(
                        GraphIssueCode.REDIRECT_CHAIN,
                        Severity.HIGH,
                        f"Redirect chain exceeds the {max_hops}-hop limit",
                        key,
                        tuple(dict.fromkeys(evidence)),
                    )
                )
    return tuple(issues)


def validate_canonical_graph(
    pages: Iterable[PageSnapshot],
    allowed_domains: Iterable[str],
) -> tuple[GraphIssue, ...]:
    edges: dict[str, tuple[str, str]] = {}
    issues: list[GraphIssue] = []
    for page in pages:
        if not page.canonical_url:
            continue
        source = normalize_url(page.normalized_url)
        try:
            target = require_allowed_url(page.canonical_url, allowed_domains)
        except URLValidationError:
            target = normalize_url(page.canonical_url)
            issues.append(
                GraphIssue(
                    GraphIssueCode.CANONICAL_TARGET_UNSAFE,
                    Severity.HIGH,
                    "Canonical target is outside the approved project domains",
                    (source, target),
                    (page.evidence_id,),
                )
            )
        if target != source:
            edges[source] = (target, page.evidence_id)
    emitted: set[frozenset[str]] = set()
    for source in sorted(edges):
        path, evidence, looped = _walk_single_edge(source, edges, step_limit=len(edges) + 1)
        if looped:
            loop_key = frozenset(path[path.index(path[-1]) :])
            if loop_key not in emitted:
                emitted.add(loop_key)
                issues.append(
                    GraphIssue(
                        GraphIssueCode.CANONICAL_LOOP,
                        Severity.CRITICAL,
                        "Canonical cycle detected",
                        tuple(path),
                        tuple(dict.fromkeys(evidence)),
                    )
                )
    return tuple(issues)


def validate_internal_links(
    pages: Iterable[PageSnapshot],
    allowed_domains: Iterable[str],
    *,
    report_unknown: bool = False,
) -> tuple[GraphIssue, ...]:
    page_list = tuple(pages)
    by_url = {normalize_url(page.normalized_url): page for page in page_list}
    issues: list[GraphIssue] = []
    seen: set[tuple[str, str, GraphIssueCode]] = set()
    for source in page_list:
        source_url = normalize_url(source.normalized_url)
        for raw_target in source.links:
            try:
                target = require_allowed_url(
                    normalize_url(raw_target, base=source_url), allowed_domains
                )
            except URLValidationError:
                continue
            page = by_url.get(target)
            evidence: tuple[str, ...]
            if page and page.status_code is not None and page.status_code >= 400:
                code = GraphIssueCode.BROKEN_INTERNAL_LINK
                severity = Severity.HIGH
                message = f"Internal link target returned HTTP {page.status_code}"
                evidence = (source.evidence_id, page.evidence_id)
            elif page is None and report_unknown:
                code = GraphIssueCode.UNKNOWN_INTERNAL_TARGET
                severity = Severity.INFO
                message = "Internal link target was not present in the crawl snapshot"
                evidence = (source.evidence_id,)
            else:
                continue
            key = (source_url, target, code)
            if key in seen:
                continue
            seen.add(key)
            issues.append(GraphIssue(code, severity, message, (source_url, target), evidence))
    return tuple(issues)
