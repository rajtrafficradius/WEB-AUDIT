"""Envelope for encrypted integration credentials; refuses plaintext fallback."""

from __future__ import annotations

import base64
import json

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _keys() -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for item in settings.CREDENTIAL_ENCRYPTION_KEYS.split(","):
        if not item.strip():
            continue
        try:
            key_id, encoded = item.split(":", 1)
            raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
        except (ValueError, UnicodeError) as exc:
            raise ImproperlyConfigured("CREDENTIAL_ENCRYPTION_KEYS is malformed") from exc
        if len(raw) != 32:
            raise ImproperlyConfigured("Credential encryption keys must decode to 32 bytes")
        result[key_id] = base64.urlsafe_b64encode(raw)
    return result


def encrypt_credentials(credentials: dict) -> tuple[str, str]:
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover
        raise ImproperlyConfigured("cryptography is required for connection credentials") from exc
    key_id = settings.CREDENTIAL_ENCRYPTION_ACTIVE_KEY
    keys = _keys()
    if not key_id or key_id not in keys:
        raise ImproperlyConfigured("Configure an active credential encryption key")
    token = Fernet(keys[key_id]).encrypt(
        json.dumps(credentials, separators=(",", ":")).encode("utf-8")
    )
    return token.decode("ascii"), key_id


def decrypt_credentials(token: str, key_id: str) -> dict:
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError as exc:  # pragma: no cover
        raise ImproperlyConfigured("cryptography is required for connection credentials") from exc
    key = _keys().get(key_id)
    if key is None:
        raise ImproperlyConfigured("The credential encryption key is unavailable")
    try:
        value = json.loads(Fernet(key).decrypt(token.encode("ascii")).decode("utf-8"))
    except (InvalidToken, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Credential payload could not be authenticated") from exc
    if not isinstance(value, dict):
        raise ValueError("Credential payload must be an object")
    return value
