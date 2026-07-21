"""Live SEMrush health and unit-balance checks.

The balance endpoint (``countapiunits.html``) is free — it reports the
account's remaining API units without consuming any — so it doubles as a
truthful "is the key working" probe. Results are cached briefly so page loads
and status polls never hammer the provider.
"""

from __future__ import annotations

import hashlib
import logging

from django.core.cache import cache

from audit_engine.crawler import CrawlError, PinnedHTTPTransport
from audit_engine.urls import SSRFGuard, URLValidationError
from integrations.market_data import resolve_org_api_key, studio_units_spent

logger = logging.getLogger(__name__)

BALANCE_HOST = "www.semrush.com"
BALANCE_ENDPOINT = "https://www.semrush.com/users/countapiunits.html"
STATUS_CACHE_SECONDS = 180
STATUS_CACHE_PREFIX = "semrush:status:v1:"
MAX_BALANCE_BYTES = 4096


def _key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def fetch_unit_balance(api_key: str, *, transport=None, guard=None) -> int | None:
    """Return the account's remaining API units, or None if it cannot be read.

    Free call: the units endpoint never charges units, so it is safe to poll.
    """

    api_key = (api_key or "").strip()
    if not api_key:
        return None
    guard = guard or SSRFGuard((BALANCE_HOST,))
    transport = transport or PinnedHTTPTransport()
    url = f"{BALANCE_ENDPOINT}?key={api_key}"
    try:
        response = transport.fetch(
            guard.validate(url),
            method="GET",
            headers={"Accept": "text/plain"},
            timeout=12.0,
            max_bytes=MAX_BALANCE_BYTES,
        )
    except (CrawlError, URLValidationError, TimeoutError, OSError) as exc:
        logger.warning("SEMrush balance check failed to connect: %s", type(exc).__name__)
        return None
    if response.status_code >= 300:
        return None
    body = response.body.decode("utf-8", errors="replace").strip()
    if not body or body.upper().startswith("ERROR"):
        return None
    digits = body.replace(",", "").split()[0] if body.split() else ""
    try:
        return max(0, int(digits))
    except ValueError:
        return None


def check_status(api_key: str | None = None, *, transport=None) -> dict:
    """Resolve the org SEMrush key and report a live, cached status payload.

    status: 'working' (key valid, balance read), 'no_key' (nothing configured),
    or 'unavailable' (a key exists but the provider rejected or was unreachable).
    """

    key = (api_key if api_key is not None else resolve_org_api_key()).strip()
    used = studio_units_spent()
    if not key:
        return {
            "status": "no_key",
            "label": "No SEMrush key",
            "message": "Add a SEMrush key on the Credentials page to enable market data.",
            "units_remaining": None,
            "units_used": used,
        }

    cache_id = f"{STATUS_CACHE_PREFIX}{_key_fingerprint(key)}"
    balance = cache.get(cache_id, "__miss__")
    if balance == "__miss__":
        balance = fetch_unit_balance(key, transport=transport)
        cache.set(cache_id, balance, STATUS_CACHE_SECONDS)

    if balance is None:
        return {
            "status": "unavailable",
            "label": "SEMrush unavailable",
            "message": "A key is configured but the provider could not be reached or rejected it.",
            "units_remaining": None,
            "units_used": used,
        }
    return {
        "status": "working",
        "label": "SEMrush is working",
        "message": f"{balance:,} API units remaining.",
        "units_remaining": balance,
        "units_used": used,
    }


def invalidate_status_cache(api_key: str) -> None:
    """Drop the cached balance so the next check re-probes (e.g. after a save)."""

    key = (api_key or "").strip()
    if key:
        cache.delete(f"{STATUS_CACHE_PREFIX}{_key_fingerprint(key)}")
