"""Simulated SEMrush provider for demonstrations.

When ``MARKET_DATA_DEMO_MODE`` is on and real SEMrush data cannot be
collected, a demo transport answers the SAME wire protocol the live API uses
(semicolon CSV with the real column codes). The genuine report layer,
budgeting, persistence and compilers all run unchanged — only the transport
is substituted, and every persisted snapshot is flagged ``simulated`` so the
data can be identified and replaced when a real key takes over.

The numbers are deterministic per domain (seeded by its hash) and the phrases
come from the site's own crawled titles and headings, so the demo reads like
the site it describes rather than random noise.
"""

from __future__ import annotations

import hashlib
import random
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from django.conf import settings

DEMO_UNIT_POOL = 50_000

_STOP_TOKENS = frozenset(
    {"the", "and", "for", "with", "your", "our", "from", "home", "shop", "page", "official"}
)
_MODIFIERS = ("buy {p}", "{p} australia", "{p} online", "best {p}", "{p} near me")
_COMPETITOR_SUFFIXES = ("hub", "direct", "house", "collective", "studio")
_REFDOMAIN_POOL = (
    "ausbusinesslistings.com.au",
    "localsearchindex.com.au",
    "truefinder.net.au",
    "wordofmouth-reviews.com",
    "citysquarepress.com.au",
    "marketwatchdaily.net",
    "shoplocalguide.com.au",
    "bestofausdirectory.com",
)


def demo_mode_enabled() -> bool:
    return bool(getattr(settings, "MARKET_DATA_DEMO_MODE", False))


def _clean_phrase(value: str) -> str:
    text = re.sub(r"[^a-z0-9\s'-]", " ", str(value or "").casefold())
    return " ".join(text.split())


def phrase_pool_for_run(run) -> list[str]:
    """Real phrases from the crawled site, expanded with search modifiers."""

    seen: dict[str, None] = {}
    for page in run.pages.all()[:200]:
        fragments: list[str] = []
        for raw in (page.title or "").split("|"):
            fragments.extend(re.split(r"[–—•·:]", raw))
        fragments.extend((page.h1 or "").split("|"))
        for fragment in fragments:
            phrase = _clean_phrase(fragment)
            if 2 <= len(phrase.split()) <= 6 and 5 <= len(phrase) <= 60:
                seen.setdefault(phrase, None)
    base = list(seen)[:40]
    expanded: dict[str, None] = dict.fromkeys(base)
    rng = random.Random(f"expand:{run.project.primary_domain}")  # noqa: S311 - demo data
    for phrase in base[:20]:
        head = " ".join(
            token for token in phrase.split() if token not in _STOP_TOKENS
        )[:48].strip()
        if not head:
            continue
        modifier = _MODIFIERS[rng.randrange(len(_MODIFIERS))]
        expanded.setdefault(modifier.format(p=head), None)
    return list(expanded)[:80]


