"""Live SEMrush health and unit-balance checks.

The balance endpoint (``countapiunits.html``) is free — it reports the
account's remaining API units without consuming any — so it doubles as a
truthful "is the key working" probe. Results are cached briefly so page loads
and status polls never hammer the provider.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

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


_BALANCE_KEYS = ("units", "api_units", "units_remaining", "remaining", "balance", "count")


def parse_balance_body(body: str) -> int | None:
    """Extract the remaining unit count from a plain-integer OR JSON response.

    The endpoint used to return a bare integer and now answers in JSON
    (``{"units": 50000}`` on success, ``{"errors": [...]}`` on a bad key), so
    both shapes must be understood or a valid account reads as 'unavailable'.
    """

    text = (body or "").strip()
    if not text:
        return None
    compact = text.replace(",", "")
    if compact.lstrip("-").isdigit():
        try:
            return max(0, int(compact))
        except ValueError:
            pass
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        if data.get("errors") or data.get("error"):
            return None
        for key in _BALANCE_KEYS:
            value = data.get(key)
            if isinstance(value, int | str):
                try:
                    return max(0, int(value))
                except (TypeError, ValueError):
                    continue
        return None
    match = re.search(r"\d[\d,]*", text)
    if match:
        try:
            return max(0, int(match.group().replace(",", "")))
        except ValueError:
            return None
    return None


def probe_balance(api_key: str, *, transport=None, guard=None) -> tuple[int | None, str]:
    """Return (remaining_units, reason). reason in {ok, rejected, unreachable, unreadable}.

    Free call: the units endpoint never charges units, so it is safe to poll.
    """

    api_key = (api_key or "").strip()
    if not api_key:
        return None, "no_key"
    guard = guard or SSRFGuard((BALANCE_HOST,))
    transport = transport or PinnedHTTPTransport()
    url = f"{BALANCE_ENDPOINT}?key={api_key}"
    try:
        response = transport.fetch(
            guard.validate(url),
            method="GET",
            headers={"Accept": "application/json, text/plain, */*"},
            timeout=12.0,
            max_bytes=MAX_BALANCE_BYTES,
        )
    except (CrawlError, URLValidationError, TimeoutError, OSError) as exc:
        logger.warning("SEMrush balance check failed to connect: %s", type(exc).__name__)
        return None, "unreachable"
    body = response.body.decode("utf-8", errors="replace").strip()
    if response.status_code == 200:
        balance = parse_balance_body(body)
        if balance is not None:
            return balance, "ok"
        if body.upper().startswith("ERROR") or '"errors"' in body or '"error"' in body:
            return None, "rejected"
        return None, "unreadable"
    if response.status_code in {400, 401, 403}:
        return None, "rejected"
    return None, "unreachable"


def fetch_unit_balance(api_key: str, *, transport=None, guard=None) -> int | None:
    """Return the account's remaining API units, or None if it cannot be read."""

    balance, _reason = probe_balance(api_key, transport=transport, guard=guard)
    return balance


_REASON_MESSAGE = {
    "rejected": "SEMrush rejected the key. Re-check the key on the Credentials page.",
    "unreachable": "SEMrush could not be reached — this is usually a temporary network issue.",
    "unreadable": "SEMrush responded but the unit balance could not be read.",
}


def check_status(api_key: str | None = None, *, transport=None, refresh: bool = False) -> dict:
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
    probe = None if refresh else cache.get(cache_id)
    if probe is None:
        balance, reason = probe_balance(key, transport=transport)
        probe = {"balance": balance, "reason": reason}
        cache.set(cache_id, probe, STATUS_CACHE_SECONDS)

    balance = probe.get("balance")
    if balance is None:
        return {
            "status": "unavailable",
            "label": "SEMrush unavailable",
            "message": _REASON_MESSAGE.get(
                probe.get("reason"),
                "A key is configured but the provider could not be reached or rejected it.",
            ),
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
