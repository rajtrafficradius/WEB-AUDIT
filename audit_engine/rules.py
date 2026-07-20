# ruff: noqa: E501
"""Small, explainable rules over frozen crawl evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from .models import BusinessProfile, Finding, PageSnapshot, RiskClass, Severity
from .urls import URLValidationError, require_allowed_url

RULESET_VERSION = "1.1.0"

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
class CrawlIntegrity:
    """How much of the site we were actually allowed to read.

    ``challenged`` URLs were answered by a bot challenge or throttle response,
    so nothing about them may be reported as a defect of the client's site.
    """

    fetched_pages: int = 0
    challenged_pages: int = 0
    rate_limited_pages: int = 0
    quarantined_urls: tuple[str, ...] = ()
    note: str = ""

    @property
    def challenge_share(self) -> float:
        total = self.fetched_pages + self.challenged_pages
        return (self.challenged_pages / total) if total else 0.0

    @property
    def status(self) -> str:
        share = self.challenge_share
        if share > 0.30:
            return "blocked"
        if share > 0.05:
            return "degraded"
        return "clean"


@dataclass(frozen=True, slots=True)
class AuditContext:
    project_id: str
    pages: tuple[PageSnapshot, ...]
    allowed_domains: tuple[str, ...]
    business_profile: BusinessProfile
    challenged_urls: frozenset[str] = frozenset()
    crawl_integrity: CrawlIntegrity | None = None

    def evaluable_pages(self) -> tuple[PageSnapshot, ...]:
        """Pages that may legitimately produce findings (quarantine excluded)."""

        if not self.challenged_urls:
            return self.pages
        return tuple(
            page for page in self.pages if page.normalized_url not in self.challenged_urls
        )


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


_AFFECTED_URL_CAP = 50
_STOPWORDS = frozenset(
    {"a", "an", "and", "are", "at", "by", "for", "from", "in", "is", "of", "on", "or", "the", "to", "with", "your", "our"}
)
_UTILITY_SEGMENTS = frozenset(
    {
        "privacy", "privacy-policy", "terms", "terms-of-service", "terms-and-conditions",
        "login", "log-in", "signin", "sign-in", "signup", "sign-up", "register",
        "cart", "checkout", "account", "my-account", "search", "tag", "tags",
        "legal", "cookies", "cookie-policy", "thank-you", "unsubscribe", "404", "sitemap",
    }
)


def _is_quarantined(context: AuditContext, page: PageSnapshot) -> bool:
    """Defence in depth: rules invoked directly still skip challenged URLs."""

    return page.normalized_url in context.challenged_urls


def _is_success(page: PageSnapshot) -> bool:
    return page.status_code is not None and 200 <= page.status_code < 300


def _is_success_html(page: PageSnapshot) -> bool:
    """Successful page that is (or is presumed to be) an HTML document."""

    if not _is_success(page):
        return False
    return not page.content_type or "html" in page.content_type.casefold()


def _path_of(page: PageSnapshot) -> str:
    return urlsplit(page.normalized_url).path or "/"


def _is_homepage(page: PageSnapshot) -> bool:
    return _path_of(page) in {"", "/"}


def _is_utility_page(page: PageSnapshot) -> bool:
    segments = {segment.casefold() for segment in _path_of(page).split("/") if segment}
    return bool(segments & _UTILITY_SEGMENTS)


def _first_h1(page: PageSnapshot) -> str | None:
    for value in page.h1:
        if value.strip():
            return " ".join(value.split()).casefold()
    return None


def _title_tokens(title: str) -> frozenset[str]:
    tokens = frozenset(re.findall(r"[a-z0-9]+", title.casefold()))
    return tokens - _STOPWORDS


def _schema_types_casefolded(page: PageSnapshot) -> frozenset[str]:
    return frozenset(value.casefold() for value in page.schema_types)


def _site_finding(
    context: AuditContext,
    affected_pages: Sequence[PageSnapshot],
    *,
    category: str,
    rule_id: str,
    severity: Severity,
    title: str,
    description: str,
    affected_count: int | None = None,
    risk: RiskClass = RiskClass.LOW,
) -> Finding:
    total = max(1, len(context.pages))
    count = affected_count if affected_count is not None else len(affected_pages)
    return Finding(
        id=str(uuid4()),
        project_id=context.project_id,
        category=category,
        rule_id=rule_id,
        rule_version=RULESET_VERSION,
        severity=severity,
        title=title,
        description=description,
        evidence_ids=tuple(dict.fromkeys(page.evidence_id for page in affected_pages))[:_AFFECTED_URL_CAP],
        affected_urls=tuple(dict.fromkeys(page.normalized_url for page in affected_pages))[:_AFFECTED_URL_CAP],
        affected_share=min(1.0, count / total),
        confidence=1.0,
        risk=risk,
    )


class HTTPStatusRule:
    rule_id = "technical.http_status"
    version = RULESET_VERSION
    category = "technical"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            if _is_quarantined(context, page):
                continue
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
            if _is_quarantined(context, page):
                continue
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
                not _is_quarantined(context, page)
                and page.status_code is not None
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
            if _is_quarantined(context, page):
                continue
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


class RedirectChainLengthRule:
    rule_id = "technical.redirect_chain_length"
    version = RULESET_VERSION
    category = "technical"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            hops = max(0, len(page.redirect_chain) - 1)
            if len(page.redirect_chain) > 2:
                yield _finding(
                    context,
                    page,
                    category=self.category,
                    rule_id=self.rule_id,
                    severity=Severity.MEDIUM,
                    title="Long redirect chain",
                    description=f"The URL only resolved after {hops} redirect hops; every extra hop wastes crawl budget and dilutes link signals.",
                )


class CanonicalMissingRule:
    rule_id = "technical.canonical_missing"
    version = RULESET_VERSION
    category = "technical"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            if _is_success_html(page) and not (page.canonical_url or "").strip():
                yield _finding(
                    context,
                    page,
                    category=self.category,
                    rule_id=self.rule_id,
                    severity=Severity.LOW,
                    title="Missing canonical tag",
                    description="The successful HTML page declares no canonical URL, leaving it exposed to parameter and duplicate-content variants.",
                )


class DuplicateContentRule:
    rule_id = "technical.duplicate_content"
    version = RULESET_VERSION
    category = "technical"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        groups: dict[str, list[PageSnapshot]] = {}
        for page in context.pages:
            if _is_success(page) and page.body_sha256:
                groups.setdefault(page.body_sha256, []).append(page)
        duplicates = [pages for pages in groups.values() if len(pages) >= 2]
        if not duplicates:
            return
        affected = [page for pages in duplicates for page in pages]
        yield _site_finding(
            context,
            affected,
            category=self.category,
            rule_id=self.rule_id,
            severity=Severity.MEDIUM,
            title="Duplicate page content detected",
            description=f"{len(affected)} crawled pages across {len(duplicates)} groups returned byte-identical bodies; duplicates split ranking signals between URLs.",
        )


class InsecureInternalLinksRule:
    rule_id = "technical.insecure_internal_links"
    version = RULESET_VERSION
    category = "technical"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            insecure = [link for link in page.links if link.startswith("http://")]
            if insecure:
                yield _finding(
                    context,
                    page,
                    category=self.category,
                    rule_id=self.rule_id,
                    severity=Severity.MEDIUM,
                    title="Insecure internal links",
                    description=f"The page contains {len(insecure)} internal links using the http:// scheme instead of https://.",
                )


class BrokenInternalLinkRule:
    rule_id = "technical.broken_internal_link"
    version = RULESET_VERSION
    category = "technical"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        status_by_url = {page.normalized_url: page for page in context.pages}
        pairs: list[tuple[PageSnapshot, PageSnapshot]] = []
        for page in context.pages:
            for link in page.links:
                target = status_by_url.get(link)
                if target is not None and target.status_code is not None and target.status_code >= 400:
                    pairs.append((page, target))
        if not pairs:
            return
        listing = "; ".join(
            f"{source.normalized_url} -> {target.normalized_url} (HTTP {target.status_code})"
            for source, target in pairs[:10]
        )
        affected = [page for pair in pairs for page in pair]
        yield _site_finding(
            context,
            affected,
            category=self.category,
            rule_id=self.rule_id,
            severity=Severity.HIGH,
            title="Broken internal links",
            description=(
                f"{len(pairs)} internal link{'s point' if len(pairs) != 1 else ' points'} "
                f"at URLs that returned an error status in this crawl: {listing}."
            ),
            affected_count=len({target.normalized_url for _, target in pairs}),
        )


class URLHygieneRule:
    rule_id = "technical.url_hygiene"
    version = RULESET_VERSION
    category = "technical"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            path = _path_of(page)
            problems = []
            if any(char.isupper() for char in path):
                problems.append("uppercase characters")
            if "_" in path:
                problems.append("underscores")
            if len(path) > 115:
                problems.append(f"a {len(path)}-character path")
            if problems:
                yield _finding(
                    context,
                    page,
                    category=self.category,
                    rule_id=self.rule_id,
                    severity=Severity.LOW,
                    title="URL formatting issues",
                    description=f"The URL path contains {', '.join(problems)}; lowercase hyphenated paths are easier to maintain and share.",
                )


class DeepPageRule:
    rule_id = "technical.deep_page"
    version = RULESET_VERSION
    category = "technical"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            if page.url_depth > 4:
                yield _finding(
                    context,
                    page,
                    category=self.category,
                    rule_id=self.rule_id,
                    severity=Severity.LOW,
                    title="Page buried deep in the site structure",
                    description=f"The URL sits {page.url_depth} path segments deep; pages beyond four levels are crawled and discovered less often.",
                )


class OrphanPageRule:
    rule_id = "technical.orphan_page"
    version = RULESET_VERSION
    category = "technical"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        linked: set[str] = set()
        for page in context.pages:
            linked.update(link for link in page.links if link != page.normalized_url)
        orphans = [
            page
            for page in context.pages
            if _is_success_html(page)
            and not _is_homepage(page)
            and page.normalized_url not in linked
        ]
        if not orphans:
            return
        yield _site_finding(
            context,
            orphans,
            category=self.category,
            rule_id=self.rule_id,
            severity=Severity.MEDIUM,
            title="Orphaned pages with no internal links",
            description=f"{len(orphans)} crawled pages receive no internal links from any other crawled page, so they depend entirely on external discovery.",
        )


class _DuplicateFieldRule:
    """Shared implementation for site-wide duplicate text-field rules."""

    rule_id = ""
    version = RULESET_VERSION
    category = "on_page"
    severity = Severity.MEDIUM
    field_label = ""

    def _value(self, page: PageSnapshot) -> str | None:
        raise NotImplementedError

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        groups: dict[str, list[PageSnapshot]] = {}
        for page in context.pages:
            if not _is_success(page):
                continue
            value = self._value(page)
            if value:
                groups.setdefault(value, []).append(page)
        duplicates = [pages for pages in groups.values() if len(pages) >= 2]
        if not duplicates:
            return
        affected = [page for pages in duplicates for page in pages]
        yield _site_finding(
            context,
            affected,
            category=self.category,
            rule_id=self.rule_id,
            severity=self.severity,
            title=f"Duplicate {self.field_label} across pages",
            description=(
                f"{len(affected)} pages share an identical {self.field_label} across "
                f"{len(duplicates)} group{'s' if len(duplicates) != 1 else ''}; "
                "each page should describe its own topic."
            ),
        )


class TitleDuplicateRule(_DuplicateFieldRule):
    rule_id = "on_page.title_duplicate"
    field_label = "title"

    def _value(self, page: PageSnapshot) -> str | None:
        return (page.title or "").strip().casefold() or None


class MetaDescriptionDuplicateRule(_DuplicateFieldRule):
    rule_id = "on_page.meta_description_duplicate"
    field_label = "meta description"

    def _value(self, page: PageSnapshot) -> str | None:
        return (page.meta_description or "").strip().casefold() or None


class H1DuplicateRule(_DuplicateFieldRule):
    rule_id = "on_page.h1_duplicate"
    field_label = "H1 heading"
    severity = Severity.LOW

    def _value(self, page: PageSnapshot) -> str | None:
        return _first_h1(page)


class TitleShortRule:
    rule_id = "on_page.title_short"
    version = RULESET_VERSION
    category = "on_page"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            if not _is_success(page):
                continue
            title = (page.title or "").strip()
            if title and len(title) < 15:
                yield _finding(
                    context,
                    page,
                    category=self.category,
                    rule_id=self.rule_id,
                    severity=Severity.LOW,
                    title="Very short page title",
                    description=f"The title is only {len(title)} characters; titles under 15 characters rarely describe the page topic.",
                )


class ThinContentRule:
    rule_id = "on_page.thin_content"
    version = RULESET_VERSION
    category = "on_page"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            if _is_quarantined(context, page):
                continue
            if not _is_success_html(page) or _is_utility_page(page):
                continue
            if page.word_count is not None and page.word_count < 200:
                yield _finding(
                    context,
                    page,
                    category=self.category,
                    rule_id=self.rule_id,
                    severity=Severity.MEDIUM,
                    title="Thin page content",
                    description=f"Only {page.word_count} words of visible text were observed; thin pages struggle to satisfy any search intent.",
                )


class ImageAltMissingRule:
    rule_id = "on_page.image_alt_missing"
    version = RULESET_VERSION
    category = "on_page"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            if _is_success_html(page) and page.images_missing_alt > 0:
                yield _finding(
                    context,
                    page,
                    category=self.category,
                    rule_id=self.rule_id,
                    severity=Severity.LOW,
                    title="Images missing alt text",
                    description=f"{page.images_missing_alt} of {page.images_total} images on the page have no alt attribute text.",
                )


class H2MissingRule:
    rule_id = "on_page.h2_missing"
    version = RULESET_VERSION
    category = "on_page"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            if not _is_success_html(page):
                continue
            if page.word_count is not None and page.word_count > 600 and not page.h2:
                yield _finding(
                    context,
                    page,
                    category=self.category,
                    rule_id=self.rule_id,
                    severity=Severity.LOW,
                    title="Long page without H2 subheadings",
                    description=f"The page holds {page.word_count} words but no H2 subheadings, making it harder to scan and to earn featured snippets.",
                )


class SlowResponseRule:
    rule_id = "performance.slow_response"
    version = RULESET_VERSION
    category = "performance"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            if page.response_ms is None:
                continue
            if page.response_ms > 2500:
                severity = Severity.HIGH
            elif page.response_ms > 1200:
                severity = Severity.MEDIUM
            else:
                continue
            yield _finding(
                context,
                page,
                category=self.category,
                rule_id=self.rule_id,
                severity=severity,
                title="Slow server response",
                description=f"The HTML document took {page.response_ms} ms to download; responses above 1200 ms hold back every downstream rendering metric.",
            )


class HeavyPageRule:
    rule_id = "performance.heavy_page"
    version = RULESET_VERSION
    category = "performance"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            if page.body_bytes > 1_500_000:
                yield _finding(
                    context,
                    page,
                    category=self.category,
                    rule_id=self.rule_id,
                    severity=Severity.LOW,
                    title="Heavy document payload",
                    description=f"The response body is {page.body_bytes:,} bytes; documents above 1.5 MB slow first render on constrained connections.",
                )


class TrackingCoverageRule:
    rule_id = "analytics.tracking_missing"
    version = RULESET_VERSION
    category = "analytics"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        html_pages = [page for page in context.pages if _is_success_html(page)]
        if not html_pages:
            return
        untracked = [page for page in html_pages if not page.analytics_tags]
        share = len(untracked) / len(html_pages)
        if share >= 0.8:
            yield _site_finding(
                context,
                untracked,
                category=self.category,
                rule_id="analytics.tracking_missing",
                severity=Severity.HIGH,
                title="Analytics tracking is absent site-wide",
                description=f"{len(untracked)} of {len(html_pages)} crawled HTML pages ({share:.0%}) show no recognised analytics tag, so performance cannot be measured.",
            )
        elif share >= 0.2:
            yield _site_finding(
                context,
                untracked,
                category=self.category,
                rule_id="analytics.tracking_partial",
                severity=Severity.MEDIUM,
                title="Analytics tracking has coverage gaps",
                description=f"{len(untracked)} of {len(html_pages)} crawled HTML pages ({share:.0%}) show no recognised analytics tag; partial coverage skews every reported metric.",
            )


class StructuredDataMissingRule:
    rule_id = "geo_aeo.structured_data_missing"
    version = RULESET_VERSION
    category = "geo_aeo"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        html_pages = [page for page in context.pages if _is_success_html(page)]
        if not html_pages:
            return
        missing = [page for page in html_pages if not page.schema_types]
        share = len(missing) / len(html_pages)
        if share >= 0.5:
            yield _site_finding(
                context,
                missing,
                category=self.category,
                rule_id=self.rule_id,
                severity=Severity.MEDIUM,
                title="Structured data missing on most pages",
                description=f"{len(missing)} of {len(html_pages)} crawled HTML pages ({share:.0%}) expose no structured data, limiting rich-result and AI-answer eligibility.",
            )


class OrganizationSchemaMissingRule:
    rule_id = "geo_aeo.organization_schema_missing"
    version = RULESET_VERSION
    category = "geo_aeo"
    required_types = frozenset({"organization", "localbusiness", "website"})

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        html_pages = [page for page in context.pages if _is_success_html(page)]
        if not html_pages:
            return
        for page in html_pages:
            if _schema_types_casefolded(page) & self.required_types:
                return
        representative = min(html_pages, key=lambda page: (page.url_depth, page.normalized_url))
        yield _site_finding(
            context,
            [representative],
            category=self.category,
            rule_id=self.rule_id,
            severity=Severity.MEDIUM,
            title="No Organization, WebSite or LocalBusiness schema",
            description="No crawled page declares Organization, WebSite or LocalBusiness structured data, so search engines and answer engines cannot verify the entity.",
        )


class HTMLLangMissingRule:
    rule_id = "geo_aeo.html_lang_missing"
    version = RULESET_VERSION
    category = "geo_aeo"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        missing = [
            page
            for page in context.pages
            if _is_success_html(page) and not (page.lang or "").strip()
        ]
        if not missing:
            return
        yield _site_finding(
            context,
            missing,
            category=self.category,
            rule_id=self.rule_id,
            severity=Severity.LOW,
            title="Pages without an html lang attribute",
            description=f"{len(missing)} crawled HTML pages do not declare a document language, which weakens locale targeting and accessibility.",
        )


class TitleCannibalizationRule:
    rule_id = "keyword_architecture.title_cannibalization"
    version = RULESET_VERSION
    category = "keyword_architecture"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        candidates = [
            (page, _title_tokens(page.title))
            for page in context.pages
            if _is_success(page) and page.title and page.title.strip()
        ]
        candidates = [(page, tokens) for page, tokens in candidates if tokens]
        parents = list(range(len(candidates)))

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        for left in range(len(candidates)):
            for right in range(left + 1, len(candidates)):
                tokens_a, tokens_b = candidates[left][1], candidates[right][1]
                jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
                if jaccard >= 0.8:
                    parents[find(right)] = find(left)
        clusters: dict[int, list[PageSnapshot]] = {}
        for index, (page, _) in enumerate(candidates):
            clusters.setdefault(find(index), []).append(page)
        overlapping = [pages for pages in clusters.values() if len(pages) >= 2]
        if not overlapping:
            return
        listing = " | ".join(
            "cluster: " + ", ".join(page.normalized_url for page in pages[:5])
            for pages in overlapping[:5]
        )
        affected = [page for pages in overlapping for page in pages]
        yield _site_finding(
            context,
            affected,
            category=self.category,
            rule_id=self.rule_id,
            severity=Severity.MEDIUM,
            title="Pages competing for the same title keywords",
            description=f"{len(affected)} pages across {len(overlapping)} clusters share nearly identical title keyword sets and may cannibalise each other. {listing}.",
        )


class GenericTitleRule:
    rule_id = "keyword_architecture.generic_title"
    version = RULESET_VERSION
    category = "keyword_architecture"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        for page in context.pages:
            if not _is_success(page):
                continue
            title = (page.title or "").strip()
            if title and len(title.split()) <= 2:
                yield _finding(
                    context,
                    page,
                    category=self.category,
                    rule_id=self.rule_id,
                    severity=Severity.LOW,
                    title="Generic brand-only title",
                    description=f'The title "{title}" appears to be just the site or brand name and targets no page-specific topic.',
                )


class ProductSchemaMissingRule:
    rule_id = "ecommerce.product_schema_missing"
    version = RULESET_VERSION
    category = "ecommerce"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        product_pages = [
            page
            for page in context.pages
            if _is_success_html(page) and "/product" in _path_of(page).casefold()
        ]
        if not product_pages:
            return
        lacking = [
            page for page in product_pages if "product" not in _schema_types_casefolded(page)
        ]
        if len(lacking) / len(product_pages) >= 0.5:
            yield _site_finding(
                context,
                lacking,
                category=self.category,
                rule_id=self.rule_id,
                severity=Severity.HIGH,
                title="Product pages without Product schema",
                description=f"{len(lacking)} of {len(product_pages)} product-path pages expose no Product structured data, forfeiting price and availability rich results.",
            )


class LocalBusinessSchemaMissingRule:
    rule_id = "local.localbusiness_schema_missing"
    version = RULESET_VERSION
    category = "local"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        html_pages = [page for page in context.pages if _is_success_html(page)]
        if not html_pages:
            return
        for page in html_pages:
            if "localbusiness" in _schema_types_casefolded(page):
                return
        representative = min(html_pages, key=lambda page: (page.url_depth, page.normalized_url))
        yield _site_finding(
            context,
            [representative],
            category=self.category,
            rule_id=self.rule_id,
            severity=Severity.MEDIUM,
            title="No LocalBusiness schema found",
            description="No crawled page declares LocalBusiness structured data, so local pack and knowledge panel signals cannot be reinforced from the website.",
        )


class CrawlDegradationRule:
    """Report our own blocked crawl as a coverage caveat, never as a site defect.

    The finding is INFO severity and carries no page evidence on purpose: the
    quarantined responses are the protection layer's, not the client's pages, so
    they must not be cited as evidence of anything about the site itself.
    """

    rule_id = "technical.crawl_degraded"
    version = RULESET_VERSION
    category = "technical"

    def evaluate(self, context: AuditContext) -> Iterable[Finding]:
        integrity = context.crawl_integrity
        if integrity is None or integrity.status == "clean":
            return
        share = integrity.challenge_share
        quarantined = tuple(integrity.quarantined_urls)[:_AFFECTED_URL_CAP]
        listing = f" Examples: {', '.join(quarantined[:5])}." if quarantined else ""
        yield Finding(
            id=str(uuid4()),
            project_id=context.project_id,
            category=self.category,
            rule_id=self.rule_id,
            rule_version=RULESET_VERSION,
            severity=Severity.INFO,
            title="Crawl coverage was reduced by bot protection",
            description=(
                f"{integrity.challenged_pages} of "
                f"{integrity.fetched_pages + integrity.challenged_pages} requested URLs "
                f"({share:.0%}) returned a bot-challenge or rate-limit response "
                f"({integrity.rate_limited_pages} were explicit rate limits), so crawl "
                f"status is '{integrity.status}'. Those URLs were quarantined and produced "
                "no findings; the audit describes only the pages we could actually read."
                f"{listing}"
            ),
            evidence_ids=(),
            affected_urls=quarantined,
            affected_share=min(1.0, share),
            confidence=1.0,
            risk=RiskClass.LOW,
        )


DEFAULT_RULES: tuple[Rule, ...] = (
    HTTPStatusRule(),
    TitleRule(),
    MetaDescriptionRule(),
    H1Rule(),
    CanonicalBoundaryRule(),
    RobotsDirectiveRule(),
    RedirectChainLengthRule(),
    CanonicalMissingRule(),
    DuplicateContentRule(),
    InsecureInternalLinksRule(),
    BrokenInternalLinkRule(),
    URLHygieneRule(),
    DeepPageRule(),
    OrphanPageRule(),
    TitleDuplicateRule(),
    MetaDescriptionDuplicateRule(),
    H1DuplicateRule(),
    TitleShortRule(),
    ThinContentRule(),
    ImageAltMissingRule(),
    H2MissingRule(),
    SlowResponseRule(),
    HeavyPageRule(),
    TrackingCoverageRule(),
    StructuredDataMissingRule(),
    OrganizationSchemaMissingRule(),
    HTMLLangMissingRule(),
    TitleCannibalizationRule(),
    GenericTitleRule(),
    ProductSchemaMissingRule(),
    LocalBusinessSchemaMissingRule(),
    CrawlDegradationRule(),
)


def run_rules(context: AuditContext, rules: Iterable[Rule] = DEFAULT_RULES) -> tuple[Finding, ...]:
    active = set(enabled_modules(context.business_profile))
    # Quarantined (bot-challenged) URLs are removed once, here, so no rule can
    # accidentally turn a challenge interstitial into a client-facing defect and
    # no share denominator counts pages we were never allowed to read.
    evaluation_context = context
    if context.challenged_urls:
        evaluation_context = replace(context, pages=context.evaluable_pages())
    findings: list[Finding] = []
    for rule in rules:
        if rule.category in active:
            findings.extend(rule.evaluate(evaluation_context))
    return tuple(findings)
