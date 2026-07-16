# ruff: noqa: E501
"""Small, explainable rules over frozen crawl evidence."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from .models import BusinessProfile, Finding, PageSnapshot, RiskClass, Severity
from .urls import URLValidationError, require_allowed_url

RULESET_VERSION = "1.0.0"

BASE_MODULES = (
    "technical",
    "on_page",
    "performance",
    "analytics",
    "keyword_architecture",
    "authority",
    "cro",
    "geo_aeo",
)


def enabled_modules(profile: BusinessProfile) -> tuple[str, ...]:
    extras = {
        BusinessProfile.SERVICE_SAAS: (),
        BusinessProfile.LOCAL: ("local",),
        BusinessProfile.ECOMMERCE: ("ecommerce",),
        BusinessProfile.HYBRID: ("local", "ecommerce"),
    }[profile]
    return BASE_MODULES + extras


@dataclass(frozen=True, slots=True)
class AuditContext:
    project_id: str
    pages: tuple[PageSnapshot, ...]
    allowed_domains: tuple[str, ...]
    business_profile: BusinessProfile


class Rule(Protocol):
    rule_id: str
    version: str
    category: str

    def evaluate(self, context: AuditContext) -> Iterable[Finding]: ...


def _finding(
    context: AuditContext,
    page: PageSnapshot,
    *,
    category: str,
    rule_id: str,
    severity: Severity,
    title: str,
    description: str,
    risk: RiskClass = RiskClass.LOW,
) -> Finding:
    denominator = max(1, len(context.pages))
    return Finding(
        id=str(uuid4()),
        project_id=context.project_id,
        category=category,
        rule_id=rule_id,
        rule_version=RULESET_VERSION,
        severity=severity,
        title=title,
        description=description,
        evidence_ids=(page.evidence_id,),
        affected_urls=(page.normalized_url,),
        affected_share=1 / denominator,
        confidence=1.0,
        risk=risk,
    )


class HTTPStatusRule:
    rule_id = "technical.http_status"
    version = RULESET_VERSION
    category = "technical"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            if page.status_code is None or page.status_code < 400:
                continue
            critical = page.status_code >= 500
            yield _finding(
                context,
                page,
                category=self.category,
                rule_id=self.rule_id,
                severity=Severity.CRITICAL if critical else Severity.HIGH,
                title=f"HTTP {page.status_code} response",
                description="The crawled URL did not return a successful or redirect response.",
            )


class TitleRule:
    rule_id = "on_page.title"
    version = RULESET_VERSION
    category = "on_page"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            if page.status_code is None or not 200 <= page.status_code < 300:
                continue
            if not page.title or not page.title.strip():
                yield _finding(
                    context,
                    page,
                    category=self.category,
                    rule_id=self.rule_id,
                    severity=Severity.HIGH,
                    title="Missing document title",
                    description="The successful HTML page has no non-empty title element.",
                )
            elif len(page.title.strip()) > 65:
                yield _finding(
                    context,
                    page,
                    category=self.category,
                    rule_id=self.rule_id,
                    severity=Severity.LOW,
                    title="Long document title",
                    description="The title is longer than the configured 65-character editorial review threshold.",
                )


class MetaDescriptionRule:
    rule_id = "on_page.meta_description"
    version = RULESET_VERSION
    category = "on_page"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            if (
                page.status_code is not None
                and 200 <= page.status_code < 300
                and (not page.meta_description or not page.meta_description.strip())
            ):
                yield _finding(
                    context,
                    page,
                    category=self.category,
                    rule_id=self.rule_id,
                    severity=Severity.MEDIUM,
                    title="Missing meta description",
                    description="The successful HTML page has no non-empty meta description.",
                )


class H1Rule:
    rule_id = "on_page.h1"
    version = RULESET_VERSION
    category = "on_page"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            if page.status_code is None or not 200 <= page.status_code < 300:
                continue
            nonempty = tuple(value.strip() for value in page.h1 if value.strip())
            if not nonempty:
                yield _finding(
                    context,
                    page,
                    category=self.category,
                    rule_id=self.rule_id,
                    severity=Severity.HIGH,
                    title="Missing primary heading",
                    description="The successful HTML page has no non-empty H1 heading.",
                )
            elif len(nonempty) > 1:
                yield _finding(
                    context,
                    page,
                    category=self.category,
                    rule_id=self.rule_id,
                    severity=Severity.MEDIUM,
                    title="Multiple primary headings",
                    description=f"The page contains {len(nonempty)} non-empty H1 headings and needs editorial review.",
                )


class CanonicalBoundaryRule:
    rule_id = "technical.canonical_boundary"
    version = RULESET_VERSION
    category = "technical"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            if not page.canonical_url:
                continue
            try:
                require_allowed_url(page.canonical_url, context.allowed_domains)
            except URLValidationError:
                yield _finding(
                    context,
                    page,
                    category=self.category,
                    rule_id=self.rule_id,
                    severity=Severity.HIGH,
                    title="Canonical points outside approved domains",
                    description="The declared canonical target is outside the project's approved domain boundary.",
                    risk=RiskClass.HIGH,
                )


class RobotsDirectiveRule:
    rule_id = "technical.robots_directive"
    version = RULESET_VERSION
    category = "technical"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            directives = {value.strip().casefold() for value in page.robots_directives}
            if "noindex" in directives or "none" in directives:
                yield _finding(
                    context,
                    page,
                    category=self.category,
                    rule_id=self.rule_id,
                    severity=Severity.LOW,
                    title="Noindex directive detected",
                    description="A noindex-equivalent directive was observed; human review must confirm intent.",
                    risk=RiskClass.MODERATE,
                )


DEFAULT_RULES: tuple[Rule, ...] = (
    HTTPStatusRule(),
    TitleRule(),
    MetaDescriptionRule(),
    H1Rule(),
    CanonicalBoundaryRule(),
    RobotsDirectiveRule(),
)


def run_rules(context: AuditContext, rules: Iterable[Rule] = DEFAULT_RULES) -> tuple[Finding, ...]:
    active = set(enabled_modules(context.business_profile))
    findings: list[Finding] = []
    for rule in rules:
        if rule.category in active:
            findings.extend(rule.evaluate(context))
    return tuple(findings)
