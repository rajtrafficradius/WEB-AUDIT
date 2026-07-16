"""URL canonicalisation and SSRF-safe target resolution."""

from __future__ import annotations

import ipaddress
import posixpath
import re
import socket
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import (
    parse_qsl,
    quote,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)


class URLValidationError(ValueError):
    """The URL is malformed or outside the explicitly approved boundary."""


class SSRFBlockedError(URLValidationError):
    """The URL resolved to an unsafe network destination."""


TRACKING_PARAMETERS = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
        "vero_conv",
        "vero_id",
    }
)
PERCENT_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _is_tracking_parameter(name: str) -> bool:
    lowered = name.casefold()
    return lowered.startswith("utm_") or lowered in TRACKING_PARAMETERS


def canonical_host(host: str) -> str:
    candidate = host.strip().rstrip(".").casefold()
    if not candidate or len(candidate) > 253:
        raise URLValidationError("URL host is missing or too long")
    try:
        encoded = candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise URLValidationError("URL host contains invalid international characters") from exc
    labels = encoded.split(".")
    if any(not label or len(label) > 63 for label in labels):
        raise URLValidationError("URL host contains an invalid label")
    return encoded


def _normalize_escaped_path(path: str) -> str:
    """Decode unreserved escapes only, preserving encoded path separators."""

    if re.search(r"%(?![0-9A-Fa-f]{2})", path):
        raise URLValidationError("URL path contains an invalid percent escape")

    def replace(match: re.Match[str]) -> str:
        byte = int(match.group(1), 16)
        if byte in {0, 92} or byte < 32 or byte == 127:
            raise URLValidationError("URL path contains a prohibited encoded character")
        character = chr(byte)
        return character if character in UNRESERVED else f"%{byte:02X}"

    return PERCENT_ESCAPE.sub(replace, path)


def normalize_url(
    raw: str,
    *,
    base: str | None = None,
    strip_tracking: bool = True,
    sort_query: bool = True,
    max_length: int = 8_192,
) -> str:
    """Return a stable HTTP(S) URL while preserving semantically relevant query data."""

    if not isinstance(raw, str):
        raise URLValidationError("URL must be text")
    candidate = raw.strip()
    if not candidate or any(ord(char) < 32 or ord(char) == 127 for char in candidate):
        raise URLValidationError("URL is empty or contains control characters")
    if len(candidate) > max_length:
        raise URLValidationError("URL exceeds the configured length limit")
    if base:
        candidate = urljoin(base, candidate)
    parsed = urlsplit(candidate)
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise URLValidationError("Only HTTP and HTTPS URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise URLValidationError("Credential-bearing URLs are not allowed")
    if not parsed.hostname:
        raise URLValidationError("URL must contain a host")
    host = canonical_host(parsed.hostname)
    try:
        port = parsed.port
    except ValueError as exc:
        raise URLValidationError("URL port is invalid") from exc
    if port is not None and not 1 <= port <= 65_535:
        raise URLValidationError("URL port is outside the valid range")
    default = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    port_part = "" if port is None or default else f":{port}"
    netloc = f"[{host}]{port_part}" if ":" in host else f"{host}{port_part}"

    # Decode only RFC-unreserved escapes. This exposes encoded dot-segments for
    # normalisation without conflating an encoded slash with a real separator.
    decoded_path = _normalize_escaped_path(parsed.path or "/")
    if "\\" in decoded_path or "\x00" in decoded_path:
        raise URLValidationError("URL path contains prohibited characters")
    trailing_slash = decoded_path.endswith("/")
    clean_path = posixpath.normpath(decoded_path)
    if not clean_path.startswith("/"):
        clean_path = "/" + clean_path
    if trailing_slash and clean_path != "/":
        clean_path += "/"
    path = quote(clean_path, safe="/%:@!$&'()*+,;=-._~")

    query_items = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    if strip_tracking:
        query_items = [
            (key, value) for key, value in query_items if not _is_tracking_parameter(key)
        ]
    if sort_query:
        query_items.sort(key=lambda item: (item[0].casefold(), item[1]))
    query = urlencode(query_items, doseq=True)
    normalized = urlunsplit((scheme, netloc, path, query, ""))
    if len(normalized) > max_length:
        raise URLValidationError("Normalized URL exceeds the configured length limit")
    return normalized


def host_is_allowed(host: str, allowed_domains: Iterable[str]) -> bool:
    normalized = canonical_host(host)
    for approved in allowed_domains:
        boundary = canonical_host(approved)
        if normalized == boundary or normalized.endswith("." + boundary):
            return True
    return False


def require_allowed_url(url: str, allowed_domains: Iterable[str]) -> str:
    normalized = normalize_url(url)
    host = urlsplit(normalized).hostname
    assert host is not None
    if not host_is_allowed(host, allowed_domains):
        raise URLValidationError("URL host is outside the approved project domains")
    return normalized


def is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    # is_global rejects loopback, private, link-local, reserved, multicast,
    # unspecified, documentation, and shared carrier-grade NAT ranges.
    return address.is_global


Resolver = Callable[[str, int, int, int], Sequence[tuple]]


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    normalized_url: str
    hostname: str
    port: int
    approved_ips: tuple[str, ...]


class SSRFGuard:
    """Validate hosts and pin a public DNS answer for the subsequent connection."""

    def __init__(
        self,
        allowed_domains: Iterable[str],
        *,
        resolver: Resolver | None = None,
        max_dns_answers: int = 16,
    ) -> None:
        self.allowed_domains = tuple(canonical_host(value) for value in allowed_domains)
        if not self.allowed_domains:
            raise ValueError("At least one approved domain is required")
        self._resolver = resolver or socket.getaddrinfo
        self.max_dns_answers = max_dns_answers

    def validate(self, url: str) -> ResolvedTarget:
        normalized = require_allowed_url(url, self.allowed_domains)
        parsed = urlsplit(normalized)
        assert parsed.hostname is not None
        hostname = canonical_host(parsed.hostname)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            answers = self._resolver(hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except (socket.gaierror, OSError) as exc:
            raise SSRFBlockedError("Approved host could not be resolved safely") from exc
        ips: set[str] = set()
        for answer in answers[: self.max_dns_answers]:
            try:
                candidate = answer[4][0]
            except (IndexError, TypeError):
                continue
            candidate = candidate.split("%", 1)[0]
            if not is_public_ip(candidate):
                raise SSRFBlockedError("Target resolves to a non-public network address")
            ips.add(str(ipaddress.ip_address(candidate)))
        if not ips:
            raise SSRFBlockedError("Target has no safe public DNS answers")
        return ResolvedTarget(normalized, hostname, port, tuple(sorted(ips)))

    def validate_redirect(self, previous_url: str, location: str) -> ResolvedTarget:
        if not location or any(ord(char) < 32 for char in location):
            raise SSRFBlockedError("Redirect target is missing or malformed")
        return self.validate(urljoin(previous_url, location))
