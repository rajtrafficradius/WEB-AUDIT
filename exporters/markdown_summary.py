"""Root AUDIT_RESULTS.md renderer for the audit deliverable package.

Pure ``dict -> str``: consumes the compiled run-data dictionary and returns
GitHub-flavored Markdown with LF endings and no HTML. Evidence-first: null
scores render as ``Withheld`` with their stated reason, unavailable values
are labelled, and nothing is coalesced to zero.
"""

from __future__ import annotations

from typing import Any

from exporters import paths as tree
from exporters.common import coverage_label, safe_text

UNAVAILABLE = "Unavailable"

_PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}


def _cell(value: Any, fallback: str = UNAVAILABLE) -> str:
    """Render a table cell: escape pipes, never emit a literal ``None``."""
    if isinstance(value, bool):
        text = "Yes" if value else "No"
    elif isinstance(value, int | float):
        text = f"{value:g}"
    else:
        text = safe_text(value, fallback)
    return text.replace("|", "\\|").replace("\n", " ")


def _percent(value: Any) -> str:
    if isinstance(value, int | float):
        return f"{value * 100:.0f}%"
    return UNAVAILABLE


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _severity_breakdown(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "0"
    counts: dict[str, int] = {}
    for finding in findings:
        severity = safe_text(finding.get("severity"), "Unclassified")
        counts[severity] = counts.get(severity, 0) + 1
    order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    parts = [
        f"{counts[name]} {name}"
        for name in sorted(counts, key=lambda item: (order.get(item, 9), item))
    ]
    return f"{len(findings)} ({' · '.join(parts)})"


def _score_text(score: Any, reason: Any) -> str:
    if isinstance(score, int | float):
        return f"{score:g} / 100"
    return f"Withheld — {safe_text(reason, 'no publication reason recorded')}"


def _finding_sort_key(finding: dict[str, Any]) -> tuple[int, float]:
    priority = _PRIORITY_ORDER.get(str(finding.get("priority") or ""), 9)
    score = finding.get("priority_score")
    numeric = float(score) if isinstance(score, int | float) else 0.0
    return (priority, -numeric)


def _category_findings_count(category: dict[str, Any],
                             findings: list[dict[str, Any]]) -> int:
    keys = {
        str(category.get("key") or "").casefold(),
        str(category.get("category") or "").casefold(),
    }
    keys.discard("")
    return sum(
        1 for finding in findings
        if str(finding.get("category") or "").casefold() in keys
    )


def _week_range(actions: list[dict[str, Any]]) -> str:
    weeks: list[int] = []
    for action in actions:
        if isinstance(action.get("week"), int):
            weeks.append(action["week"])
        if isinstance(action.get("week_end"), int):
            weeks.append(action["week_end"])
    if not weeks:
        return "Weeks unscheduled"
    first, last = min(weeks), max(weeks)
    return f"Week {first}" if first == last else f"Weeks {first}–{last}"


def _package_tree(data: dict[str, Any]) -> list[str]:
    profile = str(data.get("project", {}).get("business_profile") or "").casefold()
    backlinks_available = (
        str((data.get("backlinks") or {}).get("status") or "unavailable").casefold()
        == "available"
    )
    lines = [
        f"{tree.SUMMARY_MARKDOWN} — this summary of results, package contents and methodology",
        f"{tree.AUDIT_REPORTS}/",
        "  Technical_Audit_Report.xlsx — crawl inventory, errors, redirects, canonicals,"
        " duplicates, indexability",
        "  Content_Audit_Workbook.xlsx — page-level content inventory, thin pages and"
        " coverage gaps",
        "  Backlink_Audit_Report.xlsx — referring domains and link profile, or the reason"
        " they are unavailable",
        "  Competitor_Landscape_Analysis.xlsx — competitor set with the metrics that were"
        " actually retrieved",
        "  Ecommerce_Audit_Report.xlsx — product, category and checkout observations",
        "  GEO_AEO_Readiness_Scorecard.xlsx — generative and answer-engine readiness checks",
        "  CRO_UX_Findings.xlsx — conversion and usability observations from the crawl",
        "  Tracking_Audit_Report.xlsx — analytics and tag coverage per crawled page",
        "  GBP_Local_Audit.xlsx — local presence and NAP observations",
        "  Baseline_Performance_Analysis.xlsx — the measured baseline this plan is judged against",
        "  Enterprise_SEO_Audit_Report.pdf — the full narrative audit report",
        f"{tree.STRATEGY_DOCUMENTS}/",
        "  Master_Keyword_Universe.xlsx — every retrieved keyword with position, volume,"
        " CPC and mapped URL",
        "  Content_Gap_Analysis.xlsx — clusters where competitors rank and the site does not",
        "  Content_Strategy.xlsx — the prioritised content programme",
        "  URL_Architecture_Map.xlsx — URL inventory with depth and section rollups",
        "  Cannibalization_Resolution_Plan.xlsx — pages competing for the same intent",
        "  SEO_Strategy.docx — the evidence-led 16-week strategy document",
        "  SEO_Strategy.pdf — the same strategy in PDF form",
        f"{tree.ACTION_PLAN}/",
        "  16_Week_Action_Plan.xlsx — the sequenced plan with a week-by-week Gantt",
        "  16_Week_Action_Plan.csv — the same plan for spreadsheet-free import",
        "  16_Week_Action_Plan.pdf — the plan formatted for review and sign-off",
        f"{tree.IMPLEMENTATION}/",
        "  Technical_Fixes/",
        "    Redirect_Map.csv — redirect candidates with approval status",
        "    Redirect_Map.xlsx — the same map with review formatting",
        "    Canonical_Fixes.xlsx — canonical observations and candidates",
        "    Robots_txt_Recommendations.txt — robots and indexation recommendations",
        "  On_Page_Optimizations/",
        "    Title_Tag_Optimizations.xlsx — current vs proposed titles, approval-gated",
        "    Meta_Description_Optimizations.xlsx — current vs proposed descriptions",
        "    H1_Tags.xlsx — current vs proposed H1 headings",
        "    Internal_Link_Map.xlsx — evidence-linked internal link candidates",
        "  Schema_Markup/",
        "    Schema_Organization.json — Organization structured data template",
    ]
    if profile in {"local", "hybrid"}:
        lines.append(
            "    Schema_LocalBusiness.json — LocalBusiness structured data template"
        )
    if profile in {"ecommerce", "hybrid"}:
        lines.append(
            "    Schema_Product_Template.json — Product structured data template"
        )
    if backlinks_available:
        lines.extend([
            "  Link_Building/",
            "    Referring_Domains.xlsx — the retrieved referring-domain profile",
            "    Link_Gap_Opportunities.xlsx — domains linking to competitors but not to you",
        ])
    else:
        lines.append(
            "  (Link_Building/ omitted — no backlink provider data was retrieved for this run)"
        )
    lines.append(f"{tree.SEO_CONTENT}/")
    assets = list(data.get("content_assets") or [])
    if assets:
        for asset in assets:
            asset_id = safe_text(asset.get("id"), "CONTENT")
            slug = safe_text(asset.get("slug"), "asset")
            label = safe_text(asset.get("headline") or asset.get("title"), "content draft")
            lines.append(f"  {asset_id}_{slug}.docx — {label}")
    else:
        lines.append("  (no content assets cleared evidence checks in this run)")
    lines.extend([
        f"{tree.QA}/",
        "  QA_Report.pdf — release gates, reconciliation and QA narrative",
        "  QA_Report.json — the same QA result in machine-readable form",
        "  QC_Report.xlsx — the quality-control checks in workbook form",
        "  availability_matrix.csv — which evidence sources were available",
        "  generation_ledger.csv — every generation task with status and hashes",
        "  evidence_index.csv — the full evidence register",
        "  issue_register.csv — the full findings register",
        "  source_coverage.csv — evidence coverage per audit category",
        "  change_log.csv — provenance for this package build",
        "  package-manifest.json — file inventory for this package",
        "  checksums.sha256 — SHA-256 checksums for every file",
        f"{tree.SLIDE_DECK}/",
        "  Executive_Deck.pptx — the executive presentation",
        "  Executive_Deck.pdf — the same deck in PDF form",
        "  Executive_Deck.html — the same deck as a self-contained web page",
    ])
    return lines


def _market_section(data: dict[str, Any]) -> list[str]:
    """Domain-level market position, or the stated reason it is unavailable."""
    market = data.get("market") or {}
    lines = ["## Market position", ""]
    status = str(market.get("status") or "unavailable").casefold()
    if status != "available":
        reason = safe_text(
            market.get("unavailable_reason"),
            "no market data provider was connected for this run",
        )
        lines.extend([f"Market metrics are {UNAVAILABLE.casefold()} — {reason}", ""])
        return lines
    domain = market.get("domain") or {}
    labels = [
        ("Organic keywords", "organic_keywords"),
        ("Estimated organic traffic", "organic_traffic"),
        ("Estimated organic traffic value", "organic_cost"),
        ("Paid keywords", "adwords_keywords"),
        ("Authority score", "authority_score"),
        ("Backlinks", "backlinks_total"),
        ("Referring domains", "referring_domains"),
    ]
    rows = [[label, _cell(domain.get(key))] for label, key in labels]
    lines.extend(_table(["Metric", "Value"], rows))
    lines.append("")
    lines.append(
        f"Source: {_cell(market.get('provider'))} · database {_cell(market.get('database'))} · "
        f"retrieved {_cell(market.get('fetched_at'))}"
    )
    lines.append("")
    return lines


def _comparison_section(data: dict[str, Any]) -> list[str]:
    """Client vs competitor medians, only for metrics that were actually measured."""
    comparison = data.get("performance_vs_competitors") or {}
    lines = ["## How you compare", ""]
    if str(comparison.get("status") or "unavailable").casefold() != "available":
        reason = safe_text(
            comparison.get("unavailable_reason"),
            "no competitor metrics were retrieved for this run",
        )
        lines.extend([f"Competitor comparison is {UNAVAILABLE.casefold()} — {reason}", ""])
        return lines
    rows = [
        [
            _cell(metric.get("metric")),
            _cell(metric.get("client")),
            _cell(metric.get("competitor_median")),
            _cell(metric.get("best_competitor")),
            _cell(metric.get("best_value")),
            _cell(metric.get("position")),
        ]
        for metric in comparison.get("metrics") or []
    ]
    if not rows:
        lines.extend(["No comparable metrics were retrieved for this run.", ""])
        return lines
    lines.extend(_table(
        ["Metric", "You", "Competitor median", "Best competitor", "Best value", "Standing"],
        rows,
    ))
    summary = comparison.get("summary")
    if summary:
        lines.extend(["", safe_text(summary)])
    lines.append("")
    return lines


def _keyword_section(data: dict[str, Any]) -> list[str]:
    """The largest measured keyword opportunities; never an invented volume."""
    keywords = list(data.get("keywords") or [])
    lines = ["## Keyword opportunities", ""]
    measured = [
        keyword for keyword in keywords
        if isinstance(keyword.get("search_volume"), int | float)
    ]
    if not measured:
        reason = next(
            (
                safe_text(keyword.get("unavailable_reason"), "")
                for keyword in keywords
                if keyword.get("unavailable_reason")
            ),
            "",
        )
        detail = reason or "no keyword provider returned measured search volumes for this run"
        lines.extend([f"Keyword metrics are {UNAVAILABLE.casefold()} — {detail}", ""])
        return lines
    measured.sort(key=lambda keyword: float(keyword["search_volume"]), reverse=True)
    rows = [
        [
            _cell(keyword.get("phrase")),
            _cell(keyword.get("position")),
            _cell(keyword.get("search_volume")),
            _cell(keyword.get("cpc")),
            _cell(keyword.get("funnel_stage")),
            _cell(keyword.get("landing_url")),
        ]
        for keyword in measured[:15]
    ]
    lines.extend(_table(
        ["Keyword", "Position", "Monthly volume", "CPC", "Funnel stage", "Mapped URL"], rows
    ))
    lines.append("")
    lines.append(
        f"Showing the {len(rows)} highest-volume of {len(measured)} keywords with measured volume."
    )
    lines.append("")
    return lines


def _crawl_integrity_section(data: dict[str, Any]) -> list[str]:
    """Only rendered when the crawl was degraded or blocked — silence means clean."""
    integrity = data.get("crawl_integrity") or {}
    status = str(integrity.get("status") or "").casefold()
    if status not in {"degraded", "blocked"}:
        return []
    quarantined = list(integrity.get("quarantined_urls") or [])
    lines = ["## Crawl integrity", ""]
    lines.extend(_table(
        ["Signal", "Value"],
        [
            ["Crawl status", _cell(status)],
            ["Pages fetched", _cell(integrity.get("fetched_pages"))],
            ["Pages challenged", _cell(integrity.get("challenged_pages"))],
            ["Challenge share", _cell(_percent(integrity.get("challenge_share")))],
            ["Rate-limited pages", _cell(integrity.get("rate_limited_pages"))],
            ["Quarantined URLs", _cell(len(quarantined))],
        ],
    ))
    lines.append("")
    lines.append(safe_text(
        integrity.get("note"),
        "Findings drawn from challenged pages are held back until a clean re-crawl.",
    ))
    lines.append("")
    return lines


def render_markdown(data: dict) -> str:
    """Render the root AUDIT_RESULTS.md content for the package."""
    client = data.get("client", {})
    run = data.get("run", {})
    findings = list(data.get("findings") or [])
    actions = list(data.get("actions") or [])
    pages = list(data.get("pages") or [])
    categories = list(data.get("categories") or [])

    lines: list[str] = []
    lines.append(f"# {safe_text(client.get('name'), 'Client')} — Enterprise SEO Audit Results")
    lines.append("")
    lines.append(safe_text(
        data.get("executive_summary"),
        "No executive summary was generated for this run.",
    ))
    lines.append("")

    lines.append("## At a glance")
    lines.append("")
    coverage = run.get("evidence_coverage")
    coverage_text = _percent(coverage)
    if isinstance(coverage, int | float):
        coverage_text += f" — {coverage_label(float(coverage))}"
    lines.extend(_table(
        ["Metric", "Value"],
        [
            ["Pages crawled", _cell(len(pages))],
            ["Findings", _cell(_severity_breakdown(findings))],
            ["Planned actions", _cell(len(actions))],
            ["Evidence coverage", _cell(coverage_text)],
            ["Health score", _cell(_score_text(
                run.get("overall_score"), run.get("overall_score_reason")))],
        ],
    ))
    lines.append("")

    lines.append("## Category scorecard")
    lines.append("")
    category_rows = []
    for category in categories:
        score = category.get("score")
        score_text = f"{score:g}" if isinstance(score, int | float) else "Withheld"
        category_rows.append([
            _cell(category.get("category")),
            _cell(score_text),
            _cell(_percent(category.get("coverage"))),
            _cell(_category_findings_count(category, findings)),
        ])
    if not category_rows:
        category_rows.append(["No categories were scored in this run", "—", "—", "—"])
    lines.extend(_table(["Category", "Score", "Evidence coverage", "Findings"],
                        category_rows))
    lines.append("")

    lines.extend(_market_section(data))
    lines.extend(_comparison_section(data))
    lines.extend(_keyword_section(data))
    lines.extend(_crawl_integrity_section(data))

    lines.append("## Top priority findings")
    lines.append("")
    top_findings = sorted(findings, key=_finding_sort_key)[:10]
    if top_findings:
        for finding in top_findings:
            affected = finding.get("affected_count")
            affected_text = (
                f"{affected:g} affected" if isinstance(affected, int | float)
                else "affected count unavailable"
            )
            description = safe_text(
                finding.get("description"), "No description recorded."
            )
            impact = safe_text(finding.get("impact"), "")
            detail = f"{description} {impact}".strip()
            lines.append(
                f"- **{safe_text(finding.get('title'), 'Untitled finding')}** — "
                f"{safe_text(finding.get('severity'), 'Unclassified')}, "
                f"{affected_text}. {detail}"
            )
    else:
        lines.append("- No findings were raised during the crawl window.")
    lines.append("")

    lines.append("## 16-week action plan overview")
    lines.append("")
    phases: dict[str, list[dict[str, Any]]] = {}
    phase_order: list[str] = []
    for action in actions:
        phase = safe_text(action.get("phase"), "Plan")
        if phase not in phases:
            phases[phase] = []
            phase_order.append(phase)
        phases[phase].append(action)
    if phase_order:
        for phase in phase_order:
            phase_actions = phases[phase]
            lines.append(f"**{phase}** ({_week_range(phase_actions)})")
            lines.append("")
            for action in phase_actions[:3]:
                owner = safe_text(action.get("owner"), "Unassigned")
                lines.append(
                    f"- {safe_text(action.get('action'), 'Unspecified task')} "
                    f"({safe_text(action.get('priority'), 'unprioritised')}, {owner})"
                )
            remaining = len(phase_actions) - 3
            if remaining > 0:
                lines.append(f"- …plus {remaining} further scheduled task(s)")
            lines.append("")
    else:
        lines.append("No actions were scheduled for this run.")
        lines.append("")

    lines.append("## What is in this package")
    lines.append("")
    lines.append("```")
    lines.extend(_package_tree(data))
    lines.append("```")
    lines.append("")

    lines.append("## Data sources & coverage")
    lines.append("")
    source_rows = []
    for source in data.get("sources") or []:
        note = source.get("unavailable_reason") or source.get("scope")
        source_rows.append([
            _cell(source.get("label")),
            _cell(source.get("status")),
            _cell(_percent(source.get("coverage"))),
            _cell(note),
        ])
    if not source_rows:
        source_rows.append(["No sources were registered for this run", "—", "—", "—"])
    lines.extend(_table(["Source", "Status", "Coverage", "Notes"], source_rows))
    lines.append("")

    lines.append("## Methodology & limitations")
    lines.append("")
    lines.append(
        f"- Ruleset version: {safe_text(run.get('rule_version'))}"
    )
    lines.append(
        f"- Configured crawl budget: {_cell(run.get('configured_page_budget'))} pages"
    )
    lines.append(
        f"- Evidence cutoff: {safe_text(run.get('evidence_as_of'))}"
    )
    stopped_reason = run.get("stopped_reason")
    if stopped_reason:
        lines.append(f"- Crawl stop reason: {safe_text(stopped_reason)}")
    interpretation = run.get("coverage_interpretation")
    if interpretation:
        lines.append(f"- Coverage interpretation: {safe_text(interpretation)}")
    limitations = list(data.get("limitations") or [])
    if limitations:
        lines.append("- Limitations:")
        lines.extend(f"  - {safe_text(limitation)}" for limitation in limitations)
    else:
        lines.append("- Limitations: no additional limitations were recorded.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        f"Generated by Traffic Radius Enterprise SEO Studio · run "
        f"{safe_text(run.get('id'))} · {safe_text(run.get('captured_at'))}"
    )
    lines.append("")
    return "\n".join(lines)