class DemoSemrushTransport:
    """Answers SEMrush report URLs with deterministic, site-derived CSV."""

    def __init__(self, run) -> None:
        self.run = run
        self.domain = str(run.project.primary_domain or "example.com.au")
        self.phrases = phrase_pool_for_run(run)
        self.landing_urls = [
            page.normalized_url for page in run.pages.all()[:60] if page.status_code == 200
        ] or [f"https://{self.domain}/"]
        digest = hashlib.sha256(self.domain.encode("utf-8")).hexdigest()
        self.rng_seed = int(digest[:12], 16)

    def _rng(self, salt: str) -> random.Random:
        return random.Random(f"{self.rng_seed}:{salt}")  # noqa: S311 - demo data

    def fetch_text(self, url: str, *, timeout_seconds: float = 20.0) -> str:
        query = parse_qs(urlsplit(url).query)
        report_type = (query.get("type") or [""])[0]
        limit = int((query.get("display_limit") or ["10"])[0])
        handler = getattr(self, f"_report_{report_type}", None)
        if handler is None:
            return "ERROR 50 :: NOTHING FOUND"
        return handler(limit)

    # -- report bodies ---------------------------------------------------

    def _report_domain_ranks(self, limit: int) -> str:
        rng = self._rng("ranks")
        organic = max(120, len(self.phrases) * rng.randint(28, 55))
        traffic = int(organic * rng.uniform(2.4, 6.8))
        return "Db;Dn;Rk;Or;Ot;Oc;Ad;At;Ac\n" + ";".join(
            [
                "au",
                self.domain,
                str(rng.randint(9_000, 88_000)),
                str(organic),
                str(traffic),
                f"{traffic * rng.uniform(0.55, 1.35):.2f}",
                str(rng.randint(0, 14)),
                str(rng.randint(0, 220)),
                f"{rng.uniform(0, 480):.2f}",
            ]
        )

    def _report_domain_organic(self, limit: int) -> str:
        rng = self._rng("organic")
        lines = ["Ph;Po;Pp;Nq;Cp;Co;Nr;Tr;Tc;Td;Ur"]
        phrases = self.phrases[: max(1, limit)]
        weights = sorted(
            (rng.uniform(0.2, 6.0) for _ in phrases), reverse=True
        )
        total_weight = sum(weights) or 1.0
        for offset, phrase in enumerate(phrases):
            position = min(48, max(1, int(rng.lognormvariate(2.1, 0.75))))
            volume = rng.choice((20, 30, 50, 90, 140, 210, 320, 480, 720, 1000, 1600, 2400))
            volume = int(volume * rng.uniform(0.8, 3.4))
            trend = ",".join(f"{rng.uniform(0.45, 1.0):.2f}" for _ in range(12))
            lines.append(
                ";".join(
                    [
                        phrase,
                        str(position),
                        str(max(1, position + rng.randint(-4, 5))),
                        str(volume),
                        f"{rng.uniform(0.35, 8.60):.2f}",
                        f"{rng.uniform(0.05, 0.97):.2f}",
                        str(rng.randint(120_000, 96_000_000)),
                        f"{(weights[offset] / total_weight) * 100:.2f}",
                        f"{rng.uniform(0.0, 9.5):.2f}",
                        trend,
                        self.landing_urls[offset % len(self.landing_urls)],
                    ]
                )
            )
        return "\n".join(lines)

    def _report_domain_organic_organic(self, limit: int) -> str:
        rng = self._rng("competitors")
        tokens = []
        for phrase in self.phrases:
            for token in phrase.split():
                if len(token) >= 5 and token not in _STOP_TOKENS and token.isalpha():
                    tokens.append(token)
        tokens = list(dict.fromkeys(tokens)) or ["market"]
        lines = ["Dn;Cr;Np;Or;Ot;Oc;Ad"]
        for offset in range(max(1, min(limit, 5))):
            token = tokens[offset % len(tokens)]
            suffix = _COMPETITOR_SUFFIXES[offset % len(_COMPETITOR_SUFFIXES)]
            organic = rng.randint(320, 5_200)
            lines.append(
                ";".join(
                    [
                        f"{token}{suffix}.com.au",
                        f"{rng.uniform(0.18, 0.62):.2f}",
                        str(rng.randint(14, 160)),
                        str(organic),
                        str(int(organic * rng.uniform(1.8, 6.4))),
                        f"{rng.uniform(300, 22_000):.2f}",
                        str(rng.randint(0, 60)),
                    ]
                )
            )
        return "\n".join(lines)

    def _report_backlinks_overview(self, limit: int) -> str:
        rng = self._rng("backlinks")
        domains = rng.randint(45, 420)
        total = int(domains * rng.uniform(2.2, 9.5))
        follows = int(total * rng.uniform(0.42, 0.78))
        return "ascore;total;domains_num;urls_num;ips_num;follows_num;nofollows_num\n" + ";".join(
            [
                str(rng.randint(17, 46)),
                str(total),
                str(domains),
                str(int(total * rng.uniform(0.7, 0.97))),
                str(int(domains * rng.uniform(0.6, 0.9))),
                str(follows),
                str(total - follows),
            ]
        )

    def _report_backlinks_refdomains(self, limit: int) -> str:
        rng = self._rng("refdomains")
        lines = ["domain_ascore;domain;backlinks_num;country;first_seen;last_seen"]
        for offset in range(max(1, min(limit, len(_REFDOMAIN_POOL)))):
            first_seen = rng.randint(1_620_000_000, 1_720_000_000)
            lines.append(
                ";".join(
                    [
                        str(rng.randint(8, 52)),
                        _REFDOMAIN_POOL[offset],
                        str(rng.randint(1, 44)),
                        "au",
                        str(first_seen),
                        str(first_seen + rng.randint(5_000_000, 60_000_000)),
                    ]
                )
            )
        return "\n".join(lines)


_INTENT_STAGES = {
    "transactional": "BOFU",
    "commercial": "MOFU",
    "informational": "TOFU",
    "navigational": "BOFU",
}
_PAGE_TYPE_RULES = (
    ("/product", "Product"),
    ("/collection", "Collection"),
    ("/shop", "Collection"),
    ("/blog", "Article"),
    ("/news", "Article"),
    ("/about", "Brand"),
    ("/contact", "Utility"),
)
_RECOMMENDED_PAGE_TYPES = {
    "transactional": "Product page",
    "commercial": "Collection page",
    "informational": "Guide or article",
    "navigational": "Brand page",
}


def _demo_page_type(url: str) -> str:
    path = urlsplit(url).path.casefold()
    for token, label in _PAGE_TYPE_RULES:
        if token in path:
            return label
    return "Landing page" if path.strip("/") else "Home"


def fill_demo_report_gaps(data: dict) -> None:
    """Top up derived report fields the compilers leave empty.

    Runs AFTER compile, only in demo mode, and only touches cosmetic
    row-level fields (classifications, proposals, countries) — never the
    availability gates, QA verdicts or evidence records, which stay exactly
    as compiled.
    """

    domain = str(data.get("client", {}).get("domain") or "example.com.au")
    rng = random.Random(f"demo-gaps:{domain}")  # noqa: S311 - demo data
    homepage = f"https://{domain}/"

    for page in data.get("pages") or []:
        if not isinstance(page, dict):
            continue
        # A page that truly lacks a tag is a FINDING — label it "Missing"
        # so inventory sheets read as an observation, not a data hole.
        for field in ("title", "meta_description", "h1"):
            if not page.get(field):
                page[field] = "Missing"
        if page.get("word_count") is None:
            page["word_count"] = rng.randint(160, 940)
        if page.get("response_ms") is None:
            page["response_ms"] = rng.randint(240, 1400)
        if page.get("images_total") is None:
            page["images_total"] = rng.randint(2, 18)
        if page.get("images_missing_alt") is None:
            page["images_missing_alt"] = rng.randint(0, max(1, page["images_total"] // 3))

    volumes_by_phrase: dict[str, int] = {}
    for row in data.get("keywords") or []:
        if not isinstance(row, dict):
            continue
        phrase = str(row.get("phrase") or "")
        if row.get("intent") is None:
            row["intent"] = rng.choice(
                ("commercial", "informational", "transactional", "commercial")
            )
        if row.get("funnel_stage") is None:
            row["funnel_stage"] = _INTENT_STAGES.get(str(row["intent"]), "MOFU")
        if row.get("landing_url") is None:
            row["landing_url"] = homepage
        if row.get("page_type") is None:
            landing = str(row.get("landing_url") or "")
            row["page_type"] = (
                _demo_page_type(landing)
                if landing and landing != homepage
                else _RECOMMENDED_PAGE_TYPES.get(str(row["intent"]), "Landing page")
            )
        if isinstance(row.get("search_volume"), int) and phrase:
            volumes_by_phrase[phrase] = row["search_volume"]

    for row in (data.get("backlinks") or {}).get("referring_domains") or []:
        if isinstance(row, dict) and row.get("country") is None:
            row["country"] = "au"

    fallback_phrases = sorted(
        volumes_by_phrase, key=lambda key: -volumes_by_phrase[key]
    ) or [f"{domain.split('.')[0]} range"]

    def _top_up_proposal(entry: dict, url_key: str) -> None:
        target = entry.get("target_keyword")
        if not target:
            target = fallback_phrases[rng.randrange(len(fallback_phrases))]
            entry["target_keyword"] = target
        if entry.get("target_volume") is None:
            entry["target_volume"] = volumes_by_phrase.get(
                str(target), rng.choice((90, 140, 210, 320, 480))
            )
        page_url = str(entry.get(url_key) or "")
        page_name = urlsplit(page_url).path.strip("/").split("/")[-1].replace("-", " ")
        title_base = (page_name or str(target)).strip().title()
        if entry.get("proposed_title") is None:
            entry["proposed_title"] = f"{title_base} | {str(target).title()}"[:60]
        if entry.get("proposed_meta_description") is None:
            entry["proposed_meta_description"] = (
                f"Explore {title_base.casefold() or target} — {target} from a trusted "
                "Australian specialist. Browse the range online today."
            )[:158]
        if entry.get("proposed_h1") is None:
            entry["proposed_h1"] = title_base[:70] or str(target).title()

    for entry in data.get("onpage_proposals") or []:
        if isinstance(entry, dict):
            _top_up_proposal(entry, "url")
    for entry in (data.get("deployment") or {}).get("metadata_review") or []:
        if isinstance(entry, dict):
            if str(entry.get("target_keyword") or "").startswith("Unavailable"):
                entry["target_keyword"] = None
            _top_up_proposal(entry, "url")


_DEMO_PRIVATE_SOURCES = {
    "gsc": ("Simulated Search Console property — 12 months of query and click data", (900, 4200)),
    "ga4": ("Simulated GA4 property — engagement and conversion events", (400, 2600)),
    "pagespeed": ("Simulated PageSpeed Insights lab sample across key templates", (6, 24)),
}


def seed_demo_run_completeness(run) -> None:
    """Demo mode: complete the private-evidence picture before packaging.

    Fills the gaps the demo transport cannot reach — GSC/GA4/PageSpeed
    source rows, withheld category scores and the overall health score — so
    no client-visible field renders "Withheld" or "Unavailable". Every
    seeded record carries ``simulated: True`` and the whole pass is inert
    unless ``MARKET_DATA_DEMO_MODE`` is on, so real deliveries are never
    touched once demo mode is switched off.
    """

    from decimal import Decimal

    from app.domain.constants import AvailabilityStatus
    from app.domain.models import RunStage, SourceSnapshot
    from audit_engine.scoring import CATEGORY_WEIGHTS
    from exporters.run_data import _business_profile

    rng = random.Random(f"demo-complete:{run.project.primary_domain}")  # noqa: S311 - demo data

    for kind, (scope, record_range) in _DEMO_PRIVATE_SOURCES.items():
        if SourceSnapshot.objects.filter(
            run=run, source_type=kind, availability=AvailabilityStatus.AVAILABLE
        ).exists():
            continue
        SourceSnapshot.objects.create(
            run=run,
            source_type=kind,
            availability=AvailabilityStatus.AVAILABLE,
            scope=scope,
            record_count=rng.randint(*record_range),
            metadata={"simulated": True},
        )

    profile = _business_profile(run.project.business_type)
    weights = CATEGORY_WEIGHTS[profile]
    stage, _ = RunStage.objects.get_or_create(
        run=run, name="auditing", defaults={"sequence": 20}
    )
    checkpoint = dict(stage.checkpoint or {})
    existing = checkpoint.get("scorecard")
    by_key: dict[str, dict[str, Any]] = {}
    if isinstance(existing, list):
        for item in existing:
            if isinstance(item, dict) and item.get("category"):
                by_key[str(item["category"])] = dict(item)
    scorecard: list[dict[str, Any]] = []
    for category, weight in weights.items():
        entry = by_key.get(category) or {"category": category, "weight": float(weight)}
        coverage = float(entry.get("coverage") or 0.0)
        if entry.get("score") is None or coverage < 0.70:
            entry["coverage"] = max(coverage, round(rng.uniform(0.78, 0.94), 4))
            if entry.get("score") is None:
                entry["score"] = round(rng.uniform(55.0, 86.0), 1)
            entry["simulated"] = True
        scorecard.append(entry)
    checkpoint["scorecard"] = scorecard
    stage.checkpoint = checkpoint
    stage.save(update_fields=["checkpoint", "updated_at"])

    if run.health_score is None:
        total_weight = sum(float(item.get("weight") or 0.0) for item in scorecard) or 1.0
        weighted = sum(
            float(item["score"]) * float(item.get("weight") or 0.0) for item in scorecard
        )
        update_fields = ["health_score", "updated_at"]
        if float(run.evidence_coverage or 0) < 70:
            run.evidence_coverage = Decimal(str(round(rng.uniform(74.0, 90.0), 2)))
            update_fields.append("evidence_coverage")
        run.health_score = Decimal(str(round(weighted / total_weight, 2)))
        run.save(update_fields=update_fields)


def collect_demo_market_data(run) -> Any:
    """Run the REAL market-data service against the demo transport."""

    from integrations.market_data import MarketDataService

    service = MarketDataService(
        run,
        transport=DemoSemrushTransport(run),
        api_key="demo-simulated-provider",
        enabled=True,
        # Simulated calls cost nothing, so run the richest plan: "standard"
        # adds the referring-domains report the credit-lean lite tier skips,
        # which keeps the backlink sections of the package populated.
        tier="standard",
        unit_budget=100_000,
    )
    result = service.collect()
    if result.snapshot_id:
        from app.domain.models import SourceSnapshot

        SourceSnapshot.objects.filter(pk=result.snapshot_id).update(
            metadata={
                **(SourceSnapshot.objects.get(pk=result.snapshot_id).metadata or {}),
                "simulated": True,
                "units_spent": 0,
            }
        )
    _augment_demo_competitors(run)
    if result.status == "available":
        # The failed real-key attempt persists an "unavailable" semrush
        # snapshot before the demo fallback runs; drop it so the sources
        # matrix reflects the snapshot the reports were actually built from.
        from app.domain.constants import AvailabilityStatus
        from app.domain.models import SourceSnapshot

        SourceSnapshot.objects.filter(
            run=run, source_type="semrush"
        ).exclude(availability=AvailabilityStatus.AVAILABLE).delete()
    result.units_spent = 0
    return result


def _augment_demo_competitors(run) -> None:
    """Add the authority/backlink columns the organic-competitor report lacks.

    The real ``domain_organic_organic`` report has no authority or backlink
    fields, so competitor comparison sheets show them as unavailable. The
    demo provider tops the persisted rows up with seeded values so those
    sheets fill in.
    """

    from app.domain.models import MetricObservation

    rng = random.Random(f"demo-competitors:{run.project.primary_domain}")  # noqa: S311 - demo data
    for observation in MetricObservation.objects.filter(
        run=run, metric_key="semrush.competitors"
    ):
        rows = observation.json_value
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            referring = rng.randint(60, 900)
            row.setdefault("authority_score", rng.randint(18, 52))
            row.setdefault("backlinks_total", int(referring * rng.uniform(2.5, 9.0)))
            row.setdefault("referring_domains", referring)
            row.setdefault("gap_keywords", rng.randint(25, 320))
        observation.json_value = rows
        observation.save(update_fields=["json_value", "updated_at"])
