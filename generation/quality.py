# ruff: noqa: E501
"""Deterministic post-generation claim, domain, link, and similarity QA."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from audit_engine.models import Severity
from audit_engine.urls import URLValidationError, require_allowed_url

from .schemas import FactPack

URL_PATTERN = re.compile(r"https?://[^\s<>'\"\]\[)]+", re.IGNORECASE)
PLACEHOLDER_PATTERN = re.compile(
    r"(?:\{\{[^{}]+\}\}|\[[A-Z][A-Z0-9_ -]{2,}\]|\b(?:TODO|TBD|PLACEHOLDER|PRODUCT_NAME|INSERT_[A-Z_]+)\b)",
    re.IGNORECASE,
)
WORD_PATTERN = re.compile(r"[a-z0-9]+(?:['’-][a-z0-9]+)?", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class QualityIssue:
    code: str
    severity: Severity
    message: str
    locations: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


def _walk_strings(value: Any, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, Mapping):
        for key, child in value.items():
            yield from _walk_strings(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_strings(child, f"{path}[{index}]")


def validate_claims(document: Mapping[str, Any], fact_pack: FactPack) -> tuple[QualityIssue, ...]:
    issues: list[QualityIssue] = []
    claims = document.get("claims")
    if not isinstance(claims, list):
        return (
            QualityIssue(
                "claim_ledger_missing", Severity.HIGH, "Document has no valid claim ledger."
            ),
        )
    facts = fact_pack.facts_by_key
    for index, claim in enumerate(claims):
        path = f"$.claims[{index}]"
        if not isinstance(claim, Mapping):
            issues.append(
                QualityIssue(
                    "claim_invalid", Severity.HIGH, "Claim ledger entry is malformed.", (path,)
                )
            )
            continue
        fact_keys = claim.get("fact_keys")
        evidence_ids = claim.get("evidence_ids")
        if (
            not isinstance(fact_keys, list)
            or not fact_keys
            or not isinstance(evidence_ids, list)
            or not evidence_ids
        ):
            issues.append(
                QualityIssue(
                    "claim_unsubstantiated",
                    Severity.HIGH,
                    "Claim lacks fact and evidence references.",
                    (path,),
                )
            )
            continue
        unknown_facts = sorted(set(fact_keys) - set(facts))
        unknown_evidence = sorted(set(evidence_ids) - set(fact_pack.available_evidence_ids))
        expected_evidence = {
            evidence_id
            for key in fact_keys
            if key in facts
            for evidence_id in facts[key].evidence_ids
        }
        if unknown_facts:
            issues.append(
                QualityIssue(
                    "claim_unknown_fact",
                    Severity.HIGH,
                    "Claim references a fact outside the approved fact pack.",
                    (path,),
                )
            )
        if unknown_evidence or not expected_evidence.issubset(set(evidence_ids)):
            issues.append(
                QualityIssue(
                    "claim_bad_evidence",
                    Severity.HIGH,
                    "Claim evidence does not reconcile with approved facts.",
                    (path,),
                )
            )
    return tuple(issues)


def validate_domains_and_links(
    document: Mapping[str, Any], fact_pack: FactPack
) -> tuple[QualityIssue, ...]:
    issues: list[QualityIssue] = []
    emitted: set[tuple[str, str]] = set()
    for path, text in _walk_strings(document):
        for raw in URL_PATTERN.findall(text):
            raw = raw.rstrip(".,;:!?")
            try:
                normalized = require_allowed_url(raw, fact_pack.approved_domains)
            except URLValidationError:
                key = ("wrong_domain", raw)
                if key not in emitted:
                    emitted.add(key)
                    issues.append(
                        QualityIssue(
                            "wrong_domain",
                            Severity.HIGH,
                            "Generated URL is malformed or outside approved domains.",
                            (path,),
                        )
                    )
                continue
            status = fact_pack.known_url_statuses.get(normalized)
            if status is None and normalized not in fact_pack.known_url_statuses:
                key = ("unknown_link", normalized)
                if key not in emitted:
                    emitted.add(key)
                    issues.append(
                        QualityIssue(
                            "unknown_link",
                            Severity.MEDIUM,
                            "Generated internal URL was not verified in the evidence snapshot.",
                            (path,),
                        )
                    )
            elif status is None or status >= 400:
                key = ("broken_link", normalized)
                if key not in emitted:
                    emitted.add(key)
                    issues.append(
                        QualityIssue(
                            "broken_link",
                            Severity.HIGH,
                            "Generated internal URL is unavailable or returned an error in the evidence snapshot.",
                            (path,),
                        )
                    )
    return tuple(issues)


def validate_placeholders(document: Mapping[str, Any]) -> tuple[QualityIssue, ...]:
    return tuple(
        QualityIssue(
            "placeholder",
            Severity.HIGH,
            "Generated content contains an unresolved placeholder.",
            (path,),
        )
        for path, value in _walk_strings(document)
        if PLACEHOLDER_PATTERN.search(value)
    )


def _ngrams(text: str, size: int = 5) -> set[tuple[str, ...]]:
    words = [match.group(0).casefold() for match in WORD_PATTERN.finditer(text)]
    return {tuple(words[index : index + size]) for index in range(max(0, len(words) - size + 1))}


def similarity_score(left: str, right: str) -> float:
    left_grams = _ngrams(left)
    right_grams = _ngrams(right)
    if not left_grams and not right_grams:
        return 1.0 if left.strip() == right.strip() else 0.0
    union = left_grams | right_grams
    return len(left_grams & right_grams) / len(union) if union else 0.0


def validate_similarity(
    document: Mapping[str, Any],
    comparisons: Mapping[str, Mapping[str, Any]],
    *,
    threshold: float = 0.82,
) -> tuple[QualityIssue, ...]:
    if not 0 < threshold <= 1:
        raise ValueError("Similarity threshold must be between zero and one")
    candidate = json.dumps(document, ensure_ascii=False, sort_keys=True)
    issues: list[QualityIssue] = []
    for identifier, comparison in comparisons.items():
        score = similarity_score(
            candidate, json.dumps(comparison, ensure_ascii=False, sort_keys=True)
        )
        if score >= threshold:
            issues.append(
                QualityIssue(
                    "near_duplicate",
                    Severity.HIGH,
                    f"Generated content is too similar to approved asset {identifier} ({score:.1%}).",
                    (identifier,),
                )
            )
    return tuple(issues)


def validate_sensitive_schema_claims(
    document: Mapping[str, Any], fact_pack: FactPack
) -> tuple[QualityIssue, ...]:
    """Require explicit facts for ratings/review counts wherever they appear."""

    approved = set(fact_pack.facts_by_key)
    required_key_fragments = {
        "aggregaterating": "aggregate_rating",
        "ratingvalue": "rating_value",
        "reviewcount": "review_count",
    }
    issues: list[QualityIssue] = []

    def visit(value: Any, path: str = "$") -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                folded = str(key).replace("_", "").casefold()
                for fragment, expected in required_key_fragments.items():
                    if fragment in folded and not any(
                        expected in fact.casefold() for fact in approved
                    ):
                        issues.append(
                            QualityIssue(
                                "unsupported_rating",
                                Severity.HIGH,
                                "Rating or review schema is not supported by an approved fact.",
                                (f"{path}.{key}",),
                            )
                        )
                visit(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(document)
    return tuple(issues)


def run_generation_qa(
    document: Mapping[str, Any],
    fact_pack: FactPack,
    *,
    comparisons: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[QualityIssue, ...]:
    return (
        *validate_claims(document, fact_pack),
        *validate_domains_and_links(document, fact_pack),
        *validate_placeholders(document),
        *validate_sensitive_schema_claims(document, fact_pack),
        *validate_similarity(document, comparisons or {}),
    )
