"""Contract tests for the pure render engines (xlsx, pptx, markdown)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook
from pptx import Presentation
from pptx.util import Inches

from exporters.markdown_summary import render_markdown
from exporters.pptx_deck import render_deck
from exporters.xlsx_workbooks import EMPTY_MESSAGE, render_workbooks

pytestmark = pytest.mark.render

EXPECTED_WORKBOOKS = {
    "Technical_Audit_Report.xlsx",
    "OnPage_Audit_Report.xlsx",
    "Performance_And_Tracking_Audit.xlsx",
    "Keyword_And_Topic_Observations.xlsx",
    "URL_Architecture_Map.xlsx",
    "16_Week_Action_Plan.xlsx",
    "Title_Tag_Optimizations.xlsx",
    "Meta_Description_Optimizations.xlsx",
    "H1_Optimizations.xlsx",
    "Canonical_Review.xlsx",
    "Internal_Link_Map.xlsx",
}


def _page(number: int, **overrides: Any) -> dict[str, Any]:
    url = f"https://acmewidgets.com.au/{'about' if number == 2 else 'shop' if number == 3 else ''}"
    page: dict[str, Any] = {
        "id": f"URL-{number:04d}",
        "evidence_id": f"EV-{number:04d}",
        "original_url": url,
        "original_urls": [url],
        "duplicate_observations": 1,
        "normalized_url": url,
        "status_code": 200,
        "title": f"Acme Widgets page {number}",
        "meta_description": f"Meta description for page {number} of the Acme Widgets site.",
        "h1": f"Heading {number}",
        "canonical_url": url,
        "indexability": "No noindex observed",
        "word_count": 800 + number,
        "internal_links": 20 + number,
        "redirect_chain": [url],
        "content_type": "text/html; charset=utf-8",
        "body_sha256": f"hash-{number:02d}",
        "page_type": "Homepage" if number == 1 else "Content",
        "links": [url],
        "captured_at": "2026-07-16T02:00:00+00:00",
    }
    page.update(overrides)
    return page


def _sample_data() -> dict[str, Any]:
    """Sample run data modeled on the exporters contract fixture shapes."""
    pages = [
        _page(
            1,
            schema_types=["Organization"],
            images_total=10,
            images_missing_alt=2,
            response_ms=350,
            analytics_tags=["GA4"],
        ),
        _page(
            2,
            word_count=120,
            canonical_url="https://acmewidgets.com.au/about-us",
            response_ms=90,
            analytics_tags=[],
        ),
        _page(
            3,
            status_code=404,
            title=None,
            meta_description=None,
            h1=None,
            canonical_url=None,
            word_count=None,
            redirect_chain=[
                "https://acmewidgets.com.au/old-shop",
                "https://acmewidgets.com.au/shop",
            ],
        ),
    ]
    findings = [
        {
            "id": "F-001",
            "priority": "P1",
            "priority_score": 92.0,
            "category": "technical",
            "rule_id": "technical.http_status",
            "rule_version": "1.0.0",
            "severity": "Critical",
            "title": "HTTP 404 response on a linked page",
            "description": "A crawled URL returned 404 while still receiving internal links.",
            "impact": "Wasted crawl budget and broken user journeys.",
            "confidence": 1.0,
            "reach": "1 page",
            "affected_count": 1,
            "affected_urls": ["https://acmewidgets.com.au/shop"],
            "effort": "S",
            "implementation_risk": "low",
            "approval_class": "agency_admin",
            "as_of_date": "2026-07-16",
            "evidence_ids": ["EV-0003"],
        },
        {
            "id": "F-002",
            "priority": "P2",
            "priority_score": 71.0,
            "category": "on_page",
            "rule_id": "on_page.title_length",
            "rule_version": "1.0.0",
            "severity": "High",
            "title": "Title tag outside recommended length",
            "description": "One page title exceeds the recommended pixel budget.",
            "impact": "Truncated snippets reduce click-through.",
            "confidence": 0.9,
            "reach": "1 page",
            "affected_count": 1,
            "affected_urls": ["https://acmewidgets.com.au/"],
            "effort": "S",
            "implementation_risk": "low",
            "approval_class": "editorial",
            "as_of_date": "2026-07-16",
            "evidence_ids": ["EV-0001"],
        },
        {
            "id": "F-003",
            "priority": "P3",
            "priority_score": 45.0,
            "category": "performance",
            "rule_id": "performance.response_ms",
            "rule_version": "1.0.0",
            "severity": "Medium",
            "title": "Slow HTML response observed",
            "description": "The homepage HTML response exceeded 300 ms during the crawl.",
            "impact": "Slower first byte delays every downstream metric.",
            "confidence": 0.8,
            "reach": "1 page",
            "affected_count": 1,
            "affected_urls": ["https://acmewidgets.com.au/"],
            "effort": "M",
            "implementation_risk": "medium",
            "approval_class": "agency_admin",
            "as_of_date": "2026-07-16",
            "evidence_ids": ["EV-0001"],
        },
        {
            "id": "F-004",
            "priority": "P4",
            "priority_score": 20.0,
            "category": "keyword_architecture",
            "rule_id": "keywords.overlap",
            "rule_version": "1.0.0",
            "severity": "Low",
            "title": "Two pages target overlapping topics",
            "description": "The about and shop pages share heading topics.",
            "impact": "Potential internal competition for one intent.",
            "confidence": 0.7,
            "reach": "2 pages",
            "affected_count": 2,
            "affected_urls": [
                "https://acmewidgets.com.au/about",
                "https://acmewidgets.com.au/shop",
            ],
            "effort": "M",
            "implementation_risk": "low",
            "approval_class": "editorial",
            "as_of_date": "2026-07-16",
            "evidence_ids": ["EV-0002"],
        },
    ]
    actions = [
        {
            "id": "A-001", "phase": "Foundation", "week": 1, "week_end": 1,
            "priority": "P1", "action": "Approve the evidence boundary",
            "owner": "Agency admin", "dependencies": [], "effort": "3h",
            "kpi": "Scope decision recorded", "approval_class": "agency_admin",
            "status": "Ready", "evidence_ids": ["EV-0003"], "confidence": 1.0,
            "implementation_risk": "low", "notes": "Advisory only.",
        },
        {
            "id": "A-002", "phase": "Foundation", "week": 2, "week_end": 2,
            "priority": "P1", "action": "Fix the 404 shop route",
            "owner": "Developer", "dependencies": ["A-001"], "effort": "4h",
            "kpi": "Route returns 200", "approval_class": "agency_admin",
            "status": "Ready", "evidence_ids": ["EV-0003"], "confidence": 1.0,
            "implementation_risk": "low", "notes": None,
        },
        {
            "id": "A-003", "phase": "Technical Hardening", "week": 3, "week_end": 6,
            "priority": "P2", "action": "Resolve canonical mismatch on the about page",
            "owner": "Developer", "dependencies": [], "effort": "6h",
            "kpi": "Canonical matches the normalized URL",
            "approval_class": "agency_admin", "status": "Ready",
            "evidence_ids": ["EV-0002"], "confidence": 0.9,
            "implementation_risk": "medium", "notes": None,
        },
        {
            "id": "A-004", "phase": "Content", "week": 7, "week_end": 10,
            "priority": "P2", "action": "Rewrite the oversized homepage title",
            "owner": "Editor", "dependencies": [], "effort": "2h",
            "kpi": "Title within length budget", "approval_class": "editorial",
            "status": "Ready", "evidence_ids": ["EV-0001"], "confidence": 0.9,
            "implementation_risk": "low", "notes": None,
        },
        {
            "id": "A-005", "phase": "Content", "week": 9, "week_end": 12,
            "priority": "P3", "action": "Expand the thin about page",
            "owner": "Editor", "dependencies": [], "effort": "8h",
            "kpi": "Word count above threshold", "approval_class": "editorial",
            "status": "Ready", "evidence_ids": [], "confidence": 0.8,
            "implementation_risk": "low", "notes": None,
        },
        {
            "id": "A-006", "phase": "Measurement", "week": 13, "week_end": 16,
            "priority": "P3", "action": "Connect GSC and baseline the KPIs",
            "owner": "SEO lead", "dependencies": [], "effort": "4h",
            "kpi": "Baseline captured", "approval_class": "agency_admin",
            "status": "Ready", "evidence_ids": [], "confidence": 1.0,
            "implementation_risk": "low", "notes": None,
        },
    ]
    deck = [
        {"kind": "cover", "eyebrow": "ENTERPRISE SEO REVIEW",
         "title": "Evidence first. Growth second.",
         "body": "An approved-domain review of Acme Widgets.", "points": []},
        {"kind": "score", "eyebrow": "EVIDENCE POSTURE",
         "title": "Scores follow coverage.",
         "body": "One category is withheld below the coverage threshold.", "points": []},
        {"kind": "generic", "eyebrow": "TECHNICAL", "title": "Stabilise the crawl surface.",
         "body": "Fix the 404 route and the canonical mismatch first.",
         "points": [{"label": "Errors", "text": "1 broken route"},
                    {"label": "Canonicals", "text": "1 mismatch"}]},
        {"kind": "generic", "eyebrow": "ON-PAGE", "title": "Metadata within budget.",
         "body": "Approve the proposed title rewrites.",
         "points": [{"label": "Titles", "text": "1 oversized"}]},
        {"kind": "generic", "eyebrow": "CONTENT", "title": "Depth where it matters.",
         "body": "Expand the thin about page.", "points": []},
        {"kind": "timeline", "eyebrow": "16-WEEK ROADMAP",
         "title": "Sequence removes risk.",
         "body": "Foundation, hardening, content, measurement."},
        {"kind": "generic", "eyebrow": "MEASUREMENT", "title": "Prove it with baselines.",
         "body": "Connect first-party evidence before forecasting.",
         "points": [{"label": "GSC", "text": "Awaiting connection"}]},
        {"kind": "comparison", "eyebrow": "BEFORE / AFTER",
         "title": "Controls become blockers.",
         "body": "Manual checks become machine-verifiable gates.",
         "points": [{"label": "Before", "text": "Manual spot checks"},
                    {"label": "After", "text": "Release gates with evidence"}]},
        {"kind": "generic", "eyebrow": "NEXT", "title": "Decisions requested.",
         "body": "Approve the plan to unlock implementation.", "points": []},
    ]
    return {
        "schema_version": "1.0.0",
        "client": {"name": "Acme Widgets", "domain": "acmewidgets.com.au",
                   "locale": "en-AU"},
        "project": {"id": "proj-acme", "name": "Acme Widgets Enterprise SEO",
                    "profile": "Enterprise", "business_profile": "ecommerce"},
        "run": {
            "id": "RUN-ACME-001",
            "profile": "Enterprise",
            "configured_page_budget": 500,
            "evidence_as_of": "2026-07-16",
            "captured_at": "2026-07-16T02:00:00+00:00",
            "rule_version": "1.0.0",
            "evidence_coverage": 0.55,
            "coverage_interpretation": "Crawl-only evidence; private sources not connected.",
            "overall_score": None,
            "overall_score_reason": (
                "Weighted evidence coverage 55.0% is below the 70% publication threshold"
            ),
            "state": "GATE_1_REVIEW",
        },
        "executive_summary": (
            "Stabilise the crawl surface, then connect first-party evidence before "
            "expanding content."
        ),
        "sources": [
            {"id": "SRC-CRAWL", "label": "Approved-domain crawl", "kind": "crawl",
             "status": "available", "captured_at": "2026-07-16T02:00:00+00:00",
             "scope": "3 fetched pages", "coverage": 1.0, "unavailable_reason": None},
            {"id": "SRC-GSC", "label": "Google Search Console", "kind": "gsc",
             "status": "unavailable", "captured_at": "2026-07-16T02:00:00+00:00",
             "scope": "Not collected", "coverage": 0.0,
             "unavailable_reason": "credential_not_configured"},
        ],
        "evidence": [],
        "pages": pages,
        "findings": findings,
        "categories": [
            {"category": "Technical", "key": "technical", "score": 72.5,
             "coverage": 1.0, "weight": 0.25, "rule_version": "1.0.0",
             "status": "available", "unavailable_reason": None,
             "evidence_ids": ["EV-0003"]},
            {"category": "Content", "key": "on_page", "score": None,
             "coverage": 0.2, "weight": 0.25, "rule_version": "1.0.0",
             "status": "unavailable",
             "unavailable_reason": "Content evidence coverage below threshold",
             "evidence_ids": []},
        ],
        "content_assets": [
            {"id": "CONTENT-01", "slug": "widget-buying-guide",
             "title": "Widget buying guide", "asset_type": "Editorial guide",
             "target_url": "https://acmewidgets.com.au/shop",
             "audience": "Buyers", "intent": "Commercial",
             "primary_topic": "Widgets", "headline": "Choose the right widget",
             "summary": "A grounded guide.", "body": [], "claims": [],
             "approval_state": "draft_pending_review",
             "generation_method": "grounded", "evidence_ids": ["EV-0003"]},
        ],
        "opportunities": [
            {"id": "OPP-01", "cluster": "Widget comparison",
             "intent": "commercial investigation",
             "target_url": "https://acmewidgets.com.au/shop",
             "decision": "Improve the existing page", "evidence_ids": ["EV-0003"],
             "keyword_volume": None, "ranking": None,
             "unavailable_reason": "GSC and SEMrush not connected"},
            {"id": "OPP-02", "cluster": "Widget care",
             "intent": "informational",
             "target_url": "https://acmewidgets.com.au/about",
             "decision": "Create one supporting article", "evidence_ids": ["EV-0002"],
             "keyword_volume": None, "ranking": None,
             "unavailable_reason": "GSC and SEMrush not connected"},
        ],
        "actions": actions,
        "strategy_sections": [],
        "measurement_plan": [],
        "generation_ledger": [],
        "qa": {"release_status": "PASS", "release_statement": "No blocking failures.",
               "gates": [], "reconciliation": []},
        "limitations": [
            "Private analytics remain unavailable until a connection is approved.",
        ],
        "deployment": {
            "redirect_candidates": [],
            "canonical_candidates": [
                {"url": "https://acmewidgets.com.au/about",
                 "current_canonical": "https://acmewidgets.com.au/about-us",
                 "proposed_canonical": "https://acmewidgets.com.au/about",
                 "reason": "Canonical points at a variant URL",
                 "approval_status": "review_required", "evidence_id": "EV-0002"},
            ],
            "metadata_review": [
                {"page_id": "URL-0001", "url": "https://acmewidgets.com.au/",
                 "page_type": "Homepage", "status_code": 200,
                 "current_title": "Acme Widgets page 1", "title_length": 76,
                 "title_issue": "Too long",
                 "proposed_title": "Acme Widgets | Australian-made widgets",
                 "current_meta_description": "Meta description for page 1.",
                 "meta_description_length": 28, "meta_description_issue": "Too short",
                 "proposed_meta_description": "A longer, more useful description.",
                 "current_h1": "Heading 1", "h1_issue": "OK",
                 "proposed_h1": "Heading 1",
                 "target_keyword": "Unavailable - GSC and SEMrush not connected",
                 "priority": "P2", "evidence_id": "EV-0001",
                 "approval_status": "withheld_pending_editorial_review"},
                {"page_id": "URL-0002", "url": "https://acmewidgets.com.au/about",
                 "page_type": "Content", "status_code": 200,
                 "current_title": "Acme Widgets page 2", "title_length": 19,
                 "title_issue": "OK", "proposed_title": "Acme Widgets page 2",
                 "current_meta_description": None, "meta_description_length": 0,
                 "meta_description_issue": "Missing",
                 "proposed_meta_description": "About Acme Widgets and our workshop.",
                 "current_h1": None, "h1_issue": "Missing",
                 "proposed_h1": "About Acme Widgets",
                 "target_keyword": "Unavailable - GSC and SEMrush not connected",
                 "priority": "P2", "evidence_id": "EV-0002",
                 "approval_status": "withheld_pending_editorial_review"},
            ],
            "internal_link_candidates": [
                {"source_url": "https://acmewidgets.com.au/about",
                 "target_url": "https://acmewidgets.com.au/shop",
                 "anchor": "Browse the widget range",
                 "rationale": "Connect the about narrative to the catalogue",
                 "link_type": "Content to commercial",
                 "observed_status": "Observed in crawl",
                 "evidence_ids": ["EV-0002", "EV-0003"],
                 "approval_status": "review_ready"},
            ],
            "schema": {"deployable": [], "withheld": [
                {"reason": "No verified fact pack was available",
                 "approval_status": "withheld_pending_agency_admin"},
            ]},
            "robots": {"deployable_changes": [],
                       "recommendation": "No robots.txt change is proposed."},
            "disavow": {"enabled": False, "reason": "No backlink evidence connected."},
        },
        "deck": deck,
    }


def _clean_data() -> dict[str, Any]:
    """A no-issue variant: all pages healthy, nothing flagged anywhere."""
    data = _sample_data()
    data["pages"] = [_page(1), _page(2), _page(3)]
    data["findings"] = []
    data["deployment"]["metadata_review"] = []
    data["deployment"]["canonical_candidates"] = []
    data["deployment"]["internal_link_candidates"] = []
    data["opportunities"] = []
    return data


def _sheet_texts(worksheet: Any) -> list[str]:
    texts: list[str] = []
    for row in worksheet.iter_rows(values_only=True):
        texts.extend(str(value) for value in row if value is not None)
    return texts


def _deck_texts(presentation: Any) -> list[str]:
    texts: list[str] = []
    for slide in presentation.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                texts.append(shape.text_frame.text)
    return texts


# --------------------------------------------------------------------------- workbooks


def test_render_workbooks_writes_every_expected_workbook(tmp_path: Path) -> None:
    written = render_workbooks(_sample_data(), tmp_path)
    names = {path.name for path in written}
    assert names == EXPECTED_WORKBOOKS
    for path in written:
        assert path.exists(), f"Missing workbook: {path}"
        load_workbook(path)  # must be a valid xlsx

    assert (tmp_path / "01_Audit_Reports" / "Technical_Audit_Report.xlsx").exists()
    assert (tmp_path / "03_Action_Plan" / "16_Week_Action_Plan.xlsx").exists()
    assert (
        tmp_path / "04_Implementation_Deliverables" / "On_Page_Optimizations"
        / "Title_Tag_Optimizations.xlsx"
    ).exists()


def test_technical_workbook_structure_brand_band_and_severity_fill(tmp_path: Path) -> None:
    render_workbooks(_sample_data(), tmp_path)
    workbook = load_workbook(
        tmp_path / "01_Audit_Reports" / "Technical_Audit_Report.xlsx"
    )
    assert workbook.sheetnames == [
        "Full Site Inventory", "Error Pages", "Redirects", "Canonical Issues",
        "Duplicate Content", "Indexability", "Findings Register", "Methodology",
    ]
    inventory = workbook["Full Site Inventory"]
    assert inventory["A1"].value == "Acme Widgets — Technical Audit Report"
    assert "Run RUN-ACME-001" in str(inventory["A2"].value)
    assert inventory["A4"].value == "URL"
    assert inventory.freeze_panes == "A5"
    assert inventory.auto_filter.ref is not None
    # Word counts are real numbers, not strings.
    assert inventory["F5"].value == 801

    errors = workbook["Error Pages"]
    assert errors["A5"].value == "https://acmewidgets.com.au/shop"
    assert errors["B5"].value == 404

    register = workbook["Findings Register"]
    assert register["C5"].value == "Critical"
    assert register["C5"].fill.start_color.rgb == "FFA15C38"

    methodology = workbook["Methodology"]
    texts = _sheet_texts(methodology)
    assert any("1.0.0" in text for text in texts)
    assert any("2026-07-16" in text for text in texts)


def test_action_plan_exact_columns_and_gantt_span(tmp_path: Path) -> None:
    render_workbooks(_sample_data(), tmp_path)
    workbook = load_workbook(tmp_path / "03_Action_Plan" / "16_Week_Action_Plan.xlsx")
    plan = workbook["Action Plan"]
    headers = [plan.cell(row=4, column=index).value for index in range(1, 14)]
    assert headers == [
        "Phase", "Week", "Task #", "Category", "Description", "Pages/Items",
        "Priority", "Est. Effort", "Owner", "Deliverable", "KPI / Success Metric",
        "Approval Class", "Notes",
    ]
    # A-001 links to the technical finding through EV-0003.
    assert plan.cell(row=5, column=4).value == "Technical"
    # A-005 has no evidence link and falls back to General.
    assert plan.cell(row=9, column=4).value == "General"
    # Priority cells carry the P1 fill.
    assert plan.cell(row=5, column=7).fill.start_color.rgb == "FFA15C38"

    gantt = workbook["Gantt"]
    # A-003 spans weeks 3-6: W3 lives in column 5 (Task #, Action, W1, W2, W3...).
    filled = gantt.cell(row=7, column=5).fill.start_color.rgb
    assert filled == "FF3E4C83"
    outside = gantt.cell(row=7, column=9).fill.start_color.rgb
    assert outside != "FF3E4C83"


def test_empty_sections_render_honest_statements(tmp_path: Path) -> None:
    render_workbooks(_clean_data(), tmp_path)
    workbook = load_workbook(
        tmp_path / "01_Audit_Reports" / "Technical_Audit_Report.xlsx"
    )
    assert workbook["Error Pages"]["A5"].value == EMPTY_MESSAGE
    assert workbook["Redirects"]["A5"].value == EMPTY_MESSAGE
    assert workbook["Duplicate Content"]["A5"].value == EMPTY_MESSAGE

    performance = load_workbook(
        tmp_path / "01_Audit_Reports" / "Performance_And_Tracking_Audit.xlsx"
    )
    assert (
        performance["Response Times"]["A5"].value
        == "Response timing unavailable for this run."
    )

    onpage = load_workbook(tmp_path / "01_Audit_Reports" / "OnPage_Audit_Report.xlsx")
    thin = onpage["Thin Content"]["A5"].value
    assert thin == EMPTY_MESSAGE  # word counts exist and none are thin


def test_formula_injection_is_neutralised(tmp_path: Path) -> None:
    data = _sample_data()
    data["pages"][0]["title"] = "=HYPERLINK('http://evil.example','click')"
    render_workbooks(data, tmp_path)
    workbook = load_workbook(
        tmp_path / "01_Audit_Reports" / "Technical_Audit_Report.xlsx"
    )
    cell = workbook["Full Site Inventory"]["B5"]
    assert cell.data_type != "f"
    assert str(cell.value).startswith("'=")


def test_keyword_workbook_carries_volume_note_and_unavailable_values(
    tmp_path: Path,
) -> None:
    render_workbooks(_sample_data(), tmp_path)
    workbook = load_workbook(
        tmp_path / "02_Strategy_Documents" / "Keyword_And_Topic_Observations.xlsx"
    )
    clusters = workbook["Topic Clusters"]
    texts = _sheet_texts(clusters)
    assert any("GSC or SEMrush" in text for text in texts)
    assert clusters["F5"].value == "Unavailable"  # keyword volume never fabricated


# --------------------------------------------------------------------------- pptx deck


def test_deck_renders_one_slide_per_spec_with_footers(tmp_path: Path) -> None:
    data = _sample_data()
    output = render_deck(data, tmp_path / "07_Executive_Deck" / "Executive_Deck.pptx")
    assert output.exists()
    presentation = Presentation(str(output))
    assert len(presentation.slides) == len(data["deck"]) == 9
    assert presentation.slide_width == Inches(13.333)
    assert presentation.slide_height == Inches(7.5)

    texts = _deck_texts(presentation)
    combined = "\n".join(texts)
    assert "Evidence first. Growth second." in combined
    assert "slide 1/9" in combined
    assert "slide 9/9" in combined
    assert "Acme Widgets · Enterprise SEO Audit" in combined
    assert "None" not in combined


def test_deck_score_slide_renders_withheld_category_with_reason(tmp_path: Path) -> None:
    output = render_deck(_sample_data(), tmp_path / "deck.pptx")
    presentation = Presentation(str(output))
    score_slide = presentation.slides[1]
    slide_text = "\n".join(
        shape.text_frame.text for shape in score_slide.shapes if shape.has_text_frame
    )
    assert "Technical" in slide_text
    assert "Withheld" in slide_text
    assert "Content evidence coverage below threshold" in slide_text


def test_deck_timeline_slide_shows_phases_and_weeks(tmp_path: Path) -> None:
    output = render_deck(_sample_data(), tmp_path / "deck.pptx")
    presentation = Presentation(str(output))
    timeline_slide = presentation.slides[5]
    slide_text = "\n".join(
        shape.text_frame.text for shape in timeline_slide.shapes if shape.has_text_frame
    )
    assert "Foundation" in slide_text
    assert "Measurement" in slide_text
    assert "Weeks 13–16" in slide_text


# --------------------------------------------------------------------------- markdown


def test_markdown_contains_required_sections_and_no_none_literal() -> None:
    markdown = render_markdown(_sample_data())
    assert markdown.startswith("# Acme Widgets — Enterprise SEO Audit Results")
    for header in [
        "## At a glance",
        "## Category scorecard",
        "## Top priority findings",
        "## 16-week action plan overview",
        "## What is in this package",
        "## Data sources & coverage",
        "## Methodology & limitations",
    ]:
        assert header in markdown
    assert "None" not in markdown
    assert "\r" not in markdown
    assert markdown.endswith("\n")
    assert "Generated by Traffic Radius Enterprise SEO Studio · run RUN-ACME-001" in markdown
    # GFM table rows exist.
    assert "| Metric | Value |" in markdown
    assert "| Category | Score | Evidence coverage | Findings |" in markdown
    # Package tree is fenced and lists the ecommerce schema file.
    assert "```" in markdown
    assert "Technical_Audit_Report.xlsx" in markdown
    assert "Schema_Product_Template.json" in markdown
    assert "Schema_LocalBusiness.json" not in markdown  # ecommerce profile only
    assert "CONTENT-01_widget-buying-guide.docx" in markdown


def test_markdown_renders_withheld_scores_with_reason() -> None:
    markdown = render_markdown(_sample_data())
    assert (
        "Withheld — Weighted evidence coverage 55.0% is below the 70% "
        "publication threshold" in markdown
    )
    # The Content category has a null score and must not be coalesced to 0.
    scorecard_row = next(
        line for line in markdown.splitlines() if line.startswith("| Content |")
    )
    cells = [cell.strip() for cell in scorecard_row.strip("|").split("|")]
    assert cells[1] == "Withheld"


def test_markdown_honest_when_run_is_empty() -> None:
    data = _clean_data()
    data["actions"] = []
    data["categories"] = []
    data["content_assets"] = []
    markdown = render_markdown(data)
    assert "No actions were scheduled for this run." in markdown
    assert "No categories were scored in this run" in markdown
    assert "no content assets cleared evidence checks" in markdown
    assert "None" not in markdown
