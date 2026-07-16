"""Robots-aware, bounded crawler with DNS-pinned HTTP connections.

The crawler never follows a redirect or opens a socket before ``SSRFGuard`` has
approved the hostname and every resolved address.  The transport connects to
that approved address directly, closing the common DNS-rebinding gap between
validation and connection.
"""

from __future__ import annotations

import hashlib
import http.client
import socket
import ssl
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .urls import ResolvedTarget, SSRFGuard, URLValidationError, normalize_url, require_allowed_url


class CrawlError(RuntimeError):
    pass


class ResponseTooLarge(CrawlError):
    pass


class UnsafeHTTPResponse(CrawlError):
    pass


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


@dataclass(frozen=True, slots=True)
class CrawlFailure:
    url: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class CrawlResult:
    pages: tuple[CrawledPage, ...]
    failures: tuple[CrawlFailure, ...]
    discovered_count: int
    stopped_reason: str


class _DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.canonical: str | None = None
        self.title_parts: list[str] = []
        self.h1_parts: list[list[str]] = []
        self.meta_description: str | None = None
        self.robots: list[str] = []
        self._in_title = False
        self._in_h1 = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.casefold(): (value or "") for name, value in attrs}
        lowered = tag.casefold()
        if lowered == "a" and values.get("href"):
            self.links.append(values["href"])
        elif lowered == "link" and "canonical" in values.get("rel", "").casefold().split():
            self.canonical = values.get("href") or self.canonical
        elif lowered == "meta":
            name = values.get("name", "").casefold()
            if name == "description" and self.meta_description is None:
                self.meta_description = values.get("content", "").strip() or None
            elif name in {"robots", "googlebot"}:
                self.robots.extend(
                    item.strip().casefold()
                    for item in values.get("content", "").split(",")
                    if item.strip()
                )
        elif lowered == "title":
            self._in_title = True
        elif lowered == "h1":
            self._in_h1 = True
            self.h1_parts.append([])

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered == "title":
            self._in_title = False
        elif lowered == "h1":
            self._in_h1 = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._in_h1 and self.h1_parts:
            self.h1_parts[-1].append(data)

    def result(
        self, base_url: str, allowed_domains: tuple[str, ...]
    ) -> tuple[
        str | None, str | None, tuple[str, ...], str | None, tuple[str, ...], tuple[str, ...]
    ]:
        title = " ".join("".join(self.title_parts).split()) or None
        h1 = tuple(" ".join("".join(parts).split()) for parts in self.h1_parts)
        canonical: str | None = None
        if self.canonical:
            try:
                canonical = normalize_url(self.canonical, base=base_url)
            except URLValidationError:
                canonical = None
        links: set[str] = set()
        for href in self.links:
            try:
                normalized = require_allowed_url(
                    normalize_url(href, base=base_url), allowed_domains
                )
            except URLValidationError:
                continue
            links.add(normalized)
        return (
            title,
            self.meta_description,
            h1,
            canonical,
            tuple(dict.fromkeys(self.robots)),
            tuple(sorted(links)),
        )


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
                elif response.status_code in {401, 403, 429} or response.status_code >= 500:
                    self._policies[origin] = False
                else:
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

    def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc.casefold()
        now = self.monotonic()
        last = self._last_request.get(host)
        if last is not None:
            remaining = self.config.min_host_delay_seconds - (now - last)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request[host] = self.monotonic()

    def _fetch_following_redirects(self, url: str) -> tuple[FetchResponse, tuple[str, ...]]:
        current = url
        chain = [url]
        for hop in range(self.config.max_redirects + 1):
            if not self.robots.can_fetch(current):
                raise CrawlError("robots.txt does not permit this URL")
            self._throttle(current)
            response = self.transport.fetch(
                self.guard.validate(current),
                headers={"User-Agent": self.config.user_agent},
                timeout=self.config.request_timeout_seconds,
                max_bytes=self.config.max_body_bytes,
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response, tuple(chain)
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
                response, redirects = self._fetch_following_redirects(requested)
            except Exception as exc:  # boundary: convert provider details into safe crawl status
                failures.append(CrawlFailure(requested, "fetch_failed", type(exc).__name__))
                continue
            content_type = response.headers.get("content-type")
            media_type = (content_type or "").split(";", 1)[0].strip().casefold()
            title = description = canonical = None
            h1: tuple[str, ...] = ()
            directives: tuple[str, ...] = ()
            links: tuple[str, ...] = ()
            if media_type in {"text/html", "application/xhtml+xml"}:
                charset = "utf-8"
                if content_type and "charset=" in content_type.casefold():
                    charset = (
                        content_type.casefold()
                        .split("charset=", 1)[1]
                        .split(";", 1)[0]
                        .strip()
                        .strip('"')
                    )
                try:
                    text = response.body.decode(charset, errors="replace")
                except LookupError:
                    text = response.body.decode("utf-8", errors="replace")
                parser = _DocumentParser()
                try:
                    parser.feed(text)
                    title, description, h1, canonical, directives, links = parser.result(
                        response.url, self.config.allowed_domains
                    )
                except (TypeError, ValueError):
                    failures.append(
                        CrawlFailure(requested, "html_parse_failed", "HTML parse failed safely")
                    )
            pages.append(
                CrawledPage(
                    requested_url=requested,
                    final_url=response.url,
                    status_code=response.status_code,
                    content_type=content_type,
                    body_sha256=hashlib.sha256(response.body).hexdigest(),
                    title=title,
                    meta_description=description,
                    h1=h1,
                    canonical_url=canonical,
                    robots_directives=directives,
                    links=links,
                    redirect_chain=redirects,
                )
            )
            if depth < self.config.max_depth:
                for link in links:
                    if link not in discovered:
                        discovered.add(link)
                        queue.append((link, depth + 1))
        return CrawlResult(tuple(pages), tuple(failures), len(discovered), stopped_reason)
