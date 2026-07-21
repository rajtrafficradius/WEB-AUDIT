"""Single source of truth for Traffic Radius brand tokens.

Every renderer (XLSX, PPTX, PDF, DOCX, HTML) reads its palette, type ramp and
chart colours from this module so the deck, the report PDFs and the workbooks
cannot drift apart. ``exporters.common`` re-exports the legacy constant names
for backwards compatibility; new code should import from here.

The palette is derived from the Traffic Radius mark
(``static/img/traffic-radius-mark.svg``): a deep ink bar, a signal-blue bar and
a growth-green bar.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- core hues

BRAND_INK = "#2C3238"
"""Primary text ink — the mark's darkest bar, lightened for long-form reading."""

BRAND_DEEP = "#14171A"
"""Near-black used for cover fields and the deck's dark surfaces."""

BRAND_BLUE = "#35B0E3"
"""Signal blue — the mark's middle bar. Accents, chart series 1, highlights."""

BRAND_BLUE_DEEP = "#1B7CA8"
"""Darkened blue with a WCAG AA contrast ratio against white for table heads."""

BRAND_GREEN = "#8CC63F"
"""Growth green — the mark's tallest bar. Positive states, chart series 2."""

BRAND_GREEN_DEEP = "#5F8F1F"
"""Darkened green for text-on-light and positive severity fills."""

# --------------------------------------------------------------------------- ramps

NEUTRAL: dict[int, str] = {
    0: "#FFFFFF",
    50: "#F5F7F8",
    100: "#E9EDEF",
    200: "#D3DADE",
    300: "#B4BEC4",
    400: "#8C989F",
    500: "#6B767D",
    600: "#525C63",
    700: "#3C444A",
    800: "#2C3238",
    900: "#14171A",
}

AMBER: dict[int, str] = {
    100: "#FBEFD6",
    300: "#F0C978",
    500: "#E0A22F",
    700: "#A97417",
}

RED: dict[int, str] = {
    100: "#F9DEDC",
    300: "#E79A93",
    500: "#C8443C",
    700: "#932C26",
}

BLUE: dict[int, str] = {
    100: "#D9F1FB",
    300: "#7FCDEE",
    500: BRAND_BLUE,
    700: BRAND_BLUE_DEEP,
}

GREEN_RAMP: dict[int, str] = {
    100: "#E7F4D3",
    300: "#B7DC83",
    500: BRAND_GREEN,
    700: BRAND_GREEN_DEEP,
}

# --------------------------------------------------------------------------- semantics

INK = BRAND_INK
DEEP = BRAND_DEEP
PAPER = NEUTRAL[50]
WHITE = NEUTRAL[0]
SURFACE = NEUTRAL[0]
RULE = NEUTRAL[200]
MUTED = NEUTRAL[500]
ACCENT = BRAND_BLUE_DEEP
ACCENT_LIGHT = BRAND_BLUE
POSITIVE = BRAND_GREEN_DEEP
WARNING = AMBER[500]
CRITICAL = RED[500]

# Legacy aliases retained so older call sites keep compiling.
INDIGO = ACCENT
COPPER = CRITICAL
GREEN = POSITIVE

# ------------------------------------------------------------ executive dark theme

NAVY_900 = "#0F1B33"
"""Deck page background — the deepest navy of the executive theme."""

NAVY_700 = "#1B2A4A"
"""Card surface on the navy deck theme."""

NAVY_500 = "#24365C"
"""Elevated surface (callouts, alternate table rows) on the navy theme."""

GOLD = "#C5A059"
"""Executive gold accent — eyebrows, rules, chart series, stat values."""

GOLD_LIGHT = "#E2C286"
"""Lighter gold for large numerals and highlights on navy."""

TEXT_ON_NAVY = "#F2F5FA"
"""Primary text colour on navy surfaces."""

MUTED_ON_NAVY = "#8FA0BC"
"""Secondary/muted text colour on navy surfaces."""

RULE_ON_NAVY = "#31456B"
"""Hairlines and card borders on navy surfaces."""

# --------------------------------------------------------------------------- severity

SEVERITY_FILL: dict[str, tuple[str, str]] = {
    "critical": (RED[500], WHITE),
    "p1": (RED[500], WHITE),
    "high": (AMBER[700], WHITE),
    "p2": (AMBER[700], WHITE),
    "medium": (AMBER[300], INK),
    "p3": (AMBER[300], INK),
    "low": (NEUTRAL[300], INK),
    "p4": (NEUTRAL[300], INK),
    "info": (BLUE[100], INK),
    "positive": (GREEN_RAMP[300], INK),
}

ISSUE_FILL: dict[str, tuple[str, str]] = {
    "missing": (RED[500], WHITE),
    "too long": (AMBER[300], INK),
    "too short": (AMBER[300], INK),
    "multiple captured": (AMBER[300], INK),
    "mismatch": (AMBER[700], WHITE),
    "duplicate": (AMBER[700], WHITE),
    "review": (NEUTRAL[300], INK),
    "no change required": (GREEN_RAMP[300], INK),
    "ok": (GREEN_RAMP[300], INK),
}

# --------------------------------------------------------------------------- charts

CHART_SERIES: tuple[str, ...] = (
    BRAND_BLUE_DEEP,
    BRAND_GREEN_DEEP,
    AMBER[500],
    RED[500],
    NEUTRAL[500],
    BRAND_BLUE,
)

CHART_GRID = NEUTRAL[200]
CHART_LABEL = NEUTRAL[500]

# --------------------------------------------------------------------------- typography


@dataclass(frozen=True, slots=True)
class BrandFonts:
    """The brand type pair plus their OS fallback stacks."""

    display: str = "Fraunces"
    body: str = "Source Sans 3"
    display_stack: str = "Fraunces, Georgia, 'Times New Roman', serif"
    body_stack: str = "'Source Sans 3', 'Segoe UI', Helvetica, Arial, sans-serif"
    mono: str = "Consolas"


FONTS = BrandFonts()

TYPE_RAMP: dict[str, int] = {
    "display": 40,
    "title": 32,
    "section": 22,
    "lead": 17,
    "body": 13,
    "small": 11,
    "caption": 9,
    "micro": 8,
}


def font_paths(project_root: Path) -> tuple[Path | None, Path | None]:
    """Return the (display, body) TTF paths when they ship with the project."""
    font_dir = Path(project_root) / "static" / "fonts"
    display = font_dir / "Fraunces.ttf"
    body = font_dir / "SourceSans3.ttf"
    return (display if display.exists() else None, body if body.exists() else None)


# --------------------------------------------------------------------------- helpers


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    """Convert ``#RRGGBB`` to an ``(r, g, b)`` tuple."""
    raw = value.lstrip("#")
    return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))


def argb(value: str) -> str:
    """Convert ``#RRGGBB`` to the fully opaque ``FFRRGGBB`` openpyxl form."""
    return "FF" + value.lstrip("#").upper()


def series_color(index: int) -> str:
    """Return a deterministic chart series colour for ``index``."""
    return CHART_SERIES[index % len(CHART_SERIES)]


def severity_tone(value: object) -> tuple[str, str] | None:
    """Return the (fill, text) pair for a severity or priority token."""
    return SEVERITY_FILL.get(str(value or "").strip().casefold())
