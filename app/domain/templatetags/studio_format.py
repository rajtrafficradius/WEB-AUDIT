"""Display-only formatting filters.

These never change stored values; they only make canonical machine keys and
ratios legible in the interface (``geo_aeo`` -> ``GEO / AEO``, ``1.0000`` ->
``100%``) so operators are not asked to read database vocabulary.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()

# Domain vocabulary that should not be naively title-cased.
LABEL_OVERRIDES = {
    "on_page": "On-page",
    "geo_aeo": "GEO / AEO",
    "cro": "CRO",
    "seo": "SEO",
    "qa": "QA",
    "keyword_architecture": "Keyword architecture",
    "ecommerce": "Ecommerce",
    "gsc": "Search Console",
    "ga4": "Analytics 4",
    "semrush": "SEMrush",
    "pagespeed": "PageSpeed",
    "url": "URL",
    "cms": "CMS",
    "kpi": "KPI",
}


@register.filter
def humanise(value: object) -> str:
    """Render a machine key as a readable label."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    key = raw.casefold()
    if key in LABEL_OVERRIDES:
        return LABEL_OVERRIDES[key]
    words = raw.replace("_", " ").replace("-", " ").split()
    if not words:
        return ""
    rendered = []
    for index, word in enumerate(words):
        lowered = word.casefold()
        if lowered in LABEL_OVERRIDES:
            rendered.append(LABEL_OVERRIDES[lowered])
        elif index == 0:
            rendered.append(word[:1].upper() + word[1:].casefold())
        else:
            rendered.append(word.casefold())
    return " ".join(rendered)


@register.filter
def as_percent(value: object, places: int = 0) -> str:
    """Render a 0..1 ratio (or an already-scaled 0..100 value) as a percentage."""

    if value is None or value == "":
        return "Unavailable"
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    if number <= 1:
        number *= 100
    quantised = round(number, int(places))
    text = f"{quantised:.{int(places)}f}"
    return f"{text}%"
