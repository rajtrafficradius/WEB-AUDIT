"""Strict schemas and task text for the two package-level grounded generations.

Two generations are defined here and nothing else:

``ONPAGE_PROPOSAL_SCHEMA``
    One structured call that proposes title, meta description and H1 rewrites for
    the highest-value crawled pages.  Every proposal must be derived from the
    supplied per-page fact pack; an empty string means "no change required".

``CONTENT_OUTLINE_SCHEMA``
    One structured call that returns the outline set for every approved content
    asset in a single response, so brief expansion stays deterministic.

Both schemas require a claims ledger so ``generation.quality.validate_claims``
can reject anything the fact pack does not support.
"""

from __future__ import annotations

import json
from typing import Any

PROMPT_VERSION_ONPAGE = "package-onpage-proposals-1.0.0"
PROMPT_VERSION_OUTLINES = "package-content-outlines-1.0.0"

TITLE_MAX_CHARS = 60
META_MIN_CHARS = 70
META_MAX_CHARS = 158
H1_MAX_CHARS = 70

_CLAIMS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "minItems": 1,
    "maxItems": 200,
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["text", "fact_keys", "evidence_ids"],
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 2000},
            "fact_keys": {
                "type": "array",
                "minItems": 1,
                "maxItems": 40,
                "items": {"type": "string", "minLength": 1, "maxLength": 255},
            },
            "evidence_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": 40,
                "items": {"type": "string", "format": "uuid"},
            },
        },
    },
}

ONPAGE_PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["proposals", "claims"],
    "properties": {
        "proposals": {
            "type": "array",
            "minItems": 1,
            "maxItems": 25,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "page_id",
                    "proposed_title",
                    "proposed_meta_description",
                    "proposed_h1",
                    "rationale",
                ],
                "properties": {
                    "page_id": {"type": "string", "minLength": 1, "maxLength": 20},
                    "proposed_title": {"type": "string", "maxLength": TITLE_MAX_CHARS},
                    "proposed_meta_description": {"type": "string", "maxLength": META_MAX_CHARS},
                    "proposed_h1": {"type": "string", "maxLength": H1_MAX_CHARS},
                    "rationale": {"type": "string", "minLength": 1, "maxLength": 400},
                },
            },
        },
        "claims": _CLAIMS_SCHEMA,
    },
}

CONTENT_OUTLINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["outlines", "claims"],
    "properties": {
        "outlines": {
            "type": "array",
            "minItems": 1,
            "maxItems": 24,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["asset_id", "intent_summary", "sections"],
                "properties": {
                    "asset_id": {"type": "string", "minLength": 1, "maxLength": 20},
                    "intent_summary": {"type": "string", "minLength": 1, "maxLength": 600},
                    "sections": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 8,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["heading", "guidance"],
                            "properties": {
                                "heading": {"type": "string", "minLength": 1, "maxLength": 120},
                                "guidance": {"type": "string", "minLength": 1, "maxLength": 800},
                            },
                        },
                    },
                },
            },
        },
        "claims": _CLAIMS_SCHEMA,
    },
}

ONPAGE_PROPOSAL_TASK = (
    "Propose one replacement title tag, meta description and H1 for each supplied page. "
    f"Titles must be at most {TITLE_MAX_CHARS} characters, meta descriptions between "
    f"{META_MIN_CHARS} and {META_MAX_CHARS} characters, and H1s at most {H1_MAX_CHARS} "
    "characters. Use only the facts supplied for that page: its URL, page type, observed "
    "title, meta description, H1, word count and matched keyword phrase. Do not invent "
    "offers, prices, locations, delivery promises, awards, ratings or statistics. Do not "
    "write any URL that is not in the supplied status map. If a current value is already "
    "accurate and correctly sized, return an empty string for that field instead of "
    "restating it. Support every factual statement in the claims ledger."
)

CONTENT_OUTLINE_TASK = (
    "Return one outline per supplied content asset. Each outline needs a short search-intent "
    "summary and three to eight section headings with practical guidance for the writer. "
    "Ground every heading in the supplied page evidence and matched keyword phrases; do not "
    "invent metrics, rankings, competitors, prices or claims about the business. Outlines "
    "must differ from one another: no heading may be reused across two assets. Support every "
    "factual statement in the claims ledger."
)


def onpage_proposal_schema() -> dict[str, Any]:
    """Return a mutable copy suitable for an SDK request."""
    return json.loads(json.dumps(ONPAGE_PROPOSAL_SCHEMA))


def content_outline_schema() -> dict[str, Any]:
    """Return a mutable copy suitable for an SDK request."""
    return json.loads(json.dumps(CONTENT_OUTLINE_SCHEMA))
