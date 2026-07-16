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


class PDFReportBuilder:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.display_font = "Helvetica-Bold"
        self.body_font = "Helvetica"
        display, body = font_paths(project_root)
        if display and body:
            pdfmetrics.registerFont(TTFont("Fraunces", str(display)))
            pdfmetrics.registerFont(TTFont("SourceSans3", str(body)))
            self.display_font = "Fraunces"
            self.body_font = "SourceSans3"
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
                uppercase=True,
                letterSpacing=1.4,
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

    def _score_chart(self, categories: list[dict[str, Any]]) -> Drawing:
        drawing = Drawing(450, max(170, 28 * len(categories) + 50))
        chart = HorizontalBarChart()
        chart.x = 118
        chart.y = 24
        chart.height = max(105, 24 * len(categories))
        chart.width = 292
        chart.data = [[float(item.get("score") or 0) for item in categories]]
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
                PageBreak(),
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
        """Render a self-contained PDF slide derivative independent of office software."""
        output.parent.mkdir(parents=True, exist_ok=True)
        width, height = landscape(A4)
        doc = BaseDocTemplate(
            str(output),
            pagesize=(width, height),
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=18 * mm,
            bottomMargin=16 * mm,
            title="Kakawa Enterprise SEO Executive Deck",
            author="Traffic Radius",
            subject="Evidence-led SEO approval deck",
        )
        frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="slide")

        def background(canvas: Any, document: BaseDocTemplate) -> None:
            canvas.saveState()
            canvas.setFillColor(_c(PAPER))
            canvas.rect(0, 0, width, height, fill=1, stroke=0)
            canvas.setFont(self.body_font, 7)
            canvas.setFillColor(_c(MUTED))
            canvas.drawString(20 * mm, 8 * mm, "TRAFFIC RADIUS · KAKAWA CHOCOLATES")
            canvas.drawRightString(width - 20 * mm, 8 * mm, str(document.page))
            canvas.restoreState()

        doc.addPageTemplates([PageTemplate(id="deck", frames=[frame], onPage=background)])
        slides: list[Any] = []
        for index, slide in enumerate(data.get("deck", [])):
            slides.extend(
                [
                    Paragraph(slide.get("eyebrow", "EXECUTIVE REVIEW"), self.styles["cover_kicker"]),
                    Paragraph(slide["title"], self.styles["cover_title"]),
                    Paragraph(slide["body"], self.styles["cover_deck"]),
                ]
            )
            if slide.get("points"):
                slides.append(
                    self._table(
                        ["Signal", "Meaning"],
                        [(point["label"], point["text"]) for point in slide["points"]],
                        [45 * mm, 150 * mm],
                    )
                )
            if index < len(data.get("deck", [])) - 1:
                slides.append(PageBreak())
        doc.build(slides)
        return output


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

