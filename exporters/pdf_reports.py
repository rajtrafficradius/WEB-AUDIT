"""ReportLab renderers for client-facing PDF artifacts."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    LongTable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from .common import COPPER, INDIGO, INK, MUTED, PAPER, RULE, WHITE, font_paths, safe_text

_ReportLabParagraph = Paragraph


def Paragraph(text: Any, style: ParagraphStyle) -> Any:  # noqa: N802
    """Create a ReportLab paragraph without interpreting evidence text as XML markup."""
    return _ReportLabParagraph(xml_escape(str(text)), style)


def _c(value: str) -> colors.Color:
    return HexColor(value)


def _pdf_display(value: Any) -> str:
    """Render a measured value, or state plainly that it was not measured."""

    if value is None or value == "":
        return "Unavailable"
    return str(value)


class PDFReportBuilder:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.display_font = "Helvetica-Bold"
        self.body_font = "Helvetica"
        display, body = font_paths(project_root)
        registered = set(pdfmetrics.getRegisteredFontNames())
        # Register each brand face independently: shipping one of the two is no
        # reason to silently fall back to Helvetica for both.
        if display is not None:
            if "Fraunces" not in registered:
                pdfmetrics.registerFont(TTFont("Fraunces", str(display)))
            self.display_font = "Fraunces"
        if body is not None:
            if "SourceSans3" not in registered:
                pdfmetrics.registerFont(TTFont("SourceSans3", str(body)))
            self.body_font = "SourceSans3"
        self.fonts_embedded = display is not None and body is not None
        self.styles = self._styles()

    def _styles(self) -> dict[str, ParagraphStyle]:
        defaults = getSampleStyleSheet()
        return {
            "cover_kicker": ParagraphStyle(
                "CoverKicker",
                parent=defaults["BodyText"],
                fontName=self.body_font,
                fontSize=8.5,
                leading=11,
                textColor=_c(COPPER),
                spaceAfter=9 * mm,
            ),
            "cover_title": ParagraphStyle(
                "CoverTitle",
                parent=defaults["Title"],
                fontName=self.display_font,
                fontSize=29,
                leading=33,
                textColor=_c(INK),
                spaceAfter=7 * mm,
            ),
            "cover_deck": ParagraphStyle(
                "CoverDeck",
                parent=defaults["BodyText"],
                fontName=self.body_font,
                fontSize=13,
                leading=18,
                textColor=_c(MUTED),
                spaceAfter=14 * mm,
            ),
            "h1": ParagraphStyle(
                "H1",
                parent=defaults["Heading1"],
                fontName=self.display_font,
                fontSize=20,
                leading=24,
                textColor=_c(INK),
                spaceBefore=4 * mm,
                spaceAfter=4 * mm,
                keepWithNext=True,
            ),
            "h2": ParagraphStyle(
                "H2",
                parent=defaults["Heading2"],
                fontName=self.body_font,
                fontSize=12,
                leading=15,
                textColor=_c(INDIGO),
                spaceBefore=5 * mm,
                spaceAfter=2.5 * mm,
                keepWithNext=True,
            ),
            "body": ParagraphStyle(
                "Body",
                parent=defaults["BodyText"],
                fontName=self.body_font,
                fontSize=9.3,
                leading=13.2,
                textColor=_c(INK),
                spaceAfter=2.6 * mm,
            ),
            "small": ParagraphStyle(
                "Small",
                parent=defaults["BodyText"],
                fontName=self.body_font,
                fontSize=7.4,
                leading=10,
                textColor=_c(MUTED),
            ),
            "callout": ParagraphStyle(
                "Callout",
                parent=defaults["BodyText"],
                fontName=self.body_font,
                fontSize=10.5,
                leading=14.5,
                textColor=_c(INDIGO),
                spaceAfter=0,
            ),
            "table": ParagraphStyle(
                "Table",
                parent=defaults["BodyText"],
                fontName=self.body_font,
                fontSize=7.4,
                leading=9.4,
                textColor=_c(INK),
            ),
            "table_head": ParagraphStyle(
                "TableHead",
                parent=defaults["BodyText"],
                fontName=self.body_font,
                fontSize=7.2,
                leading=8.8,
                textColor=_c(WHITE),
            ),
        }

    def _doc(self, output: Path, title: str, *, wide: bool = False) -> BaseDocTemplate:
        output.parent.mkdir(parents=True, exist_ok=True)
        page_size = landscape(A4) if wide else A4
        width, height = page_size
        doc = BaseDocTemplate(
            str(output),
            pagesize=page_size,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=20 * mm,
            bottomMargin=17 * mm,
            title=title,
            author="Traffic Radius",
            subject="Evidence-led enterprise SEO review",
            creator="Traffic Radius Enterprise SEO Studio",
            lang="en-AU",
        )
        frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")

        def decorate(canvas: Any, document: BaseDocTemplate) -> None:
            canvas.saveState()
            canvas.setFillColor(_c(PAPER))
            canvas.rect(0, 0, width, height, stroke=0, fill=1)
            canvas.setStrokeColor(_c(RULE))
            canvas.setLineWidth(0.6)
            canvas.line(doc.leftMargin, 12 * mm, width - doc.rightMargin, 12 * mm)
            canvas.setFont(self.body_font, 7.2)
            canvas.setFillColor(_c(MUTED))
            canvas.drawString(doc.leftMargin, 7.8 * mm, "TRAFFIC RADIUS · EVIDENCE BEFORE ASSERTION")
            canvas.drawRightString(
                width - doc.rightMargin, 7.8 * mm, f"{document.page} · {title}"
            )
            canvas.restoreState()

        doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=decorate)])
        return doc

    def _cover(
        self,
        title: str,
        subtitle: str,
        *,
        client: str,
        as_of: str,
        run_id: str,
        status: str = "APPROVAL-READY",
    ) -> list[Any]:
        meta = Table(
            [
                ["CLIENT", client],
                ["EVIDENCE AS OF", as_of],
                ["RUN", run_id],
                ["STATUS", status],
            ],
            colWidths=[34 * mm, 106 * mm],
        )
        meta.setStyle(
            TableStyle(
                [
                    ("FONT", (0, 0), (-1, -1), self.body_font, 8),
                    ("TEXTCOLOR", (0, 0), (0, -1), _c(MUTED)),
                    ("TEXTCOLOR", (1, 0), (1, -1), _c(INK)),
                    ("LINEBELOW", (0, 0), (-1, -2), 0.5, _c(RULE)),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        return [
            Spacer(1, 18 * mm),
            Paragraph("TRAFFIC RADIUS · ENTERPRISE SEO STUDIO", self.styles["cover_kicker"]),
            Paragraph(title, self.styles["cover_title"]),
            Paragraph(subtitle, self.styles["cover_deck"]),
            Spacer(1, 9 * mm),
            meta,
            Spacer(1, 22 * mm),
            Paragraph(
                "This report distinguishes verified evidence, derived analysis, professional judgment, and unavailable data. No client website or external platform was changed.",
                self.styles["callout"],
            ),
            PageBreak(),
        ]

    def _callout(self, text: str, label: str = "DECISION NOTE") -> Table:
        table = Table(
            [[Paragraph(label, self.styles["small"]), Paragraph(text, self.styles["callout"])]],
            colWidths=[31 * mm, 109 * mm],
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), _c(WHITE)),
                    ("BOX", (0, 0), (-1, -1), 0.7, _c(RULE)),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        return table

    def _table(
        self,
        headers: list[str],
        rows: Iterable[Iterable[Any]],
        widths: list[float] | None = None,
        *,
        repeat_rows: int = 1,
    ) -> LongTable:
        data = [[Paragraph(header, self.styles["table_head"]) for header in headers]]
        for row in rows:
            data.append(
                [Paragraph(safe_text(value), self.styles["table"]) for value in row]
            )
        table = LongTable(data, colWidths=widths, repeatRows=repeat_rows, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), _c(INDIGO)),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_c(WHITE), _c(PAPER)]),
                    ("GRID", (0, 0), (-1, -1), 0.35, _c(RULE)),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        return table

    @staticmethod
    def _split_scores(
        categories: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split into (scored, withheld). A null score is never coerced to zero."""
        scored = [
            item for item in categories
            if isinstance(item.get("score"), int | float)
        ]
        withheld = [
            item for item in categories
            if not isinstance(item.get("score"), int | float)
        ]
        return scored, withheld

    def _withheld_flowables(self, withheld: list[dict[str, Any]]) -> list[Any]:
        """List withheld categories as text — they must never appear as a 0 bar."""
        if not withheld:
            return []
        flowables: list[Any] = [
            Paragraph("Withheld categories", self.styles["h2"]),
            self._table(
                ["Category", "Evidence coverage", "Why it is withheld"],
                [
                    (
                        item.get("category"),
                        f"{item['coverage']:.0%}"
                        if isinstance(item.get("coverage"), int | float)
                        else "Unavailable",
                        item.get("unavailable_reason")
                        or "Evidence coverage below the publication threshold",
                    )
                    for item in withheld
                ],
                [38 * mm, 30 * mm, 72 * mm],
            ),
        ]
        return flowables

    def _score_chart(self, categories: list[dict[str, Any]]) -> Drawing:
        """Chart only the categories that actually have a measured score."""
        categories, _withheld = self._split_scores(categories)
        if not categories:
            drawing = Drawing(450, 40)
            drawing.add(
                String(
                    0, 20,
                    "No category cleared the evidence threshold, so no score is charted.",
                    fontName=self.body_font, fontSize=9, fillColor=_c(MUTED),
                )
            )
            return drawing
        drawing = Drawing(450, max(170, 28 * len(categories) + 50))
        chart = HorizontalBarChart()
        chart.x = 118
        chart.y = 24
        chart.height = max(105, 24 * len(categories))
        chart.width = 292
        chart.data = [[float(item["score"]) for item in categories]]
        chart.categoryAxis.categoryNames = [str(item["category"]) for item in categories]
        chart.categoryAxis.labels.fontName = self.body_font
        chart.categoryAxis.labels.fontSize = 7
        chart.valueAxis.valueMin = 0
        chart.valueAxis.valueMax = 100
        chart.valueAxis.valueStep = 20
        chart.valueAxis.labels.fontName = self.body_font
        chart.valueAxis.labels.fontSize = 7
        chart.bars[0].fillColor = _c(INDIGO)
        chart.bars[0].strokeColor = _c(INDIGO)
        chart.barWidth = 10
        drawing.add(chart)
        drawing.add(String(118, chart.height + 38, "Category scores (evidence-covered rules only)", fontName=self.body_font, fontSize=9, fillColor=_c(MUTED)))
        return drawing

    _MARKET_ROWS: tuple[tuple[str, str], ...] = (
        ("organic_keywords", "Organic keywords"),
        ("organic_traffic", "Organic traffic (monthly)"),
        ("organic_cost", "Organic traffic value"),
        ("authority_score", "Authority score"),
        ("referring_domains", "Referring domains"),
        ("backlinks_total", "Backlinks (total)"),
    )

    def _market_flowables(self, data: dict[str, Any]) -> list[Any]:
        """Market, keyword and competitor sections — measured values only."""
        market = data.get("market") if isinstance(data.get("market"), dict) else {}
        keywords = [row for row in (data.get("keywords") or []) if isinstance(row, dict)]
        competitors = [
            row for row in (data.get("competitors") or []) if isinstance(row, dict)
        ]
        performance = data.get("performance_vs_competitors")
        performance = performance if isinstance(performance, dict) else {}
        flowables: list[Any] = [Paragraph("Market position", self.styles["h1"])]

        domain = market.get("domain") if isinstance(market.get("domain"), dict) else {}
        if str(market.get("status") or "").casefold() == "available":
            flowables.append(
                self._table(
                    ["Metric", "Value", "Provider"],
                    [
                        (
                            label,
                            safe_text(domain.get(key)),
                            safe_text(market.get("provider")),
                        )
                        for key, label in self._MARKET_ROWS
                    ],
                    [48 * mm, 46 * mm, 46 * mm],
                )
            )
        else:
            flowables.append(
                Paragraph(
                    "Unavailable — "
                    + safe_text(
                        market.get("unavailable_reason"),
                        "no market data provider is connected to this run",
                    )
                    + ". No volume, ranking or traffic figure is estimated in its place.",
                    self.styles["body"],
                )
            )
        flowables.append(Spacer(1, 5 * mm))

        flowables.append(Paragraph("Keyword evidence", self.styles["h2"]))
        if keywords:
            volumes = [
                row.get("search_volume") for row in keywords
                if isinstance(row.get("search_volume"), int | float)
            ]
            mapped = [
                row for row in keywords
                if str(row.get("landing_url") or "").strip()
            ]
            top = sorted(
                (row for row in keywords if isinstance(row.get("search_volume"), int | float)),
                key=lambda row: -float(row["search_volume"]),
            )[:8]
            flowables.append(
                self._table(
                    ["Measure", "Value"],
                    [
                        ("Keywords measured", len(keywords)),
                        ("With a measured volume", len(volumes)),
                        ("Total measured volume", sum(volumes) if volumes else "Unavailable"),
                        ("Mapped to a crawled URL", len(mapped)),
                        ("Content gap (no mapped URL)", len(keywords) - len(mapped)),
                    ],
                    [72 * mm, 68 * mm],
                )
            )
            if top:
                flowables.extend([
                    Spacer(1, 4 * mm),
                    self._table(
                        ["Keyword", "Volume", "Position", "Landing URL"],
                        [
                            (
                                row.get("phrase"),
                                safe_text(row.get("search_volume")),
                                safe_text(row.get("position")),
                                safe_text(row.get("landing_url"), "Not mapped"),
                            )
                            for row in top
                        ],
                        [46 * mm, 18 * mm, 18 * mm, 58 * mm],
                    ),
                ])
        else:
            flowables.append(
                Paragraph(
                    "Unavailable — no keyword provider is connected, so no phrase, "
                    "volume or ranking is reported for this run.",
                    self.styles["body"],
                )
            )
        flowables.append(Spacer(1, 5 * mm))

        flowables.append(Paragraph("Competitor landscape", self.styles["h2"]))
        if competitors:
            flowables.append(
                self._table(
                    ["Competitor", "Common keywords", "Organic keywords", "Organic traffic"],
                    [
                        (
                            row.get("domain"),
                            safe_text(row.get("common_keywords")),
                            safe_text(row.get("organic_keywords")),
                            safe_text(row.get("organic_traffic")),
                        )
                        for row in competitors[:10]
                    ],
                    [50 * mm, 30 * mm, 30 * mm, 30 * mm],
                )
            )
        else:
            flowables.append(
                Paragraph(
                    "Unavailable — no competitor set was measured for this run.",
                    self.styles["body"],
                )
            )

        metrics = [row for row in (performance.get("metrics") or []) if isinstance(row, dict)]
        if metrics:
            flowables.extend([
                Spacer(1, 4 * mm),
                self._table(
                    ["Metric", "Client", "Competitor median", "Position"],
                    [
                        (
                            row.get("metric"),
                            safe_text(row.get("client")),
                            safe_text(row.get("competitor_median")),
                            safe_text(row.get("position"), "unknown"),
                        )
                        for row in metrics
                    ],
                    [56 * mm, 28 * mm, 32 * mm, 24 * mm],
                ),
            ])
        flowables.extend([Spacer(1, 5 * mm), PageBreak()])
        return flowables

    def executive_report(self, data: dict[str, Any], output: Path) -> Path:
        client = data["client"]["name"]
        run = data["run"]
        findings = data.get("findings", [])
        actions = data.get("actions", [])
        doc = self._doc(output, "Executive SEO Review")
        story = self._cover(
            "Enterprise SEO Evidence & Direction",
            "A decision brief connecting observed site evidence to a safe, sequenced programme of work.",
            client=client,
            as_of=run["evidence_as_of"],
            run_id=run["id"],
        )
        story.extend(
            [
                Paragraph("The decision in one page", self.styles["h1"]),
                self._callout(data["executive_summary"]),
                Spacer(1, 6 * mm),
                Paragraph("Evidence posture", self.styles["h2"]),
                self._table(
                    ["Signal", "Status", "Interpretation"],
                    [
                        (
                            "Coverage",
                            f"{run['evidence_coverage']:.0%}",
                            run["coverage_interpretation"],
                        ),
                        (
                            "Overall health score",
                            safe_text(run.get("overall_score"), "Withheld"),
                            run["overall_score_reason"],
                        ),
                        ("Approved domain", data["client"]["domain"], "Domain boundary enforced"),
                        ("External changes", "None", "Recommendations remain proposals"),
                    ],
                    [37 * mm, 31 * mm, 72 * mm],
                ),
                Spacer(1, 6 * mm),
                Paragraph("Where evidence points first", self.styles["h2"]),
                self._score_chart(data.get("categories", [])),
                *self._withheld_flowables(
                    self._split_scores(data.get("categories", []))[1]
                ),
                PageBreak(),
                *self._market_flowables(data),
                Paragraph("Priority findings", self.styles["h1"]),
                self._table(
                    ["Priority", "Finding", "Why it matters", "Confidence", "Evidence"],
                    [
                        (
                            finding["priority"],
                            finding["title"],
                            finding["impact"],
                            f"{finding['confidence']:.0%}",
                            ", ".join(finding["evidence_ids"]),
                        )
                        for finding in findings[:10]
                    ],
                    [15 * mm, 35 * mm, 47 * mm, 19 * mm, 24 * mm],
                ),
                Spacer(1, 7 * mm),
                Paragraph("First 30 days", self.styles["h1"]),
                self._table(
                    ["Week", "Action", "Owner", "Approval", "KPI"],
                    [
                        (
                            item["week"],
                            item["action"],
                            item["owner"],
                            item["approval_class"],
                            item["kpi"],
                        )
                        for item in actions
                        if int(item["week_end"]) <= 4
                    ],
                    [14 * mm, 54 * mm, 23 * mm, 25 * mm, 24 * mm],
                ),
                Spacer(1, 6 * mm),
                self._callout(
                    "Approve Gate 1 only after the evidence register and strategic direction are accepted. Risky deployment assets remain separately approval-gated.",
                    "NEXT CONTROL",
                ),
                PageBreak(),
                Paragraph("Limitations and unavailable evidence", self.styles["h1"]),
            ]
        )
        for limitation in data.get("limitations", []):
            story.append(Paragraph(f"• {limitation}", self.styles["body"]))
        story.extend(
            [
                Paragraph("Source register", self.styles["h1"]),
                self._table(
                    ["ID", "Source", "Captured", "Scope", "Status"],
                    [
                        (
                            source["id"],
                            source["label"],
                            source["captured_at"],
                            source["scope"],
                            source["status"],
                        )
                        for source in data.get("sources", [])
                    ],
                    [19 * mm, 45 * mm, 25 * mm, 32 * mm, 19 * mm],
                ),
            ]
        )
        doc.build(story)
        return output

    def strategy_report(self, data: dict[str, Any], output: Path) -> Path:
        doc = self._doc(output, "Enterprise SEO Strategy")
        run = data["run"]
        story = self._cover(
            "Evidence-led SEO Strategy",
            "A 16-week operating strategy for technical integrity, discoverability, information architecture, and measured content growth.",
            client=data["client"]["name"],
            as_of=run["evidence_as_of"],
            run_id=run["id"],
        )
        for section in data.get("strategy_sections", []):
            story.append(Paragraph(section["title"], self.styles["h1"] if section.get("level", 1) == 1 else self.styles["h2"]))
            for paragraph in section.get("paragraphs", []):
                story.append(Paragraph(paragraph, self.styles["body"]))
            if section.get("decision"):
                story.append(self._callout(section["decision"], "STRATEGIC DECISION"))
                story.append(Spacer(1, 4 * mm))
        story.extend(
            [
                PageBreak(),
                Paragraph("Canonical opportunity map", self.styles["h1"]),
                self._table(
                    ["Cluster", "Intent", "Target URL", "Decision", "Evidence"],
                    [
                        (
                            item["cluster"],
                            item["intent"],
                            item["target_url"],
                            item["decision"],
                            ", ".join(item["evidence_ids"]),
                        )
                        for item in data.get("opportunities", [])
                    ],
                    [31 * mm, 23 * mm, 42 * mm, 26 * mm, 18 * mm],
                ),
                Spacer(1, 6 * mm),
                Paragraph("Measurement contract", self.styles["h1"]),
                self._table(
                    ["KPI", "Baseline", "Cadence", "Source", "Decision use"],
                    [
                        (
                            item["kpi"],
                            item["baseline"],
                            item["cadence"],
                            item["source"],
                            item["decision_use"],
                        )
                        for item in data.get("measurement_plan", [])
                    ],
                    [28 * mm, 26 * mm, 22 * mm, 29 * mm, 35 * mm],
                ),
            ]
        )
        doc.build(story)
        return output

    def content_strategy(self, data: dict[str, Any], output: Path) -> Path:
        """Demand-and-publishing strategy: a distinct document from strategy_report."""

        doc = self._doc(output, "Content and Keyword Strategy")
        run = data["run"]
        market = data.get("market") or {}
        keywords = list(data.get("keywords") or [])
        clusters = list(data.get("keyword_clusters") or [])
        assets = list(data.get("content_assets") or [])
        story = self._cover(
            "Content and Keyword Strategy",
            "Where measured search demand meets the pages that exist today, and what should be published next.",
            client=data["client"]["name"],
            as_of=run["evidence_as_of"],
            run_id=run["id"],
        )
        if market.get("status") == "available":
            domain = market.get("domain") or {}
            demand = (
                f"{len(keywords)} ranking keywords retrieved from "
                f"{market.get('provider', 'the connected provider')} "
                f"({market.get('database', 'configured')} database). Domain totals: "
                f"{_pdf_display(domain.get('organic_keywords'))} organic keywords, "
                f"{_pdf_display(domain.get('organic_traffic'))} estimated monthly sessions."
            )
        else:
            demand = (
                "Search-demand metrics are unavailable for this run: "
                f"{market.get('unavailable_reason') or 'no keyword provider is connected'}. "
                "Priorities below derive from crawl evidence only; no volume or traffic figure is asserted."
            )
        story.extend(
            [
                Paragraph("Demand evidence", self.styles["h1"]),
                Paragraph(demand, self.styles["body"]),
                Spacer(1, 4 * mm),
                Paragraph("Topic clusters and coverage", self.styles["h1"]),
                self._table(
                    ["Cluster", "Keywords", "Volume", "Coverage", "Primary URL"],
                    [
                        (
                            cluster.get("name", "Unavailable"),
                            cluster.get("keyword_count", 0),
                            _pdf_display(cluster.get("total_volume")),
                            cluster.get("coverage", "unknown"),
                            cluster.get("primary_url") or "No mapped page",
                        )
                        for cluster in clusters[:30]
                    ]
                    or [("No clusters were derived from the available evidence.", "", "", "", "")],
                    [38 * mm, 18 * mm, 18 * mm, 22 * mm, 44 * mm],
                ),
                Spacer(1, 5 * mm),
                Paragraph("Priority keywords", self.styles["h1"]),
                self._table(
                    ["Keyword", "Position", "Volume", "CPC", "Landing URL"],
                    [
                        (
                            item.get("phrase", ""),
                            _pdf_display(item.get("position")),
                            _pdf_display(item.get("search_volume")),
                            _pdf_display(item.get("cpc")),
                            item.get("landing_url") or "Unmapped",
                        )
                        for item in keywords[:30]
                    ]
                    or [("No provider keyword data was available for this run.", "", "", "", "")],
                    [46 * mm, 18 * mm, 18 * mm, 16 * mm, 42 * mm],
                ),
                PageBreak(),
                Paragraph("Publishing priorities", self.styles["h1"]),
                self._table(
                    ["Asset", "Intent", "Target URL", "Approval"],
                    [
                        (
                            asset.get("title", ""),
                            asset.get("intent", ""),
                            asset.get("target_url", ""),
                            asset.get("approval_state", ""),
                        )
                        for asset in assets
                    ]
                    or [("No content assets passed the evidence gates for this run.", "", "", "")],
                    [52 * mm, 26 * mm, 44 * mm, 18 * mm],
                ),
            ]
        )
        doc.build(story)
        return output

    def action_plan(self, data: dict[str, Any], output: Path) -> Path:
        doc = self._doc(output, "16-Week Action Plan", wide=True)
        run = data["run"]
        story = self._cover(
            "16-Week Canonical Action Plan",
            "One authoritative sequence with owners, dependencies, effort, evidence, KPIs, and approval controls.",
            client=data["client"]["name"],
            as_of=run["evidence_as_of"],
            run_id=run["id"],
        )
        story.extend(
            [
                Paragraph("Sequenced delivery plan", self.styles["h1"]),
                self._table(
                    ["ID", "Wk", "Priority", "Action", "Owner", "Depends", "Effort", "KPI", "Approval"],
                    [
                        (
                            item["id"],
                            f"{item['week']}-{item['week_end']}",
                            item["priority"],
                            item["action"],
                            item["owner"],
                            ", ".join(item.get("dependencies", [])) or "None",
                            item["effort"],
                            item["kpi"],
                            item["approval_class"],
                        )
                        for item in data.get("actions", [])
                    ],
                    [14 * mm, 14 * mm, 16 * mm, 72 * mm, 24 * mm, 27 * mm, 21 * mm, 34 * mm, 29 * mm],
                ),
                Spacer(1, 6 * mm),
                self._callout(
                    "This schedule is canonical. CSV and XLSX derivatives reconcile to the same action IDs; any change creates a new version rather than an untracked edit.",
                    "CONTROL",
                ),
            ]
        )
        doc.build(story)
        return output

    def qa_report(self, data: dict[str, Any], output: Path) -> Path:
        doc = self._doc(output, "Package Quality Assurance")
        run = data["run"]
        qa = data["qa"]
        story = self._cover(
            "Package Quality Assurance",
            "Release evidence for domain safety, claims, reconciliations, rendering, approvals, and package integrity.",
            client=data["client"]["name"],
            as_of=run["evidence_as_of"],
            run_id=run["id"],
            status=qa["release_status"],
        )
        story.extend(
            [
                Paragraph("Release decision", self.styles["h1"]),
                self._callout(qa["release_statement"], "QA VERDICT"),
                Spacer(1, 6 * mm),
                self._table(
                    ["Gate", "Status", "Critical", "High", "Evidence"],
                    [
                        (
                            gate["name"],
                            gate["status"],
                            gate["critical_failures"],
                            gate["high_failures"],
                            gate["evidence"],
                        )
                        for gate in qa.get("gates", [])
                    ],
                    [39 * mm, 23 * mm, 18 * mm, 18 * mm, 42 * mm],
                ),
                Spacer(1, 6 * mm),
                Paragraph("Reconciled counts", self.styles["h1"]),
                self._table(
                    ["Measure", "Canonical", "Package", "Result"],
                    [
                        (item["measure"], item["canonical"], item["package"], item["result"])
                        for item in qa.get("reconciliation", [])
                    ],
                    [48 * mm, 27 * mm, 27 * mm, 38 * mm],
                ),
                Spacer(1, 6 * mm),
                Paragraph("Known limitations", self.styles["h1"]),
            ]
        )
        for limitation in data.get("limitations", []):
            story.append(Paragraph(f"• {limitation}", self.styles["body"]))
        doc.build(story)
        return output

    def comparison_report(self, data: dict[str, Any], output: Path) -> Path:
        doc = self._doc(output, "Kakawa v18 vs v19 Quality Comparison")
        run = data["run"]
        benchmark = data.get("quality_benchmark", {})
        packages = benchmark.get("packages", [])
        categories = benchmark.get("categories", [])
        story = self._cover(
            "Kakawa v18 to v19 Quality Comparison",
            "A scored evidence-and-usability benchmark plus negative-regression review.",
            client=data["client"]["name"],
            as_of=run["evidence_as_of"],
            run_id=run["id"],
        )
        if packages:
            story.extend([
                Paragraph("Quality score", self.styles["h1"]),
                Paragraph(benchmark.get("method", ""), self.styles["body"]),
                self._table(
                    ["Package", "Score / 100", "Verdict"],
                    [(item["version"], item["total"], item["verdict"]) for item in packages],
                    [35 * mm, 22 * mm, 83 * mm],
                ),
                Spacer(1, 6 * mm),
            ])
            if categories and all(len(item.get("scores", [])) == len(categories) for item in packages):
                story.extend([
                    Paragraph("Rubric detail", self.styles["h1"]),
                    self._table(
                        ["Category", "Weight", *[item["version"] for item in packages]],
                        [
                            (category["name"], category["weight"], *[item["scores"][index] for item in packages])
                            for index, category in enumerate(categories)
                        ],
                        [48 * mm, 18 * mm, 24 * mm, 24 * mm, 26 * mm],
                    ),
                    Spacer(1, 7 * mm),
                ])
        story.extend([
            Paragraph("Regression outcomes", self.styles["h1"]),
            self._table(
                ["Failure mode", "v18 observation", "v19 control", "v19 result"],
                [(item["failure_mode"], item["v18_observation"], item["v19_control"], item["v19_result"]) for item in data.get("comparison", [])],
                [31 * mm, 35 * mm, 45 * mm, 29 * mm],
            ),
            Spacer(1, 7 * mm),
            self._callout(
                "Enhanced v19 preserves V18's useful breadth while removing duplicate payloads, unsafe assumptions and contradictory counts. Missing private evidence remains unavailable rather than fabricated.",
                "BENCHMARK CONCLUSION",
            ),
        ])
        doc.build(story)
        return output
    def deck_pdf(self, data: dict[str, Any], output: Path) -> Path:
        """Render the deck's PDF sibling.

        Delegates to :func:`exporters.pptx_deck.render_deck_pdf` so the PPTX and
        the PDF cannot drift: one slide sequence, one page per slide, no
        LibreOffice dependency.
        """
        from exporters.pptx_deck import render_deck_pdf

        return render_deck_pdf(data, Path(output))


def write_qa_json(data: dict[str, Any], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "run_id": data["run"]["id"],
        "client": data["client"],
        "qa": data["qa"],
        "limitations": data.get("limitations", []),
    }
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output

