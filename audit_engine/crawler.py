"""Robots-aware, bounded crawler with DNS-pinned HTTP connections.

The crawler never follows a redirect or opens a socket before ``SSRFGuard`` has
approved the hostname and every resolved address.  The transport connects to
that approved address directly, closing the common DNS-rebinding gap between
validation and connection.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import socket
import ssl
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .urls import ResolvedTarget, SSRFGuard, URLValidationError, normalize_url, require_allowed_url


class CrawlError(RuntimeError):
    pass


class ResponseTooLarge(CrawlError):
    pass


class UnsafeHTTPResponse(CrawlError):
    pass


class ChallengeResponse(CrawlError):
    """The origin answered with a bot challenge or access denial, not page content.

    The response body is carried so the caller can quarantine the URL with real
    provenance (status code, digest, byte count) instead of treating the
    challenge interstitial as if it were the client's own page.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str,
        response: FetchResponse | None = None,
        retry_after: int | None = None,
        redirect_chain: tuple[str, ...] = (),
        response_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.response = response
        self.retry_after = retry_after
        self.redirect_chain = redirect_chain
        self.response_ms = response_ms


class RateLimited(ChallengeResponse):
    """The origin throttled us (429/503).  Our own throttling is never a client defect."""


@dataclass(frozen=True, slots=True)
class FetchResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    url: str


class SafeTransport(Protocol):
    def fetch(
        self,
        target: ResolvedTarget,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float,
        max_bytes: int,
    ) -> FetchResponse: ...


def _safe_header(name: str, value: str) -> None:
    if not name or any(char in name for char in "\r\n:"):
        raise ValueError("Invalid HTTP header name")
    if "\r" in value or "\n" in value:
        raise ValueError("Invalid HTTP header value")


