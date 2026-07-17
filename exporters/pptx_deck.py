"""Executive deck renderer (python-pptx, 16:9) for the audit package.

Pure ``dict -> file``: consumes the compiled run-data dictionary and renders
``data['deck']`` slide specs onto blank layouts with explicit text boxes.
Brand: INK cover with WHITE display type and a COPPER eyebrow; PAPER content
slides with INK text and INDIGO accents. No template placeholders, no
external images, and no fabricated figures — score bars come straight from
``data['categories']`` and withheld scores render as withheld.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

from exporters.common import (
    COPPER,
    GREEN,
    INDIGO,
    INK,
    MUTED,
    PAPER,
    RULE,
    WHITE,
    BrandFonts,
    safe_text,
)

SLIDE_WIDTH_IN = 13.333
SLIDE_HEIGHT_IN = 7.5
MARGIN_IN = 0.6
CONTENT_WIDTH_IN = SLIDE_WIDTH_IN - 2 * MARGIN_IN

_FONTS = BrandFonts()


def _rgb(color: str) -> RGBColor:
    return RGBColor.from_string(color.lstrip("#"))


def _background(slide: Any, color: str) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = _rgb(color)


def _textbox(
    slide: Any,
    left: float,
    top: float,
    width: float,
    height: float,
    text: str,
    *,
    size: int,
    color: str,
    bold: bool = False,
    italic: bool = False,
    font: str | None = None,
    align: PP_ALIGN = PP_ALIGN.LEFT,
) -> Any:
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    frame = box.text_frame
    frame.word_wrap = True
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    run = paragraph.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = _rgb(color)
    run.font.name = font or _FONTS.body
    return box


def _rect(
    slide: Any,
    left: float,
    top: float,
    width: float,
    height: float,
    fill_color: str | None,
    *,
    line_color: str | None = None,
) -> Any:
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height)
    )
    shape.shadow.inherit = False
    if fill_color is None:
        shape.fill.background()
    else:
        shape.fill.solid()
        shape.fill.fore_color.rgb = _rgb(fill_color)
    if line_color is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = _rgb(line_color)
        shape.line.width = Pt(0.75)
    return shape


def _footer(slide: Any, data: dict[str, Any], index: int, total: int, *, dark: bool) -> None:
    client = safe_text(data.get("client", {}).get("name"), "Client")
    as_of = safe_text(data.get("run", {}).get("evidence_as_of"))
    text = (
        f"{client} · Enterprise SEO Audit · Evidence as of {as_of} · "
        f"slide {index}/{total}"
    )
    _textbox(
        slide, MARGIN_IN, SLIDE_HEIGHT_IN - 0.42, CONTENT_WIDTH_IN, 0.3, text,
        size=9, color=RULE if dark else MUTED,
    )


def _eyebrow(slide: Any, text: str, *, top: float = 0.55) -> None:
    _textbox(
        slide, MARGIN_IN, top, CONTENT_WIDTH_IN, 0.35, text.upper(),
        size=13, color=COPPER, bold=True,
    )


def _title(slide: Any, text: str, *, top: float = 0.95, color: str = INK,
           size: int = 34) -> None:
    _textbox(
        slide, MARGIN_IN, top, CONTENT_WIDTH_IN, 1.3, text,
        size=size, color=color, bold=True, font=_FONTS.display,
    )


def _point_cards(slide: Any, points: list[dict[str, Any]], *, top: float) -> None:
    cards = points[:4]
    if not cards:
        return
    gap = 0.35
    card_width = (CONTENT_WIDTH_IN - gap) / 2
    card_height = 1.45
    for index, point in enumerate(cards):
        column = index % 2
        row = index // 2
        left = MARGIN_IN + column * (card_width + gap)
        card_top = top + row * (card_height + 0.3)
        _rect(slide, left, card_top, card_width, card_height, WHITE, line_color=RULE)
        _textbox(
            slide, left + 0.25, card_top + 0.18, card_width - 0.5, 0.3,
            safe_text(point.get("label")), size=13, color=COPPER, bold=True,
        )
        _textbox(
            slide, left + 0.25, card_top + 0.55, card_width - 0.5, card_height - 0.7,
            safe_text(point.get("text")), size=13, color=INK,
        )


def _render_cover(slide: Any, spec: dict[str, Any], data: dict[str, Any]) -> None:
    _background(slide, INK)
    client = safe_text(data.get("client", {}).get("name"), "Client")
    as_of = safe_text(data.get("run", {}).get("evidence_as_of"))
    _textbox(
        slide, MARGIN_IN, 2.0, CONTENT_WIDTH_IN, 0.4,
        safe_text(spec.get("eyebrow"), "ENTERPRISE SEO REVIEW").upper(),
        size=14, color=COPPER, bold=True,
    )
    _textbox(
        slide, MARGIN_IN, 2.5, CONTENT_WIDTH_IN, 1.9,
        safe_text(spec.get("title"), "Enterprise SEO Audit"),
        size=40, color=WHITE, bold=True, font=_FONTS.display,
    )
    body = spec.get("body")
    if body:
        _textbox(slide, MARGIN_IN, 4.45, CONTENT_WIDTH_IN - 2.0, 1.1, str(body),
                 size=18, color=PAPER)
    _textbox(
        slide, MARGIN_IN, 5.85, CONTENT_WIDTH_IN, 0.4,
        f"{client} · Evidence as of {as_of}", size=14, color=RULE,
    )


def _render_score(slide: Any, spec: dict[str, Any], data: dict[str, Any]) -> None:
    categories = list(data.get("categories") or [])[:6]
    body = spec.get("body")
    if body:
        _textbox(slide, MARGIN_IN, 2.0, CONTENT_WIDTH_IN, 0.55, str(body),
                 size=16, color=INK)
    label_width = 2.7
    value_width = 1.0
    track_left = MARGIN_IN + label_width + value_width + 0.2
    track_width = SLIDE_WIDTH_IN - MARGIN_IN - track_left
    row_top = 2.75
    withheld_reason: str | None = None
    for category in categories:
        label = safe_text(category.get("category"))
        score = category.get("score")
        _textbox(slide, MARGIN_IN, row_top, label_width, 0.32, label,
                 size=14, color=INK, bold=True)
        if isinstance(score, int | float):
            _textbox(slide, MARGIN_IN + label_width, row_top, value_width, 0.32,
                     f"{score:.0f}", size=14, color=INDIGO, bold=True)
            _rect(slide, track_left, row_top + 0.03, track_width, 0.26, RULE)
            bar_width = max(0.05, track_width * min(max(float(score), 0.0), 100.0) / 100.0)
            _rect(slide, track_left, row_top + 0.03, bar_width, 0.26, INDIGO)
        else:
            chip = _rect(slide, track_left, row_top + 0.01, 1.35, 0.3, MUTED)
            frame = chip.text_frame
            frame.word_wrap = False
            run = frame.paragraphs[0].add_run()
            run.text = "Withheld"
            run.font.size = Pt(11)
            run.font.bold = True
            run.font.color.rgb = _rgb(WHITE)
            if withheld_reason is None:
                withheld_reason = (
                    category.get("unavailable_reason")
                    or data.get("run", {}).get("overall_score_reason")
                )
        row_top += 0.58
    if not categories:
        _textbox(
            slide, MARGIN_IN, 2.9, CONTENT_WIDTH_IN, 0.5,
            "Category scores are unavailable for this run.", size=16, color=MUTED,
        )
    if withheld_reason:
        _textbox(
            slide, MARGIN_IN, 6.5, CONTENT_WIDTH_IN, 0.4,
            f"Withheld: {withheld_reason}", size=11, color=MUTED, italic=True,
        )


def _render_generic(slide: Any, spec: dict[str, Any]) -> None:
    body = spec.get("body")
    if body:
        _textbox(slide, MARGIN_IN, 2.05, CONTENT_WIDTH_IN, 1.05, str(body),
                 size=17, color=INK)
    _point_cards(slide, list(spec.get("points") or []), top=3.3)
    callout = spec.get("callout")
    if callout:
        _rect(slide, MARGIN_IN, 6.35, CONTENT_WIDTH_IN, 0.55, PAPER, line_color=COPPER)
        _textbox(slide, MARGIN_IN + 0.2, 6.44, CONTENT_WIDTH_IN - 0.4, 0.4,
                 str(callout), size=13, color=COPPER, bold=True)


def _phase_summary(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    phases: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for action in actions:
        phase = safe_text(action.get("phase"), "Plan")
        if phase not in phases:
            phases[phase] = {"name": phase, "weeks": [], "actions": []}
            order.append(phase)
        entry = phases[phase]
        week = action.get("week")
        if isinstance(week, int):
            entry["weeks"].append(week)
            week_end = action.get("week_end")
            if isinstance(week_end, int):
                entry["weeks"].append(week_end)
        entry["actions"].append(safe_text(action.get("action")))
    return [phases[name] for name in order[:4]]


def _render_timeline(slide: Any, spec: dict[str, Any], data: dict[str, Any]) -> None:
    body = spec.get("body")
    if body:
        _textbox(slide, MARGIN_IN, 2.0, CONTENT_WIDTH_IN, 0.55, str(body),
                 size=16, color=INK)
    phases = _phase_summary(list(data.get("actions") or []))
    if not phases:
        _textbox(
            slide, MARGIN_IN, 3.0, CONTENT_WIDTH_IN, 0.5,
            "The 16-week action plan is unavailable for this run.", size=16, color=MUTED,
        )
        return
    accents = [INDIGO, GREEN, COPPER, MUTED]
    gap = 0.3
    block_width = (CONTENT_WIDTH_IN - gap * (len(phases) - 1)) / len(phases)
    top = 2.75
    for index, phase in enumerate(phases):
        left = MARGIN_IN + index * (block_width + gap)
        accent = accents[index % len(accents)]
        weeks = phase["weeks"]
        if weeks:
            first, last = min(weeks), max(weeks)
            week_label = f"Week {first}" if first == last else f"Weeks {first}–{last}"
        else:
            week_label = "Weeks unscheduled"
        band = _rect(slide, left, top, block_width, 0.85, accent)
        frame = band.text_frame
        frame.word_wrap = True
        run = frame.paragraphs[0].add_run()
        run.text = phase["name"]
        run.font.size = Pt(14)
        run.font.bold = True
        run.font.color.rgb = _rgb(WHITE)
        week_par = frame.add_paragraph()
        week_run = week_par.add_run()
        week_run.text = week_label
        week_run.font.size = Pt(11)
        week_run.font.color.rgb = _rgb(PAPER)
        _rect(slide, left, top + 0.85, block_width, 2.9, WHITE, line_color=RULE)
        item_top = top + 1.05
        for action_text in phase["actions"][:3]:
            _textbox(
                slide, left + 0.18, item_top, block_width - 0.36, 0.85,
                f"• {action_text}", size=11, color=INK,
            )
            item_top += 0.9


def _render_comparison(slide: Any, spec: dict[str, Any]) -> None:
    body = spec.get("body")
    if body:
        _textbox(slide, MARGIN_IN, 2.0, CONTENT_WIDTH_IN, 0.8, str(body),
                 size=16, color=INK)
    points = list(spec.get("points") or [])
    gap = 0.4
    column_width = (CONTENT_WIDTH_IN - gap) / 2
    split = (len(points) + 1) // 2
    columns = [points[:split], points[split:]]
    top = 3.0
    height = 3.6
    for index, column_points in enumerate(columns):
        left = MARGIN_IN + index * (column_width + gap)
        _rect(slide, left, top, column_width, height, WHITE, line_color=RULE)
        item_top = top + 0.25
        if not column_points:
            _textbox(slide, left + 0.25, item_top, column_width - 0.5, 0.4,
                     "No comparison points supplied.", size=12, color=MUTED, italic=True)
            continue
        for point in column_points[:4]:
            _textbox(slide, left + 0.25, item_top, column_width - 0.5, 0.3,
                     safe_text(point.get("label")), size=13, color=INDIGO, bold=True)
            _textbox(slide, left + 0.25, item_top + 0.32, column_width - 0.5, 0.5,
                     safe_text(point.get("text")), size=12, color=INK)
            item_top += 0.88


def render_deck(data: dict, output: Path) -> Path:
    """Render the executive deck from ``data['deck']`` slide specs."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    presentation = Presentation()
    presentation.slide_width = Inches(SLIDE_WIDTH_IN)
    presentation.slide_height = Inches(SLIDE_HEIGHT_IN)
    blank_layout = presentation.slide_layouts[6]

    slides = list(data.get("deck") or [])
    total = len(slides)
    for index, spec in enumerate(slides, start=1):
        slide = presentation.slides.add_slide(blank_layout)
        kind = str(spec.get("kind") or "generic").casefold()
        dark = kind == "cover"
        if dark:
            _render_cover(slide, spec, data)
        else:
            _background(slide, PAPER)
            _eyebrow(slide, safe_text(spec.get("eyebrow"), ""))
            _title(slide, safe_text(spec.get("title"), "Untitled section"))
            if kind == "score":
                _render_score(slide, spec, data)
            elif kind == "timeline":
                _render_timeline(slide, spec, data)
            elif kind == "comparison":
                _render_comparison(slide, spec)
            else:
                _render_generic(slide, spec)
        _footer(slide, data, index, total, dark=dark)

    presentation.save(str(output))
    return output
