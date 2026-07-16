"""Security and normalization tests for audit URL boundaries."""

from __future__ import annotations

import socket

import pytest

from audit_engine.urls import (
    SSRFBlockedError,
    SSRFGuard,
    URLValidationError,
    host_is_allowed,
    normalize_url,
)


def resolver_for(*addresses: str):
    def resolve(host: str, port: int, family: int, kind: int):
        del host, family
        return [
            (
                socket.AF_INET6 if ":" in value else socket.AF_INET,
                kind,
                6,
                "",
                (value, port),
            )
            for value in addresses
        ]

    return resolve


def test_normalize_url_removes_fragment_tracking_default_port_and_dot_segments() -> None:
    actual = normalize_url(" HTTPS://Example.COM:443/a/../b/?z=2&utm_source=x&a=1#section ")
    assert actual == "https://example.com/b/?a=1&z=2"


def test_normalize_url_resolves_relative_and_encoded_dot_segments() -> None:
    assert normalize_url("../%2e%2e/products", base="https://example.com/a/b/") == (
        "https://example.com/products"
    )


def test_normalize_url_preserves_encoded_path_separator() -> None:
    assert normalize_url("https://example.com/a%2fb") == "https://example.com/a%2Fb"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://user:pass@example.com/",
        "https://example.com:70000/",
        "https://example.com/ok\nX-Test: injected",
        "https://example.com/a\\b",
    ],
)
def test_normalize_url_rejects_unsafe_forms(url: str) -> None:
    with pytest.raises(URLValidationError):
        normalize_url(url)


def test_allowlist_uses_label_boundary_not_suffix() -> None:
    assert host_is_allowed("shop.example.com", ("example.com",))
    assert not host_is_allowed("example.com.attacker.test", ("example.com",))
    assert not host_is_allowed("notexample.com", ("example.com",))


def test_ssrf_guard_returns_pinned_public_answers() -> None:
    guard = SSRFGuard(("example.com",), resolver=resolver_for("93.184.216.34"))
    target = guard.validate("https://www.example.com/path")
    assert target.hostname == "www.example.com"
    assert target.approved_ips == ("93.184.216.34",)
    assert target.port == 443


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.1", "169.254.169.254", "100.64.0.1", "::1", "fe80::1"],
)
def test_ssrf_guard_rejects_non_public_answers(address: str) -> None:
    guard = SSRFGuard(("example.com",), resolver=resolver_for(address))
    with pytest.raises(SSRFBlockedError):
        guard.validate("https://example.com/")


def test_ssrf_guard_rejects_mixed_public_and_private_dns_answers() -> None:
    guard = SSRFGuard(
        ("example.com",),
        resolver=resolver_for("93.184.216.34", "127.0.0.1"),
    )
    with pytest.raises(SSRFBlockedError):
        guard.validate("https://example.com/")


def test_redirect_is_revalidated_against_allowlist() -> None:
    guard = SSRFGuard(("example.com",), resolver=resolver_for("93.184.216.34"))
    with pytest.raises(URLValidationError):
        guard.validate_redirect("https://example.com/a", "https://attacker.test/")