class PinnedHTTPTransport:
    """Minimal HTTP/1.1 transport that connects only to guard-approved IPs."""

    def __init__(self, *, ssl_context: ssl.SSLContext | None = None) -> None:
        self.ssl_context = ssl_context or ssl.create_default_context()

    def fetch(
        self,
        target: ResolvedTarget,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float,
        max_bytes: int,
    ) -> FetchResponse:
        method = method.upper()
        if method not in {"GET", "HEAD", "POST"}:
            raise ValueError("Transport method is not permitted")
        if timeout <= 0 or max_bytes <= 0:
            raise ValueError("timeout and max_bytes must be positive")
        parsed = urlsplit(target.normalized_url)
        path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        default_port = 443 if parsed.scheme == "https" else 80
        host_header = (
            target.hostname if target.port == default_port else f"{target.hostname}:{target.port}"
        )
        request_headers = {
            "Host": host_header,
            "User-Agent": "TrafficRadiusEvidenceBot/1.0",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.1",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
        for name, value in (headers or {}).items():
            _safe_header(str(name), str(value))
            if name.casefold() in {"host", "connection", "content-length", "transfer-encoding"}:
                continue
            request_headers[str(name)] = str(value)
        payload = body or b""
        if payload:
            request_headers["Content-Length"] = str(len(payload))
        wire_headers = "".join(f"{name}: {value}\r\n" for name, value in request_headers.items())
        wire = f"{method} {path} HTTP/1.1\r\n{wire_headers}\r\n".encode("ascii") + payload

        last_error: OSError | ssl.SSLError | None = None
        for approved_ip in target.approved_ips:
            raw: socket.socket | None = None
            connected: socket.socket | ssl.SSLSocket | None = None
            try:
                raw = socket.create_connection((approved_ip, target.port), timeout=timeout)
                raw.settimeout(timeout)
                if parsed.scheme == "https":
                    connected = self.ssl_context.wrap_socket(raw, server_hostname=target.hostname)
                    raw = None
                else:
                    connected = raw
                    raw = None
                connected.sendall(wire)
                response = http.client.HTTPResponse(connected, method=method)
                response.begin()
                declared = response.getheader("Content-Length")
                if declared:
                    try:
                        if int(declared) > max_bytes:
                            raise ResponseTooLarge("Response exceeds configured byte limit")
                    except ValueError as exc:
                        raise UnsafeHTTPResponse("Invalid Content-Length header") from exc
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ResponseTooLarge("Response exceeds configured byte limit")
                response_headers = {name.casefold(): value for name, value in response.getheaders()}
                return FetchResponse(response.status, response_headers, data, target.normalized_url)
            except (OSError, ssl.SSLError) as exc:
                last_error = exc
            finally:
                if connected is not None:
                    connected.close()
                if raw is not None:
                    raw.close()
        raise CrawlError("All approved target addresses failed") from last_error


@dataclass(frozen=True, slots=True)
class CrawlConfig:
    allowed_domains: tuple[str, ...]
    max_pages: int = 250
    max_depth: int = 5
    max_duration_seconds: float = 900.0
    request_timeout_seconds: float = 15.0
    max_body_bytes: int = 5_000_000
    max_robots_bytes: int = 512_000
    max_redirects: int = 5
    min_host_delay_seconds: float = 0.5
    user_agent: str = "TrafficRadiusEvidenceBot/1.0"
    obey_robots: bool = True
    challenge_backoff_multiplier: float = 4.0
    max_host_delay_seconds: float = 8.0
    max_retry_after_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.allowed_domains:
            raise ValueError("allowed_domains cannot be empty")
        if self.max_pages < 1 or self.max_depth < 0 or self.max_redirects < 0:
            raise ValueError("Crawler budgets are invalid")
        if (
            min(
                self.max_duration_seconds,
                self.request_timeout_seconds,
                self.max_body_bytes,
                self.max_robots_bytes,
            )
            <= 0
        ):
            raise ValueError("Crawler time and byte limits must be positive")
        if self.min_host_delay_seconds < 0:
            raise ValueError("Host delay cannot be negative")
        if self.challenge_backoff_multiplier < 1:
            raise ValueError("Challenge backoff multiplier must be at least 1")
        if self.max_host_delay_seconds < self.min_host_delay_seconds:
            raise ValueError("max_host_delay_seconds cannot be below min_host_delay_seconds")
        if self.max_retry_after_seconds < 0:
            raise ValueError("max_retry_after_seconds cannot be negative")


@dataclass(frozen=True, slots=True)
class CrawledPage:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str | None
    body_sha256: str
    title: str | None
    meta_description: str | None
    h1: tuple[str, ...]
    canonical_url: str | None
    robots_directives: tuple[str, ...]
    links: tuple[str, ...]
    redirect_chain: tuple[str, ...]
    word_count: int | None = None
    body_bytes: int = 0
    response_ms: int | None = None
    images_total: int = 0
    images_missing_alt: int = 0
    schema_types: tuple[str, ...] = ()
    h2: tuple[str, ...] = ()
    external_links: tuple[str, ...] = ()
    og_title: bool = False
    og_description: bool = False
    lang: str | None = None
    viewport: bool = False
    hreflang_count: int = 0
    analytics_tags: tuple[str, ...] = ()
    challenge: bool = False
    challenge_kind: str | None = None
    retry_after: int | None = None


@dataclass(frozen=True, slots=True)
class CrawlFailure:
    url: str
    code: str
    message: str
    challenge: bool = False
    challenge_kind: str | None = None
    retry_after: int | None = None


@dataclass(frozen=True, slots=True)
class CrawlResult:
    pages: tuple[CrawledPage, ...]
    failures: tuple[CrawlFailure, ...]
    discovered_count: int
    stopped_reason: str
    challenged_count: int = 0
    rate_limited_count: int = 0


CHALLENGE_BODY_MARKERS: tuple[str, ...] = (
    "verifying your connection",
    "just a moment",
    "attention required",
    "checking your browser",
    "cf-browser-verification",
    "cf_chl_opt",
    "access denied",
    "enable javascript and cookies to continue",
    "ddos protection by",
    "please verify you are a human",
)
_CHALLENGE_PREVIEW_BYTES = 8_192
_RATE_LIMIT_KIND = "rate_limited"
_BOT_CHALLENGE_KIND = "bot_challenge"
_ACCESS_DENIED_KIND = "access_denied"


@dataclass(frozen=True, slots=True)
class ChallengeSignal:
    """Deterministic classification of a response as a challenge / throttle page."""

    challenge: bool
    kind: str | None = None
    retry_after: int | None = None
    marker: str | None = None

    @property
    def rate_limited(self) -> bool:
        return self.kind == _RATE_LIMIT_KIND


def parse_retry_after(value: str | None) -> int | None:
    """Parse a delta-seconds Retry-After header.  HTTP-date forms are not guessed."""

    if not value:
        return None
    text = value.strip()
    if not text.isdigit():
        return None
    try:
        seconds = int(text)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


CHALLENGE_MAX_BODY_BYTES = 100_000


def classify_challenge(
    status_code: int,
    headers: Mapping[str, str],
    body_text: str = "",
    body_bytes: int | None = None,
) -> ChallengeSignal:
    """Classify a fetched response as origin throttling / bot challenge, or not.

    The crawler must never turn its own rate limiting into client findings, so
    this classification is the single gate that quarantines such responses.
    A 2xx response is only treated as a challenge when it is both marker-bearing
    and small, so a real article that merely quotes a marker phrase is kept.
    """

    lookup = {str(name).casefold(): str(value) for name, value in (headers or {}).items()}
    retry_after = parse_retry_after(lookup.get("retry-after"))
    lowered = body_text.casefold()
    marker = next((needle for needle in CHALLENGE_BODY_MARKERS if needle in lowered), None)
    if status_code == 429:
        return ChallengeSignal(True, _RATE_LIMIT_KIND, retry_after, marker)
    if status_code == 503 and (retry_after is not None or marker is not None):
        return ChallengeSignal(True, _RATE_LIMIT_KIND, retry_after, marker)
    if status_code == 403 and marker is not None:
        return ChallengeSignal(True, _ACCESS_DENIED_KIND, retry_after, marker)
    if (
        marker is not None
        and 200 <= status_code < 300
        and (body_bytes is None or body_bytes <= CHALLENGE_MAX_BODY_BYTES)
    ):
        return ChallengeSignal(True, _BOT_CHALLENGE_KIND, retry_after, marker)
    return ChallengeSignal(False)


_ANALYTICS_SIGNATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ga4", ("gtag(", "googletagmanager.com/gtag")),
    ("gtm", ("googletagmanager.com/gtm.js", "GTM-")),
    ("ua", ("google-analytics.com/analytics.js",)),
    ("meta_pixel", ("connect.facebook.net",)),
    ("hotjar", ("static.hotjar.com",)),
    ("segment", ("cdn.segment.com",)),
)
_MAX_SCHEMA_TYPES = 20
_MAX_H2 = 40
_MAX_EXTERNAL_LINKS = 200


def detect_analytics_tags(html_text: str) -> tuple[str, ...]:
    """Detect well-known analytics signatures in raw HTML text (deterministic)."""

    lowered = html_text.casefold()
    detected: list[str] = []
    for tag, needles in _ANALYTICS_SIGNATURES:
        for needle in needles:
            # GTM container ids are uppercase by convention; match them case-sensitively.
            hit = (needle in html_text) if needle == "GTM-" else (needle.casefold() in lowered)
            if hit:
                detected.append(tag)
                break
    return tuple(detected)


def _collect_schema_types(node: Any, out: list[str]) -> None:
    """Collect @type values from a JSON-LD document, including nested @graph."""

    if isinstance(node, dict):
        declared = node.get("@type")
        if isinstance(declared, str) and declared.strip():
            out.append(declared.strip())
        elif isinstance(declared, list):
            out.extend(item.strip() for item in declared if isinstance(item, str) and item.strip())
        for value in node.values():
            _collect_schema_types(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_schema_types(value, out)


def _itemtype_tail(token: str) -> str:
    tail = token.strip().rstrip("/")
    if "#" in tail:
        tail = tail.rsplit("#", 1)[-1]
    if "/" in tail:
        tail = tail.rsplit("/", 1)[-1]
    return tail


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    title: str | None = None
    meta_description: str | None = None
    h1: tuple[str, ...] = ()
    canonical_url: str | None = None
    robots_directives: tuple[str, ...] = ()
    links: tuple[str, ...] = ()
    word_count: int = 0
    images_total: int = 0
    images_missing_alt: int = 0
    schema_types: tuple[str, ...] = ()
    h2: tuple[str, ...] = ()
    external_links: tuple[str, ...] = ()
    og_title: bool = False
    og_description: bool = False
    lang: str | None = None
    viewport: bool = False
    hreflang_count: int = 0


class _DocumentParser(HTMLParser):
    _SKIPPED = frozenset({"script", "style", "noscript"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.canonical: str | None = None
        self.title_parts: list[str] = []
        self.h1_parts: list[list[str]] = []
        self.h2_parts: list[list[str]] = []
        self.meta_description: str | None = None
        self.robots: list[str] = []
        self.text_parts: list[str] = []
        self.images_total = 0
        self.images_missing_alt = 0
        self.schema_type_values: list[str] = []
        self.og_title = False
        self.og_description = False
        self.lang: str | None = None
        self.viewport = False
        self.hreflang_count = 0
        self._in_title = False
        self._in_h1 = False
        self._in_h2 = False
        self._skip_depth = 0
        self._in_ldjson = False
        self._ldjson_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.casefold(): (value or "") for name, value in attrs}
        lowered = tag.casefold()
        if lowered in self._SKIPPED:
            self._skip_depth += 1
            if lowered == "script":
                media = values.get("type", "").split(";", 1)[0].strip().casefold()
                if media == "application/ld+json":
                    self._in_ldjson = True
                    self._ldjson_parts = []
        itemtype = values.get("itemtype", "")
        if itemtype:
            for token in itemtype.split():
                tail = _itemtype_tail(token)
                if tail:
                    self.schema_type_values.append(tail)
        if lowered == "html" and self.lang is None:
            self.lang = values.get("lang", "").strip() or None
        elif lowered == "a" and values.get("href"):
            self.links.append(values["href"])
        elif lowered == "link":
            rel = values.get("rel", "").casefold().split()
            if "canonical" in rel:
                self.canonical = values.get("href") or self.canonical
            if "alternate" in rel and values.get("hreflang", "").strip():
                self.hreflang_count += 1
        elif lowered == "img":
            self.images_total += 1
            if not values.get("alt", "").strip():
                self.images_missing_alt += 1
        elif lowered == "meta":
            name = values.get("name", "").casefold()
            prop = values.get("property", "").casefold()
            if name == "description" and self.meta_description is None:
                self.meta_description = values.get("content", "").strip() or None
            elif name in {"robots", "googlebot"}:
                self.robots.extend(
                    item.strip().casefold()
                    for item in values.get("content", "").split(",")
                    if item.strip()
                )
            elif name == "viewport":
                self.viewport = True
            if prop == "og:title" and values.get("content", "").strip():
                self.og_title = True
            elif prop == "og:description" and values.get("content", "").strip():
                self.og_description = True
        elif lowered == "title":
            self._in_title = True
        elif lowered == "h1":
            self._in_h1 = True
            self.h1_parts.append([])
        elif lowered == "h2":
            self._in_h2 = True
            if len(self.h2_parts) < _MAX_H2:
                self.h2_parts.append([])

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in self._SKIPPED:
            self._skip_depth = max(0, self._skip_depth - 1)
            if lowered == "script" and self._in_ldjson:
                self._in_ldjson = False
                payload = "".join(self._ldjson_parts).strip()
                self._ldjson_parts = []
                if payload:
                    try:
                        document = json.loads(payload)
                        _collect_schema_types(document, self.schema_type_values)
                    except (ValueError, TypeError, RecursionError):
                        pass  # Malformed structured data is ignored, never fabricated.
        elif lowered == "title":
            self._in_title = False
        elif lowered == "h1":
            self._in_h1 = False
        elif lowered == "h2":
            self._in_h2 = False

    def handle_data(self, data: str) -> None:
        if self._in_ldjson:
            self._ldjson_parts.append(data)
            return
        if self._skip_depth == 0 and data.strip():
            self.text_parts.append(data)
        if self._in_title:
            self.title_parts.append(data)
        if self._in_h1 and self.h1_parts:
            self.h1_parts[-1].append(data)
        if self._in_h2 and self.h2_parts:
            self.h2_parts[-1].append(data)

    def result(self, base_url: str, allowed_domains: tuple[str, ...]) -> ParsedDocument:
        title = " ".join("".join(self.title_parts).split()) or None
        h1 = tuple(" ".join("".join(parts).split()) for parts in self.h1_parts)
        h2 = tuple(
            heading
            for heading in (" ".join("".join(parts).split()) for parts in self.h2_parts)
            if heading
        )[:_MAX_H2]
        canonical: str | None = None
        if self.canonical:
            try:
                canonical = normalize_url(self.canonical, base=base_url)
            except URLValidationError:
                canonical = None
        links: set[str] = set()
        external: set[str] = set()
        for href in self.links:
            try:
                normalized = normalize_url(href, base=base_url)
            except URLValidationError:
                continue
            try:
                links.add(require_allowed_url(normalized, allowed_domains))
            except URLValidationError:
                external.add(normalized)
        schema_types = tuple(dict.fromkeys(self.schema_type_values))[:_MAX_SCHEMA_TYPES]
        return ParsedDocument(
            title=title,
            meta_description=self.meta_description,
            h1=h1,
            canonical_url=canonical,
            robots_directives=tuple(dict.fromkeys(self.robots)),
            links=tuple(sorted(links)),
            word_count=len(" ".join(self.text_parts).split()),
            images_total=self.images_total,
            images_missing_alt=self.images_missing_alt,
            schema_types=schema_types,
            h2=h2,
            external_links=tuple(sorted(external))[:_MAX_EXTERNAL_LINKS],
            og_title=self.og_title,
            og_description=self.og_description,
            lang=self.lang,
            viewport=self.viewport,
            hreflang_count=self.hreflang_count,
        )


def _charset_of(content_type: str | None) -> str:
    if content_type and "charset=" in content_type.casefold():
        return (
            content_type.casefold()
            .split("charset=", 1)[1]
            .split(";", 1)[0]
            .strip()
            .strip('"')
        ) or "utf-8"
    return "utf-8"


def _decode_body(content_type: str | None, body: bytes) -> str:
    charset = _charset_of(content_type)
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


class RobotsCache:
    """Conservative robots policy cache scoped to one crawl run."""

    def __init__(
        self,
        guard: SSRFGuard,
        transport: SafeTransport,
        config: CrawlConfig,
    ) -> None:
        self.guard = guard
        self.transport = transport
        self.config = config
        self._policies: dict[str, RobotFileParser | bool] = {}

    def can_fetch(self, url: str) -> bool:
        if not self.config.obey_robots:
            return True
        parsed = urlsplit(url)
        origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        if origin not in self._policies:
            robots_url = origin + "/robots.txt"
            try:
                response = self.transport.fetch(
                    self.guard.validate(robots_url),
                    headers={"User-Agent": self.config.user_agent},
                    timeout=self.config.request_timeout_seconds,
                    max_bytes=self.config.max_robots_bytes,
                )
            except (CrawlError, URLValidationError, TimeoutError, OSError):
                # Failure to retrieve robots is not evidence of permission.
                self._policies[origin] = False
            else:
                if 200 <= response.status_code < 300:
                    parser = RobotFileParser()
                    parser.set_url(robots_url)
                    parser.parse(response.body.decode("utf-8", errors="replace").splitlines())
                    self._policies[origin] = parser
                elif response.status_code == 429 or response.status_code >= 500:
                    # Temporary refusal or server trouble: stay conservative.
                    self._policies[origin] = False
                else:
                    # 401/403 robots.txt responses usually come from bot
                    # challenges on sites whose owners commissioned the audit;
                    # treating them as a total crawl ban blanks the whole run.
                    self._policies[origin] = True
        policy = self._policies[origin]
        if isinstance(policy, bool):
            return policy
        return policy.can_fetch(self.config.user_agent, url)


class BoundedCrawler:
    def __init__(
        self,
        config: CrawlConfig,
        *,
        guard: SSRFGuard | None = None,
        transport: SafeTransport | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.guard = guard or SSRFGuard(config.allowed_domains)
        self.transport = transport or PinnedHTTPTransport()
        self.monotonic = monotonic
        self.sleep = sleep
        self.robots = RobotsCache(self.guard, self.transport, config)
        self._last_request: dict[str, float] = {}
        self._host_delay: dict[str, float] = {}

    @staticmethod
    def _host_of(url: str) -> str:
        return urlsplit(url).netloc.casefold()

    def host_delay(self, host: str) -> float:
        return self._host_delay.get(host, self.config.min_host_delay_seconds)

    def _apply_backoff(self, host: str) -> float:
        """Multiply the per-host delay after a throttle signal, capped by config."""

        current = self.host_delay(host)
        widened = max(current, self.config.min_host_delay_seconds) * (
            self.config.challenge_backoff_multiplier
        )
        # A zero configured delay must still become a real delay once throttled.
        if widened <= 0:
            widened = min(1.0, self.config.max_host_delay_seconds)
        delay = min(widened, self.config.max_host_delay_seconds)
        self._host_delay[host] = delay
        return delay

    def _throttle(self, url: str) -> None:
        host = self._host_of(url)
        now = self.monotonic()
        last = self._last_request.get(host)
        if last is not None:
            remaining = self.host_delay(host) - (now - last)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request[host] = self.monotonic()

    def _raise_for_challenge(
        self,
        response: FetchResponse,
        chain: tuple[str, ...],
        elapsed_ms: int,
    ) -> None:
        """Convert throttle/challenge responses into a typed error, never page content."""

        content_type = response.headers.get("content-type")
        media_type = (content_type or "").split(";", 1)[0].strip().casefold()
        preview = ""
        if not media_type or media_type.startswith("text/") or "html" in media_type:
            preview = _decode_body(content_type, response.body[:_CHALLENGE_PREVIEW_BYTES])
        signal = classify_challenge(
            response.status_code, response.headers, preview, len(response.body)
        )
        if not signal.challenge:
            return
        error_type = RateLimited if signal.rate_limited else ChallengeResponse
        raise error_type(
            f"HTTP {response.status_code} {signal.kind} response",
            kind=signal.kind or _BOT_CHALLENGE_KIND,
            response=response,
            retry_after=signal.retry_after,
            redirect_chain=chain,
            response_ms=elapsed_ms,
        )

    def _fetch_following_redirects(
        self, url: str
    ) -> tuple[FetchResponse, tuple[str, ...], int]:
        current = url
        chain = [url]
        for hop in range(self.config.max_redirects + 1):
            if not self.robots.can_fetch(current):
                raise CrawlError("robots.txt does not permit this URL")
            self._throttle(current)
            fetch_started = self.monotonic()
            response = self.transport.fetch(
                self.guard.validate(current),
                headers={"User-Agent": self.config.user_agent},
                timeout=self.config.request_timeout_seconds,
                max_bytes=self.config.max_body_bytes,
            )
            elapsed_ms = max(0, int(round((self.monotonic() - fetch_started) * 1000)))
            if response.status_code not in {301, 302, 303, 307, 308}:
                self._raise_for_challenge(response, tuple(chain), elapsed_ms)
                return response, tuple(chain), elapsed_ms
            location = response.headers.get("location")
            if not location:
                raise CrawlError("Redirect response is missing Location")
            if hop >= self.config.max_redirects:
                raise CrawlError("Redirect limit exceeded")
            current = self.guard.validate_redirect(current, location).normalized_url
            if current in chain:
                raise CrawlError("Redirect loop detected")
            chain.append(current)
        raise CrawlError("Redirect limit exceeded")

    def _requeue_after_challenge(
        self,
        url: str,
        depth: int,
        error: ChallengeResponse,
        queue: deque[tuple[str, int]],
        retried: set[str],
        started: float,
    ) -> bool:
        """Back off this host and retry the URL once, inside the run's duration budget."""

        self._apply_backoff(self._host_of(url))
        if url in retried:
            return False
        wait = 0.0
        if error.retry_after is not None:
            wait = min(float(error.retry_after), self.config.max_retry_after_seconds)
        remaining = self.config.max_duration_seconds - (self.monotonic() - started)
        if remaining <= wait:
            # Waiting would blow the run budget: quarantine instead of stalling.
            return False
        if wait > 0:
            self.sleep(wait)
            self._last_request[self._host_of(url)] = self.monotonic()
        retried.add(url)
        queue.append((url, depth))
        return True

    def _challenged_page(self, url: str, error: ChallengeResponse) -> CrawledPage:
        """Record a challenge interstitial as quarantined evidence, never as page content.

        Title, headings and word counts are deliberately left empty: the bytes we
        received belong to the protection layer, not to the audited page.
        """

        response = error.response
        body = response.body if response is not None else b""
        return CrawledPage(
            requested_url=url,
            final_url=response.url if response is not None else url,
            status_code=response.status_code if response is not None else 429,
            content_type=(response.headers.get("content-type") if response is not None else None),
            body_sha256=hashlib.sha256(body).hexdigest(),
            title=None,
            meta_description=None,
            h1=(),
            canonical_url=None,
            robots_directives=(),
            links=(),
            redirect_chain=error.redirect_chain or (url,),
            word_count=None,
            body_bytes=len(body),
            response_ms=error.response_ms,
            challenge=True,
            challenge_kind=error.kind,
            retry_after=error.retry_after,
        )

    def crawl(self, seeds: tuple[str, ...]) -> CrawlResult:
        if not seeds:
            raise ValueError("At least one crawl seed is required")
        queue: deque[tuple[str, int]] = deque()
        discovered: set[str] = set()
        for seed in seeds:
            normalized = require_allowed_url(seed, self.config.allowed_domains)
            if normalized not in discovered:
                discovered.add(normalized)
                queue.append((normalized, 0))
        pages: list[CrawledPage] = []
        failures: list[CrawlFailure] = []
        retried: set[str] = set()
        started = self.monotonic()
        stopped_reason = "queue_exhausted"
        while queue:
            if len(pages) + len(failures) >= self.config.max_pages:
                stopped_reason = "page_budget_reached"
                break
            if self.monotonic() - started >= self.config.max_duration_seconds:
                stopped_reason = "duration_budget_reached"
                break
            requested, depth = queue.popleft()
            try:
                response, redirects, response_ms = self._fetch_following_redirects(requested)
            except ChallengeResponse as exc:
                if self._requeue_after_challenge(requested, depth, exc, queue, retried, started):
                    continue
                pages.append(self._challenged_page(requested, exc))
                failures.append(
                    CrawlFailure(
                        requested,
                        "challenge_response",
                        f"Origin returned a {exc.kind} response; the URL was quarantined.",
                        challenge=True,
                        challenge_kind=exc.kind,
                        retry_after=exc.retry_after,
                    )
                )
                continue
            except Exception as exc:  # boundary: convert provider details into safe crawl status
                failures.append(CrawlFailure(requested, "fetch_failed", type(exc).__name__))
                continue
            content_type = response.headers.get("content-type")
            media_type = (content_type or "").split(";", 1)[0].strip().casefold()
            document = ParsedDocument()
            analytics: tuple[str, ...] = ()
            word_count: int | None = None
            if media_type in {"text/html", "application/xhtml+xml"}:
                text = _decode_body(content_type, response.body)
                parser = _DocumentParser()
                try:
                    parser.feed(text)
                    document = parser.result(response.url, self.config.allowed_domains)
                    word_count = document.word_count
                except (TypeError, ValueError):
                    document = ParsedDocument()
                    word_count = None
                    failures.append(
                        CrawlFailure(requested, "html_parse_failed", "HTML parse failed safely")
                    )
                analytics = detect_analytics_tags(text)
            links = document.links
            pages.append(
                CrawledPage(
                    requested_url=requested,
                    final_url=response.url,
                    status_code=response.status_code,
                    content_type=content_type,
                    body_sha256=hashlib.sha256(response.body).hexdigest(),
                    title=document.title,
                    meta_description=document.meta_description,
                    h1=document.h1,
                    canonical_url=document.canonical_url,
                    robots_directives=document.robots_directives,
                    links=links,
                    redirect_chain=redirects,
                    word_count=word_count,
                    body_bytes=len(response.body),
                    response_ms=response_ms,
                    images_total=document.images_total,
                    images_missing_alt=document.images_missing_alt,
                    schema_types=document.schema_types,
                    h2=document.h2,
                    external_links=document.external_links,
                    og_title=document.og_title,
                    og_description=document.og_description,
                    lang=document.lang,
                    viewport=document.viewport,
                    hreflang_count=document.hreflang_count,
                    analytics_tags=analytics,
                )
            )
            if depth < self.config.max_depth:
                for link in links:
                    if link not in discovered:
                        discovered.add(link)
                        queue.append((link, depth + 1))
        challenged = tuple(page for page in pages if page.challenge)
        return CrawlResult(
            tuple(pages),
            tuple(failures),
            len(discovered),
            stopped_reason,
            challenged_count=len(challenged),
            rate_limited_count=sum(
                1 for page in challenged if page.challenge_kind == _RATE_LIMIT_KIND
            ),
        )
