"""Professional XLSX workbook renderers for the audit deliverable package.

Pure ``dict -> files`` renderers built on openpyxl. No Django imports: the
input is the compiled run-data dictionary produced by
``exporters.run_data.compile_run_data`` and the output is the ten-plus
workbooks of the V18-style package tree.

Every sheet follows the house convention: a brand band (rows 1-2), a styled
header row (row 4), zebra-striped data from row 5, frozen panes at A5, an
auto-filter across the header + data range, explicit column widths, and a
closing Methodology sheet. Evidence-first: unavailable values are labelled
``Unavailable`` and empty sections carry an explicit professional statement
instead of a blank grid.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from exporters.common import COPPER, INDIGO, INK, MUTED, PAPER, RULE, WHITE

UNAVAILABLE = "Unavailable"
EMPTY_MESSAGE = "No issues detected in this category during the crawl window."
KEYWORD_VOLUME_NOTE = (
    "Note: keyword search volumes and rankings require an approved GSC or SEMrush "
    "connection. Only crawl-observed topics are listed; no volumes are estimated."
)

# Column width presets by content type.
W_URL = 55
W_TEXT = 45
W_NUM = 12
W_DATE = 14
W_LABEL = 18
W_WEEK = 5

HIGH = "#C08552"
MEDIUM = "#D9B36A"
LOW = "#8FA69E"

SEVERITY_COLORS: dict[str, tuple[str, str]] = {
    "critical": (COPPER, WHITE),
    "p1": (COPPER, WHITE),
    "high": (HIGH, WHITE),
    "p2": (HIGH, WHITE),
    "medium": (MEDIUM, INK),
    "p3": (MEDIUM, INK),
    "low": (LOW, INK),
    "p4": (LOW, INK),
}

ISSUE_COLORS: dict[str, tuple[str, str]] = {
    "missing": (COPPER, WHITE),
    "too long": (MEDIUM, INK),
    "too short": (MEDIUM, INK),
    "multiple captured": (MEDIUM, INK),
    "mismatch": (HIGH, WHITE),
    "review": (LOW, INK),
}


def _argb(color: str) -> str:
    return "FF" + color.lstrip("#").upper()


def _fill(color: str) -> PatternFill:
    return PatternFill(fill_type="solid", start_color=_argb(color), end_color=_argb(color))


_THIN_RULE = Side(style="thin", color=_argb(RULE)[2:])
_BORDER = Border(left=_THIN_RULE, right=_THIN_RULE, top=_THIN_RULE, bottom=_THIN_RULE)


def _guard(value: str) -> str:
    """Formula-injection guard: neutralise strings Excel could execute."""
    if value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _cell_value(value: Any) -> Any:
    """Coerce a raw value for a worksheet cell. Numbers stay numbers."""
    if value is None:
        return UNAVAILABLE
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int | float):
        return value
    if isinstance(value, list | tuple):
        joined = ", ".join(str(item) for item in value)
        return _guard(joined) if joined else ""
    text = str(value).strip()
    if not text:
        return ""
    return _guard(text)


def _severity_token(value: Any) -> str:
    return str(value or "").strip().casefold()


def _add_sheet(
    workbook: Workbook,
    meta: dict[str, str],
    name: str,
    headers: list[str],
    rows: list[list[Any]],
    widths: list[int],
    *,
    wrap_columns: frozenset[int] = frozenset(),
    color_column: int | None = None,
    color_map: dict[str, tuple[str, str]] | None = None,
    empty_message: str = EMPTY_MESSAGE,
    note: str | None = None,
) -> Worksheet:
    """Write one branded sheet: band rows 1-2, header row 4, data from row 5."""
    sheet = workbook.create_sheet(title=name[:31])
    column_count = len(headers)
    last_letter = get_column_letter(column_count)

    # Brand band.
    sheet.merge_cells(f"A1:{last_letter}1")
    sheet.merge_cells(f"A2:{last_letter}2")
    band_fill = _fill(INK)
    for column in range(1, column_count + 1):
        for row in (1, 2):
            cell = sheet.cell(row=row, column=column)
            cell.fill = band_fill
    title_cell = sheet["A1"]
    title_cell.value = _guard(f"{meta['client']} — {meta['title']}")
    title_cell.font = Font(name="Calibri", bold=True, size=14, color=_argb(WHITE)[2:])
    title_cell.alignment = Alignment(vertical="center")
    subtitle_cell = sheet["A2"]
    subtitle_cell.value = _guard(
        f"Prepared by Traffic Radius · Evidence as of {meta['as_of']} · Run {meta['run_id']}"
    )
    subtitle_cell.font = Font(name="Calibri", italic=True, size=9, color=_argb(MUTED)[2:])
    subtitle_cell.alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 28
    sheet.row_dimensions[2].height = 15

    # Header row 4.
    header_fill = _fill(INDIGO)
    for index, header in enumerate(headers, start=1):
        cell = sheet.cell(row=4, column=index, value=_guard(header))
        cell.font = Font(name="Calibri", bold=True, size=11, color=_argb(WHITE)[2:])
        cell.fill = header_fill
        cell.border = _BORDER
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[4].height = 24

    body_rows = rows or [[empty_message] + [""] * (column_count - 1)]
    zebra_fill = _fill(PAPER)
    for row_offset, row_values in enumerate(body_rows):
        row_number = 5 + row_offset
        for column_index in range(1, column_count + 1):
            raw = row_values[column_index - 1] if column_index <= len(row_values) else ""
            cell = sheet.cell(row=row_number, column=column_index, value=_cell_value(raw))
            cell.border = _BORDER
            font_color = _argb(INK)[2:]
            fill = zebra_fill if row_number % 2 == 0 else None
            if color_column is not None and column_index == color_column and color_map:
                tone = color_map.get(_severity_token(raw))
                if tone is not None:
                    fill = _fill(tone[0])
                    font_color = _argb(tone[1])[2:]
            if fill is not None:
                cell.fill = fill
            cell.font = Font(name="Calibri", size=10, color=font_color)
            cell.alignment = Alignment(
                vertical="top",
                wrap_text=(column_index in wrap_columns),
            )

    last_data_row = 4 + len(body_rows)
    sheet.freeze_panes = "A5"
    sheet.auto_filter.ref = f"A4:{last_letter}{last_data_row}"
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width

    if note:
        note_row = last_data_row + 2
        sheet.merge_cells(f"A{note_row}:{last_letter}{note_row}")
        note_cell = sheet.cell(row=note_row, column=1, value=_guard(note))
        note_cell.font = Font(name="Calibri", bold=True, size=10, color=_argb(COPPER)[2:])
        note_cell.alignment = Alignment(vertical="center", wrap_text=True)
        sheet.row_dimensions[note_row].height = 30
    return sheet


def _methodology_rows(data: dict[str, Any], measured: str) -> list[list[Any]]:
    run = data.get("run", {})
    coverage = run.get("evidence_coverage")
    coverage_text = f"{coverage * 100:.0f}%" if isinstance(coverage, int | float) else UNAVAILABLE
    rows: list[list[Any]] = [
        ["What was measured", measured],
        ["Evidence cutoff", run.get("evidence_as_of") or UNAVAILABLE],
        ["Ruleset version", run.get("rule_version") or UNAVAILABLE],
        ["Run", run.get("id") or UNAVAILABLE],
        ["Client domain", data.get("client", {}).get("domain") or UNAVAILABLE],
        ["Configured page budget", run.get("configured_page_budget")],
        ["Weighted evidence coverage", coverage_text],
        [
            "Coverage interpretation",
            run.get("coverage_interpretation") or UNAVAILABLE,
        ],
        [
            "Evidence policy",
            "Canonical crawl evidence only. Unavailable fields are labelled explicitly; "
            "no substitute metrics are invented.",
        ],
    ]
    for limitation in data.get("limitations") or []:
        rows.append(["Limitation", limitation])
    return rows


def _add_methodology(workbook: Workbook, meta: dict[str, str], data: dict[str, Any],
                     measured: str) -> None:
    _add_sheet(
        workbook,
        meta,
        "Methodology",
        ["Field", "Detail"],
        _methodology_rows(data, measured),
        [26, 90],
        wrap_columns=frozenset({2}),
    )


def _meta(data: dict[str, Any], title: str) -> dict[str, str]:
    run = data.get("run", {})
    return {
        "client": str(data.get("client", {}).get("name") or "Client"),
        "title": title,
        "as_of": str(run.get("evidence_as_of") or UNAVAILABLE),
        "run_id": str(run.get("id") or UNAVAILABLE),
    }


def _new_workbook() -> Workbook:
    workbook = Workbook()
    default = workbook.active
    if default is not None:
        workbook.remove(default)
    return workbook


def _save(workbook: Workbook, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


def _findings_for(data: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    wanted = {key.casefold() for key in keys}
    return [
        finding
        for finding in data.get("findings") or []
        if str(finding.get("category") or "").casefold() in wanted
    ]


def _finding_rows(findings: list[dict[str, Any]]) -> list[list[Any]]:
    return [
        [
            finding.get("id"),
            finding.get("priority"),
            finding.get("severity"),
            finding.get("title"),
            finding.get("description"),
            finding.get("impact"),
            finding.get("affected_count"),
            finding.get("confidence"),
            finding.get("effort"),
            finding.get("evidence_ids"),
        ]
        for finding in findings
    ]


FINDING_HEADERS = [
    "Finding ID", "Priority", "Severity", "Title", "Description",
    "Impact", "Affected Count", "Confidence", "Effort", "Evidence IDs",
]
FINDING_WIDTHS = [W_NUM, W_NUM, W_NUM, W_TEXT, W_TEXT, W_TEXT, W_NUM, W_NUM, W_NUM, 24]


def _canonical_state(page: dict[str, Any]) -> str:
    canonical = page.get("canonical_url")
    if not canonical:
        return "Missing"
    normalized = str(page.get("normalized_url") or "")
    if str(canonical).rstrip("/").casefold() == normalized.rstrip("/").casefold():
        return "Match"
    return "Mismatch"


def _url_depth(url: str) -> int:
    return len([segment for segment in urlsplit(url).path.split("/") if segment])


def _pages(data: dict[str, Any]) -> list[dict[str, Any]]:
    return list(data.get("pages") or [])


# --------------------------------------------------------------------------- workbooks


def _technical_audit(data: dict[str, Any], path: Path) -> Path:
    meta = _meta(data, "Technical Audit Report")
    pages = _pages(data)
    workbook = _new_workbook()

    inventory = [
        [
            page.get("normalized_url"),
            page.get("title"),
            page.get("meta_description"),
            page.get("h1"),
            page.get("status_code"),
            page.get("word_count"),
            page.get("canonical_url"),
            page.get("schema_types"),
            page.get("internal_links"),
            page.get("images_total"),
            page.get("images_missing_alt"),
            page.get("page_type"),
        ]
        for page in pages
    ]
    _add_sheet(
        workbook, meta, "Full Site Inventory",
        ["URL", "Title", "Meta Description", "H1", "Status Code", "Word Count",
         "Canonical", "Schema Types", "Internal Links", "Images Total",
         "Images Missing Alt", "Page Type"],
        inventory,
        [W_URL, W_TEXT, W_TEXT, W_TEXT, W_NUM, W_NUM, W_URL, 24, W_NUM, W_NUM, W_NUM, W_LABEL],
        wrap_columns=frozenset({2, 3, 4}),
        empty_message="No pages were captured during the crawl window.",
    )

    errors = [
        [page.get("normalized_url"), page.get("status_code"), page.get("page_type"),
         page.get("indexability"), page.get("evidence_id")]
        for page in pages
        if isinstance(page.get("status_code"), int) and page["status_code"] >= 400
    ]
    _add_sheet(
        workbook, meta, "Error Pages",
        ["URL", "Status Code", "Page Type", "Indexability", "Evidence ID"],
        errors, [W_URL, W_NUM, W_LABEL, 28, W_NUM],
    )

    redirects = [
        [page.get("normalized_url"), page.get("status_code"),
         " → ".join(str(hop) for hop in page.get("redirect_chain") or []),
         len(page.get("redirect_chain") or []), page.get("evidence_id")]
        for page in pages
        if len(page.get("redirect_chain") or []) > 1
    ]
    _add_sheet(
        workbook, meta, "Redirects",
        ["URL", "Status Code", "Redirect Chain", "Hops", "Evidence ID"],
        redirects, [W_URL, W_NUM, W_URL + 20, W_NUM, W_NUM],
        wrap_columns=frozenset({3}),
    )

    canonical_issues = [
        [page.get("normalized_url"), page.get("canonical_url"), _canonical_state(page),
         page.get("page_type"), page.get("evidence_id")]
        for page in pages
        if _canonical_state(page) != "Match"
    ]
    _add_sheet(
        workbook, meta, "Canonical Issues",
        ["URL", "Declared Canonical", "State", "Page Type", "Evidence ID"],
        canonical_issues, [W_URL, W_URL, W_NUM, W_LABEL, W_NUM],
        color_column=3, color_map=ISSUE_COLORS,
    )

    hash_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        digest = page.get("body_sha256")
        if digest:
            hash_groups[str(digest)].append(page)
    duplicates: list[list[Any]] = []
    for digest in sorted(hash_groups):
        group = hash_groups[digest]
        if len(group) > 1:
            for page in group:
                duplicates.append(
                    [page.get("normalized_url"), digest, len(group),
                     page.get("page_type"), page.get("evidence_id")]
                )
    _add_sheet(
        workbook, meta, "Duplicate Content",
        ["URL", "Body SHA-256", "Group Size", "Page Type", "Evidence ID"],
        duplicates, [W_URL, 40, W_NUM, W_LABEL, W_NUM],
    )

    def _indexability_blocked(page: dict[str, Any]) -> bool:
        value = page.get("indexability")
        if value is False:
            return True
        text = str(value or "").casefold()
        return "noindex" in text and "no noindex" not in text

    indexability = [
        [page.get("normalized_url"), page.get("indexability"), page.get("status_code"),
         page.get("page_type"), page.get("evidence_id")]
        for page in pages
        if _indexability_blocked(page)
    ]
    _add_sheet(
        workbook, meta, "Indexability",
        ["URL", "Indexability Signal", "Status Code", "Page Type", "Evidence ID"],
        indexability, [W_URL, 34, W_NUM, W_LABEL, W_NUM],
        empty_message="No noindex or robots exclusions were observed during the crawl window.",
    )

    _add_sheet(
        workbook, meta, "Findings Register",
        FINDING_HEADERS, _finding_rows(_findings_for(data, "technical")),
        FINDING_WIDTHS, wrap_columns=frozenset({4, 5, 6}),
        color_column=3, color_map=SEVERITY_COLORS,
    )
    _add_methodology(
        workbook, meta, data,
        "HTTP status, redirect chains, canonical declarations, duplicate body hashes and "
        "indexability signals for every page fetched in the approved-domain crawl.",
    )
    return _save(workbook, path)


def _metadata_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    deployment = data.get("deployment") or {}
    return list(deployment.get("metadata_review") or [])


def _onpage_audit(data: dict[str, Any], path: Path) -> Path:
    meta = _meta(data, "On-Page Audit Report")
    pages = _pages(data)
    review = _metadata_rows(data)
    workbook = _new_workbook()

    def _issue_rows(issue_key: str, current_key: str, length_key: str | None,
                    proposed_key: str) -> list[list[Any]]:
        rows: list[list[Any]] = []
        for entry in review:
            issue = str(entry.get(issue_key) or "").strip()
            if not issue or issue.casefold() == "ok":
                continue
            row: list[Any] = [entry.get("url"), entry.get("page_type"),
                              entry.get(current_key)]
            if length_key is not None:
                row.append(entry.get(length_key))
            row.extend([issue, entry.get(proposed_key), entry.get("priority"),
                        entry.get("approval_status")])
            rows.append(row)
        return rows

    _add_sheet(
        workbook, meta, "Title Issues",
        ["URL", "Page Type", "Current Title", "Length", "Issue", "Proposed Title",
         "Priority", "Approval Status"],
        _issue_rows("title_issue", "current_title", "title_length", "proposed_title"),
        [W_URL, W_LABEL, W_TEXT, W_NUM, W_NUM, W_TEXT, W_NUM, 26],
        wrap_columns=frozenset({3, 6}), color_column=5, color_map=ISSUE_COLORS,
    )
    _add_sheet(
        workbook, meta, "Meta Description Issues",
        ["URL", "Page Type", "Current Meta Description", "Length", "Issue",
         "Proposed Meta Description", "Priority", "Approval Status"],
        _issue_rows("meta_description_issue", "current_meta_description",
                    "meta_description_length", "proposed_meta_description"),
        [W_URL, W_LABEL, W_TEXT, W_NUM, W_NUM, W_TEXT, W_NUM, 26],
        wrap_columns=frozenset({3, 6}), color_column=5, color_map=ISSUE_COLORS,
    )
    _add_sheet(
        workbook, meta, "H1 Issues",
        ["URL", "Page Type", "Current H1", "Issue", "Proposed H1", "Priority",
         "Approval Status"],
        _issue_rows("h1_issue", "current_h1", None, "proposed_h1"),
        [W_URL, W_LABEL, W_TEXT, W_NUM, W_TEXT, W_NUM, 26],
        wrap_columns=frozenset({3, 5}), color_column=4, color_map=ISSUE_COLORS,
    )

    counted_pages = [page for page in pages if isinstance(page.get("word_count"), int)]
    thin = [
        [page.get("normalized_url"), page.get("word_count"), page.get("page_type"),
         page.get("title"), page.get("evidence_id")]
        for page in sorted(counted_pages, key=lambda item: item.get("word_count") or 0)
        if (page.get("word_count") or 0) < 250
    ]
    _add_sheet(
        workbook, meta, "Thin Content",
        ["URL", "Word Count", "Page Type", "Title", "Evidence ID"],
        thin, [W_URL, W_NUM, W_LABEL, W_TEXT, W_NUM],
        wrap_columns=frozenset({4}),
        empty_message=(
            EMPTY_MESSAGE if counted_pages
            else "Word counts were unavailable for this run; thin content was not assessed."
        ),
        note="Threshold: pages under 250 observed body words are flagged for editorial review.",
    )

    alt_pages = [page for page in pages if isinstance(page.get("images_missing_alt"), int)]
    alt_issues = [
        [page.get("normalized_url"), page.get("images_total"),
         page.get("images_missing_alt"), page.get("page_type"), page.get("evidence_id")]
        for page in alt_pages
        if (page.get("images_missing_alt") or 0) > 0
    ]
    _add_sheet(
        workbook, meta, "Image Alt Issues",
        ["URL", "Images Total", "Images Missing Alt", "Page Type", "Evidence ID"],
        alt_issues, [W_URL, W_NUM, W_NUM, W_LABEL, W_NUM],
        empty_message=(
            EMPTY_MESSAGE if alt_pages
            else "Image inventories were unavailable for this run; alt coverage was not assessed."
        ),
    )

    _add_sheet(
        workbook, meta, "On-Page Findings",
        FINDING_HEADERS,
        _finding_rows(_findings_for(data, "on_page", "onpage", "on-page", "content")),
        FINDING_WIDTHS, wrap_columns=frozenset({4, 5, 6}),
        color_column=3, color_map=SEVERITY_COLORS,
    )
    _add_methodology(
        workbook, meta, data,
        "Title, meta description and H1 quality against length and duplication rules, "
        "plus word-count and image alt coverage where the crawler captured them.",
    )
    return _save(workbook, path)


def _performance_audit(data: dict[str, Any], path: Path) -> Path:
    meta = _meta(data, "Performance & Tracking Audit")
    pages = _pages(data)
    workbook = _new_workbook()

    timed = [page for page in pages if isinstance(page.get("response_ms"), int | float)]
    response_rows = [
        [page.get("normalized_url"), page.get("response_ms"), page.get("status_code"),
         page.get("page_type"), page.get("evidence_id")]
        for page in sorted(timed, key=lambda item: item.get("response_ms") or 0, reverse=True)
    ]
    _add_sheet(
        workbook, meta, "Response Times",
        ["URL", "Response (ms)", "Status Code", "Page Type", "Evidence ID"],
        response_rows, [W_URL, W_NUM, W_NUM, W_LABEL, W_NUM],
        empty_message="Response timing unavailable for this run.",
    )

    weighed = [page for page in pages if isinstance(page.get("body_bytes"), int | float)]
    weight_rows = [
        [page.get("normalized_url"), page.get("body_bytes"),
         round((page.get("body_bytes") or 0) / 1024, 1), page.get("page_type"),
         page.get("evidence_id")]
        for page in sorted(weighed, key=lambda item: item.get("body_bytes") or 0, reverse=True)
    ]
    _add_sheet(
        workbook, meta, "Page Weight",
        ["URL", "Body Bytes", "Body KB", "Page Type", "Evidence ID"],
        weight_rows, [W_URL, W_NUM, W_NUM, W_LABEL, W_NUM],
        empty_message="Page weight unavailable for this run.",
    )

    tagged = [page for page in pages if "analytics_tags" in page]
    if tagged:
        analytics_rows = [
            [page.get("normalized_url"),
             ", ".join(page.get("analytics_tags") or []) or "No tags detected",
             "Yes" if page.get("analytics_tags") else "No",
             page.get("page_type")]
            for page in tagged
        ]
        analytics_empty = EMPTY_MESSAGE
    else:
        analytics_rows = [
            [finding.get("title"), finding.get("description"), finding.get("severity"),
             finding.get("reach")]
            for finding in _findings_for(data, "analytics", "tracking", "performance")
        ]
        analytics_empty = (
            "Per-page analytics detection was unavailable for this run and no "
            "analytics findings were raised."
        )
    _add_sheet(
        workbook, meta, "Analytics Tag Coverage",
        (["URL", "Analytics Tags", "Detected", "Page Type"] if tagged
         else ["Finding", "Description", "Severity", "Reach"]),
        analytics_rows,
        ([W_URL, W_TEXT, W_NUM, W_LABEL] if tagged else [W_TEXT, W_TEXT, W_NUM, W_LABEL]),
        wrap_columns=frozenset({2}),
        empty_message=analytics_empty,
    )

    _add_sheet(
        workbook, meta, "Perf & Analytics Findings",
        FINDING_HEADERS,
        _finding_rows(
            _findings_for(data, "performance", "analytics", "tracking", "performance_tracking")
        ),
        FINDING_WIDTHS, wrap_columns=frozenset({4, 5, 6}),
        color_column=3, color_map=SEVERITY_COLORS,
    )
    _add_methodology(
        workbook, meta, data,
        "Server response timing, HTML payload weight and analytics tag presence as "
        "observed by the crawler. Lab performance metrics (Core Web Vitals) require a "
        "PageSpeed connection and are not simulated.",
    )
    return _save(workbook, path)


def _keyword_observations(data: dict[str, Any], path: Path) -> Path:
    meta = _meta(data, "Keyword & Topic Observations")
    workbook = _new_workbook()

    clusters = [
        [opp.get("id"), opp.get("cluster"), opp.get("intent"), opp.get("target_url"),
         opp.get("decision"), opp.get("keyword_volume"), opp.get("ranking"),
         opp.get("unavailable_reason"), opp.get("evidence_ids")]
        for opp in data.get("opportunities") or []
    ]
    _add_sheet(
        workbook, meta, "Topic Clusters",
        ["ID", "Cluster", "Intent", "Target URL", "Decision", "Keyword Volume",
         "Ranking", "Unavailable Reason", "Evidence IDs"],
        clusters,
        [W_NUM, W_TEXT, 24, W_URL, W_TEXT, W_NUM, W_NUM, W_TEXT, 22],
        wrap_columns=frozenset({2, 5, 8}),
        empty_message="No topic opportunities cleared evidence checks during this run.",
        note=KEYWORD_VOLUME_NOTE,
    )
    _add_sheet(
        workbook, meta, "Cannibalization Signals",
        FINDING_HEADERS,
        _finding_rows(
            _findings_for(data, "keyword_architecture", "cannibalization", "keywords")
        ),
        FINDING_WIDTHS, wrap_columns=frozenset({4, 5, 6}),
        color_column=3, color_map=SEVERITY_COLORS,
    )
    _add_methodology(
        workbook, meta, data,
        "Topic clusters and overlap signals derived from crawl-observed pages and "
        "internal architecture. Search volumes and rankings are withheld until GSC or "
        "SEMrush evidence is connected — none are estimated.",
    )
    return _save(workbook, path)


def _url_architecture(data: dict[str, Any], path: Path) -> Path:
    meta = _meta(data, "URL Architecture Map")
    pages = _pages(data)
    workbook = _new_workbook()

    url_map = [
        [page.get("normalized_url"), page.get("page_type"),
         _url_depth(str(page.get("normalized_url") or "")), page.get("status_code"),
         page.get("internal_links"), page.get("title")]
        for page in pages
    ]
    _add_sheet(
        workbook, meta, "URL Map",
        ["URL", "Page Type", "Depth", "Status", "Internal Links", "Title"],
        url_map, [W_URL, W_LABEL, W_NUM, W_NUM, W_NUM, W_TEXT],
        wrap_columns=frozenset({6}),
        empty_message="No pages were captured during the crawl window.",
    )

    sections: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        sections[str(page.get("page_type") or "Other")].append(page)
    rollup: list[list[Any]] = []
    for section in sorted(sections):
        group = sections[section]
        links = [p.get("internal_links") for p in group
                 if isinstance(p.get("internal_links"), int | float)]
        depths = [_url_depth(str(p.get("normalized_url") or "")) for p in group]
        rollup.append([
            section, len(group),
            round(sum(links) / len(links), 1) if links else UNAVAILABLE,
            round(sum(depths) / len(depths), 1) if depths else UNAVAILABLE,
        ])
    _add_sheet(
        workbook, meta, "Sections",
        ["Page Type", "Pages", "Avg Internal Links", "Avg Depth"],
        rollup, [W_LABEL + 8, W_NUM, W_NUM + 6, W_NUM],
        empty_message="No pages were captured during the crawl window.",
    )
    _add_methodology(
        workbook, meta, data,
        "Normalized URL inventory with path depth, observed status and internal link "
        "counts, rolled up by classified page type.",
    )
    return _save(workbook, path)


ACTION_PLAN_HEADERS = [
    "Phase", "Week", "Task #", "Category", "Description", "Pages/Items", "Priority",
    "Est. Effort", "Owner", "Deliverable", "KPI / Success Metric", "Approval Class", "Notes",
]


def _category_label(raw: str) -> str:
    return raw.replace("_", " ").replace("-", " ").strip().title() or "General"


def _action_plan(data: dict[str, Any], path: Path) -> Path:
    meta = _meta(data, "16-Week Action Plan")
    actions = list(data.get("actions") or [])
    workbook = _new_workbook()

    finding_by_evidence: dict[str, dict[str, Any]] = {}
    for finding in data.get("findings") or []:
        for evidence_id in finding.get("evidence_ids") or []:
            finding_by_evidence.setdefault(str(evidence_id), finding)

    plan_rows: list[list[Any]] = []
    for action in actions:
        matched: dict[str, Any] | None = None
        for evidence_id in action.get("evidence_ids") or []:
            matched = finding_by_evidence.get(str(evidence_id))
            if matched is not None:
                break
        explicit = str(action.get("category") or "").strip()
        category = explicit or (
            _category_label(str(matched.get("category") or "")) if matched else "General"
        )
        week = action.get("week")
        week_end = action.get("week_end") or week
        week_label = (
            f"W{week}" if week == week_end or week_end is None else f"W{week}-W{week_end}"
        )
        plan_rows.append([
            action.get("phase"),
            week_label if week is not None else None,
            action.get("id"),
            category,
            action.get("action"),
            (matched or {}).get("affected_count"),
            action.get("priority"),
            action.get("effort"),
            action.get("owner"),
            action.get("deliverable") or "Unavailable - not specified in the plan record",
            action.get("kpi"),
            action.get("approval_class"),
            action.get("notes"),
        ])
    _add_sheet(
        workbook, meta, "Action Plan",
        ACTION_PLAN_HEADERS, plan_rows,
        [16, 10, W_NUM, 20, W_TEXT, W_NUM, W_NUM, W_NUM, 20, 30, 34, 20, W_TEXT],
        wrap_columns=frozenset({5, 10, 11, 13}),
        color_column=7, color_map=SEVERITY_COLORS,
        empty_message="No actions were scheduled for this run.",
    )

    # Gantt sheet.
    gantt_headers = ["Task #", "Action"] + [f"W{week}" for week in range(1, 17)]
    gantt_rows = [
        [action.get("id"), action.get("action")] + [""] * 16 for action in actions
    ]
    sheet = _add_sheet(
        workbook, meta, "Gantt",
        gantt_headers, gantt_rows,
        [W_NUM, W_TEXT] + [W_WEEK] * 16,
        wrap_columns=frozenset({2}),
        empty_message="No actions were scheduled for this run.",
    )
    bar_fill = _fill(INDIGO)
    for row_offset, action in enumerate(actions):
        week = action.get("week")
        if not isinstance(week, int):
            continue
        start = max(1, min(16, week))
        end_raw = action.get("week_end")
        end = max(start, min(16, end_raw)) if isinstance(end_raw, int) else start
        for week_number in range(start, end + 1):
            sheet.cell(row=5 + row_offset, column=2 + week_number).fill = bar_fill

    _add_methodology(
        workbook, meta, data,
        "The canonical 16-week action sequence compiled from evidence-linked findings. "
        "Categories join each task to its source finding; unmatched tasks are General.",
    )
    return _save(workbook, path)


def _metadata_workbook(data: dict[str, Any], path: Path, *, kind: str) -> Path:
    review = _metadata_rows(data)
    workbook = _new_workbook()
    if kind == "title":
        meta = _meta(data, "Title Tag Optimizations")
        headers = ["URL", "Page Type", "Current Title", "Length", "Issue",
                   "Proposed Title", "Approval Status"]
        rows = [
            [entry.get("url"), entry.get("page_type"), entry.get("current_title"),
             entry.get("title_length"), entry.get("title_issue"),
             entry.get("proposed_title"), entry.get("approval_status")]
            for entry in review
        ]
        widths = [W_URL, W_LABEL, W_TEXT, W_NUM, W_NUM, W_TEXT, 26]
        issue_column = 5
        sheet_name = "Title Tags"
        measured = ("Current versus proposed title tags for every reviewed page; issues "
                    "are highlighted and every proposal awaits editorial approval.")
    elif kind == "meta":
        meta = _meta(data, "Meta Description Optimizations")
        headers = ["URL", "Page Type", "Current Meta Description", "Length", "Issue",
                   "Proposed Meta Description", "Approval Status"]
        rows = [
            [entry.get("url"), entry.get("page_type"),
             entry.get("current_meta_description"), entry.get("meta_description_length"),
             entry.get("meta_description_issue"),
             entry.get("proposed_meta_description"), entry.get("approval_status")]
            for entry in review
        ]
        widths = [W_URL, W_LABEL, W_TEXT, W_NUM, W_NUM, W_TEXT, 26]
        issue_column = 5
        sheet_name = "Meta Descriptions"
        measured = ("Current versus proposed meta descriptions for every reviewed page; "
                    "issues are highlighted and proposals await editorial approval.")
    else:
        meta = _meta(data, "H1 Optimizations")
        headers = ["URL", "Page Type", "Current H1", "Issue", "Proposed H1",
                   "Approval Status"]
        rows = [
            [entry.get("url"), entry.get("page_type"), entry.get("current_h1"),
             entry.get("h1_issue"), entry.get("proposed_h1"),
             entry.get("approval_status")]
            for entry in review
        ]
        widths = [W_URL, W_LABEL, W_TEXT, W_NUM, W_TEXT, 26]
        issue_column = 4
        sheet_name = "H1 Headings"
        measured = ("Current versus proposed H1 headings for every reviewed page; issues "
                    "are highlighted and proposals await editorial approval.")

    _add_sheet(
        workbook, meta, sheet_name, headers, rows, widths,
        wrap_columns=frozenset({3, issue_column + 1}),
        color_column=issue_column, color_map=ISSUE_COLORS,
        empty_message="No metadata review rows were produced for this run.",
    )
    _add_methodology(workbook, meta, data, measured)
    return _save(workbook, path)


def _canonical_review(data: dict[str, Any], path: Path) -> Path:
    meta = _meta(data, "Canonical Review")
    deployment = data.get("deployment") or {}
    workbook = _new_workbook()

    rows: list[list[Any]] = []
    for candidate in deployment.get("canonical_candidates") or []:
        rows.append([
            candidate.get("url") or candidate.get("source_url"),
            candidate.get("current_canonical") or candidate.get("canonical_url"),
            candidate.get("proposed_canonical") or candidate.get("target_url"),
            candidate.get("reason") or candidate.get("issue"),
            "Deployment candidate",
            candidate.get("approval_status"),
            candidate.get("evidence_id") or candidate.get("evidence_ids"),
        ])
    for page in _pages(data):
        state = _canonical_state(page)
        if state == "Match":
            continue
        rows.append([
            page.get("normalized_url"), page.get("canonical_url"), None,
            f"Canonical {state.casefold()} observed in crawl", "Crawl observation",
            "review_required", page.get("evidence_id"),
        ])
    _add_sheet(
        workbook, meta, "Canonical Review",
        ["URL", "Current Canonical", "Proposed Canonical", "Issue", "Source",
         "Approval Status", "Evidence"],
        rows, [W_URL, W_URL, W_URL, W_TEXT, W_LABEL + 4, 24, W_NUM],
        wrap_columns=frozenset({4}),
        empty_message="All observed canonicals were consistent during the crawl window.",
    )
    _add_methodology(
        workbook, meta, data,
        "Canonical declarations compared against normalized URLs, plus any deployment "
        "candidates. Every change requires administrator approval before implementation.",
    )
    return _save(workbook, path)


def _internal_link_map(data: dict[str, Any], path: Path) -> Path:
    meta = _meta(data, "Internal Link Map")
    deployment = data.get("deployment") or {}
    workbook = _new_workbook()
    rows = [
        [candidate.get("source_url"), candidate.get("target_url"), candidate.get("anchor"),
         candidate.get("rationale"), candidate.get("link_type"),
         candidate.get("observed_status"), candidate.get("approval_status"),
         candidate.get("evidence_ids")]
        for candidate in deployment.get("internal_link_candidates") or []
    ]
    _add_sheet(
        workbook, meta, "Internal Link Map",
        ["Source", "Target", "Anchor", "Rationale", "Link Type", "Observed",
         "Approval", "Evidence IDs"],
        rows, [W_URL, W_URL, W_TEXT, W_TEXT, 24, W_LABEL, 22, 22],
        wrap_columns=frozenset({3, 4}),
        empty_message="No internal link candidates cleared evidence checks during this run.",
    )
    _add_methodology(
        workbook, meta, data,
        "Internal link candidates derived from crawl-observed relationships. Anchors and "
        "targets were observed on the approved domain; additions await approval.",
    )
    return _save(workbook, path)


def render_workbooks(data: dict, package_root: Path) -> list[Path]:
    """Render every XLSX workbook of the package tree. Returns written paths."""
    package_root = Path(package_root)
    reports = package_root / "01_Audit_Reports"
    strategy = package_root / "02_Strategy_Documents"
    plan = package_root / "03_Action_Plan"
    onpage = package_root / "04_Implementation_Deliverables" / "On_Page_Optimizations"
    technical = package_root / "04_Implementation_Deliverables" / "Technical_Fixes"
    linking = package_root / "04_Implementation_Deliverables" / "Internal_Linking"

    return [
        _technical_audit(data, reports / "Technical_Audit_Report.xlsx"),
        _onpage_audit(data, reports / "OnPage_Audit_Report.xlsx"),
        _performance_audit(data, reports / "Performance_And_Tracking_Audit.xlsx"),
        _keyword_observations(data, strategy / "Keyword_And_Topic_Observations.xlsx"),
        _url_architecture(data, strategy / "URL_Architecture_Map.xlsx"),
        _action_plan(data, plan / "16_Week_Action_Plan.xlsx"),
        _metadata_workbook(data, onpage / "Title_Tag_Optimizations.xlsx", kind="title"),
        _metadata_workbook(
            data, onpage / "Meta_Description_Optimizations.xlsx", kind="meta"
        ),
        _metadata_workbook(data, onpage / "H1_Optimizations.xlsx", kind="h1"),
        _canonical_review(data, technical / "Canonical_Review.xlsx"),
        _internal_link_map(data, linking / "Internal_Link_Map.xlsx"),
    ]
