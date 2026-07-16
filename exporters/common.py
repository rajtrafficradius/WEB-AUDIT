"""Shared presentation data and brand tokens."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

INK = "#17201E"
PAPER = "#F6F2E9"
INDIGO = "#3E4C83"
COPPER = "#A15C38"
GREEN = "#2F6B57"
MUTED = "#66716D"
RULE = "#D8D4C9"
WHITE = "#FFFEFA"


@dataclass(frozen=True, slots=True)
class BrandFonts:
    display: str = "Fraunces"
    body: str = "Source Sans 3"


def font_paths(project_root: Path) -> tuple[Path | None, Path | None]:
    font_dir = project_root / "static" / "fonts"
    display = font_dir / "Fraunces.ttf"
    body = font_dir / "SourceSans3.ttf"
    return (display if display.exists() else None, body if body.exists() else None)


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

