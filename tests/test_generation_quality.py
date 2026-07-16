from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from audit_engine.models import Severity, VerifiedFact
from generation.quality import run_generation_qa, similarity_score
from generation.schemas import FactPack


def uid() -> str:
    return str(uuid4())


def make_pack() -> FactPack:
    evidence = uid()
    return FactPack(
        uid(),
        ("example.com",),
        (VerifiedFact("homepage_title", "Example", (evidence,), datetime.now(UTC)),),
        frozenset({evidence}),
        {
            "https://example.com/": 200,
            "https://example.com/missing": 404,
            "https://example.com/unmeasured": None,
        },
        {"gsc": "credential_not_configured"},
    )


def valid_document(pack: FactPack) -> dict:
    fact = pack.facts[0]
    return {
        "body": "Review https://example.com/ using the approved evidence.",
        "claims": [
            {
                "text": "The observed homepage title is Example.",
                "fact_keys": [fact.key],
                "evidence_ids": list(fact.evidence_ids),
            }
        ],
    }


def test_clean_document_has_no_high_or_critical_quality_issues() -> None:
    pack = make_pack()
    issues = run_generation_qa(valid_document(pack), pack)
    assert not [issue for issue in issues if issue.severity in {Severity.HIGH, Severity.CRITICAL}]


def test_claim_ledger_rejects_unknown_facts_and_evidence() -> None:
    pack = make_pack()
    document = valid_document(pack)
    document["claims"][0]["fact_keys"] = ["invented_metric"]
    document["claims"][0]["evidence_ids"] = [uid()]
    codes = {issue.code for issue in run_generation_qa(document, pack)}
    assert {"claim_unknown_fact", "claim_bad_evidence"}.issubset(codes)


def test_wrong_domain_broken_unknown_link_and_placeholder_are_caught() -> None:
    pack = make_pack()
    document = valid_document(pack)
    document["body"] = (
        "Use https://attacker.test/, https://example.com/missing, "
        "https://example.com/unverified and {{PRODUCT_NAME}}."
    )
    codes = {issue.code for issue in run_generation_qa(document, pack)}
    assert {"wrong_domain", "broken_link", "unknown_link", "placeholder"}.issubset(codes)


def test_unavailable_known_link_is_high_severity() -> None:
    pack = make_pack()
    document = valid_document(pack)
    document["body"] = "Use https://example.com/unmeasured."
    issues = run_generation_qa(document, pack)
    assert any(issue.code == "broken_link" and issue.severity is Severity.HIGH for issue in issues)


def test_rating_schema_requires_verified_rating_facts() -> None:
    pack = make_pack()
    document = valid_document(pack)
    document["schema"] = {"aggregateRating": {"ratingValue": 4.8, "reviewCount": 25}}
    codes = {issue.code for issue in run_generation_qa(document, pack)}
    assert "unsupported_rating" in codes


def test_similarity_uses_word_ngrams_and_flags_near_duplicate() -> None:
    repeated = " ".join(f"evidence word {number}" for number in range(100))
    assert similarity_score(repeated, repeated) == 1
    pack = make_pack()
    document = valid_document(pack)
    document["body"] = repeated
    issues = run_generation_qa(document, pack, comparisons={"asset-1": document.copy()})
    assert any(issue.code == "near_duplicate" for issue in issues)
