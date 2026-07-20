"""Shared presentation helpers.

Brand tokens now live in :mod:`exporters.brand`; this module re-exports them
under their historical names so existing call sites keep working.
"""

from __future__ import annotations

from typing import Any

from exporters.brand import (
    ACCENT,
    ACCENT_LIGHT,
    AMBER,
    BLUE,
    BRAND_BLUE,
    BRAND_BLUE_DEEP,
    BRAND_DEEP,
    BRAND_GREEN,
    BRAND_GREEN_DEEP,
    BRAND_INK,
    CHART_SERIES,
    COPPER,
    CRITICAL,
    DEEP,
    GREEN,
    GREEN_RAMP,
    INDIGO,
    INK,
    ISSUE_FILL,
    MUTED,
    NEUTRAL,
    PAPER,
    POSITIVE,
    RED,
    RULE,
    SEVERITY_FILL,
    SURFACE,
    TYPE_RAMP,
    WARNING,
    WHITE,
    BrandFonts,
    argb,
    font_paths,
    hex_to_rgb,
    series_color,
    severity_tone,
)

__all__ = [
    "ACCENT",
    "ACCENT_LIGHT",
    "AMBER",
    "BLUE",
    "BRAND_BLUE",
    "BRAND_BLUE_DEEP",
    "BRAND_DEEP",
    "BRAND_GREEN",
    "BRAND_GREEN_DEEP",
    "BRAND_INK",
    "CHART_SERIES",
    "COPPER",
    "CRITICAL",
    "DEEP",
    "GREEN",
    "GREEN_RAMP",
    "INDIGO",
    "INK",
    "ISSUE_FILL",
    "MUTED",
    "NEUTRAL",
    "PAPER",
    "POSITIVE",
    "RED",
    "RULE",
    "SEVERITY_FILL",
    "SURFACE",
    "TYPE_RAMP",
    "WARNING",
    "WHITE",
    "BrandFonts",
    "argb",
    "coverage_label",
    "font_paths",
    "hex_to_rgb",
    "safe_text",
    "series_color",
    "severity_tone",
]


def coverage_label(value: float | None) -> str:
    if value is None:
        return "Unavailable"
    if value >= 0.9:
        return "Complete"
    if value >= 0.7:
        return "Sufficient"
    return "Limited"


def safe_text(value: Any, fallback: str = "Unavailable") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback
