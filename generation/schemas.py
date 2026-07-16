"""Strict generation contracts and approved fact packs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from audit_engine.models import ContractError, VerifiedFact
from audit_engine.urls import canonical_host, require_allowed_url

STRATEGY_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "executive_summary", "recommendations", "claims", "unavailable_items"],
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 200},
        "executive_summary": {"type": "string", "minLength": 1, "maxLength": 5000},
        "recommendations": {
            "type": "array",
            "maxItems": 100,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "rationale", "implementation", "evidence_ids", "risk"],
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 300},
                    "rationale": {"type": "string", "minLength": 1, "maxLength": 4000},
                    "implementation": {"type": "string", "minLength": 1, "maxLength": 10000},
                    "evidence_ids": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "format": "uuid"},
                    },
                    "risk": {"type": "string", "enum": ["low", "moderate", "high", "dangerous"]},
                },
            },
        },
        "claims": {
            "type": "array",
            "maxItems": 500,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "fact_keys", "evidence_ids"],
                "properties": {
                    "text": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "fact_keys": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1, "maxLength": 255},
                    },
                    "evidence_ids": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "format": "uuid"},
                    },
                },
            },
        },
        "unavailable_items": {
            "type": "array",
            "maxItems": 100,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "reason"],
                "properties": {
                    "source": {"type": "string", "minLength": 1, "maxLength": 100},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 500},
                },
            },
        },
    },
}


@dataclass(frozen=True, slots=True)
class FactPack:
    project_id: str
    approved_domains: tuple[str, ...]
    facts: tuple[VerifiedFact, ...]
    available_evidence_ids: frozenset[str]
    known_url_statuses: Mapping[str, int | None] = field(default_factory=dict)
    unavailable_sources: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.approved_domains:
            raise ContractError("Fact pack requires at least one approved domain")
        normalized_domains = tuple(canonical_host(value) for value in self.approved_domains)
        object.__setattr__(self, "approved_domains", normalized_domains)
        fact_keys = [fact.key for fact in self.facts]
        if len(set(fact_keys)) != len(fact_keys):
            raise ContractError("Fact keys must be unique")
        for fact in self.facts:
            missing = set(fact.evidence_ids) - set(self.available_evidence_ids)
            if missing:
                raise ContractError(f"Fact {fact.key} references unavailable evidence")
        normalized_urls: dict[str, int | None] = {}
        for url, status in self.known_url_statuses.items():
            normalized = require_allowed_url(url, normalized_domains)
            if status is not None and not 100 <= status <= 599:
                raise ContractError("Known URL status must be a valid HTTP status")
            normalized_urls[normalized] = status
        object.__setattr__(self, "known_url_statuses", normalized_urls)
        if any(
            not key.strip() or not reason.strip()
            for key, reason in self.unavailable_sources.items()
        ):
            raise ContractError("Unavailable sources require a source name and reason")

    @property
    def facts_by_key(self) -> Mapping[str, VerifiedFact]:
        return {fact.key: fact for fact in self.facts}

    def as_untrusted_payload(self) -> Mapping[str, Any]:
        return {
            "project_id": self.project_id,
            "approved_domains": list(self.approved_domains),
            "facts": [
                {
                    "key": fact.key,
                    "value": fact.value,
                    "evidence_ids": list(fact.evidence_ids),
                    "as_of": fact.as_of.isoformat(),
                }
                for fact in self.facts
            ],
            "known_url_statuses": dict(self.known_url_statuses),
            "unavailable_sources": dict(self.unavailable_sources),
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.as_untrusted_payload(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def strategy_schema() -> dict[str, Any]:
    """Return a mutable copy suitable for an SDK request."""

    return json.loads(json.dumps(STRATEGY_SCHEMA))
