"""V18-compatible, audit-specific workbook specifications."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any
from urllib.parse import urlsplit

NOTE = "Canonical evidence only; unavailable fields are explicit and no substitute metrics are invented."


def vals(items, keys):
    out = []
    for item in items:
        row = []
        for key in keys:
            value = item.get(key)
            if isinstance(value, list):
                value = ", ".join(map(str, value))
            row.append("Unavailable" if value is None else value)
        out.append(row)
    return out


def sheet(name, headers, rows, note=NOTE, visual=None, priority=None):
    return {
        "name": name[:31],
        "headers": headers,
        "rows": rows
        or [["UNAVAILABLE"] + ["No supported rows were available"] * (len(headers) - 1)],
        "widths": [max(13, min(50, len(h) * 1.7 + 7)) for h in headers],
        "note": note,
        "visual_summary": visual,
        "priority_column": priority,
    }


def unavailable(name, headers, reason):
    return sheet(
        name, headers, [["UNAVAILABLE"] + [reason] * (len(headers) - 1)], f"UNAVAILABLE: {reason}"
    )


def spec(
    path,
    title,
    sheets,
    data,
    status="EVIDENCE-LINKED",
    decision="Review and approve before implementation.",
    approval=False,
):
    return {
        "path": path,
        "title": title,
        "subtitle": "Traffic Radius enterprise SEO - V18-compatible v19 evidence edition",
        "sheets": sheets,
        "as_of": data["run"]["evidence_as_of"],
        "domain": data["client"]["domain"],
        "run_id": data["run"]["id"],
        "status": status,
        "decision": decision,
        "evidence_linked": True,
        "approval_required": approval,
    }


def ptype(p):
    return p.get("page_type") or "Other"


def ti(p):
    v = p.get("title") or ""
    return (
        "Missing" if not v else "Too short" if len(v) < 25 else "Too long" if len(v) > 60 else "OK"
    )


def mi(p):
    v = p.get("meta_description") or ""
    return (
        "Missing" if not v else "Too short" if len(v) < 70 else "Too long" if len(v) > 160 else "OK"
    )


def hi(p):
    v = p.get("h1") or ""
    return "Missing" if not v else "Multiple captured" if " | " in v else "OK"


def cm(p):
    v = p.get("canonical_url")
    if not v:
        return "Missing"
    return (
        "Yes"
        if str(v).rstrip("/").casefold() == p["normalized_url"].rstrip("/").casefold()
        else "Mismatch"
    )


def inventory(p):
    return [
        p["normalized_url"],
        p.get("title") or "Unavailable",
        p.get("meta_description") or "Unavailable",
        p.get("h1") or "Unavailable",
        p.get("status_code") or "Unavailable",
        "Unavailable - word count not captured",
        p.get("canonical_url") or "Unavailable",
        "Unavailable - schema extraction not supplied",
        p.get("internal_links", 0),
        "Unavailable - external links not captured",
        "Unavailable - image inventory not supplied",
        "Unavailable - image inventory not supplied",
        ptype(p),
        len(p.get("title") or ""),
        len(p.get("meta_description") or ""),
        ti(p),
        mi(p),
        hi(p),
        "Not assessed - extraction unavailable",
        "Not assessed - image inventory unavailable",
    ]


def duplicate_rows(pages):
    out = []
    for field, label in (
        ("title", "Duplicate title"),
        ("meta_description", "Duplicate meta description"),
        ("h1", "Duplicate H1"),
    ):
        groups = defaultdict(list)
        for p in pages:
            value = str(p.get(field) or "").strip().casefold()
            if value:
                groups[value].append(p)
        for records in groups.values():
            if len(records) > 1:
                for p in records:
                    out.append(
                        [
                            p["normalized_url"],
                            label,
                            p.get(field) or "Unavailable",
                            "Unavailable",
                            ptype(p),
                            p["evidence_id"],
                        ]
                    )
    return out


def qa_rows(data):
    out = []
    domain = data["client"]["domain"]
    for p in data["pages"]:
        host = (urlsplit(p["normalized_url"]).hostname or "").casefold()
        status = int(p.get("status_code") or 0)
        out.append(
            [
                "Domain safety",
                f"Approved host - {p['id']}",
                "PASS" if host == domain or host.endswith("." + domain) else "FAIL",
                p["normalized_url"],
                p["evidence_id"],
            ]
        )
        out.append(
            [
                "HTTP",
                f"Captured status - {p['id']}",
                "PASS" if 200 <= status < 400 else "REVIEW",
                f"HTTP {status}; {p['normalized_url']}",
                p["evidence_id"],
            ]
        )
        out.append(
            [
                "Evidence",
                f"Lineage - {p['id']}",
                "PASS",
                f"Resolves to {p['evidence_id']}",
                p["evidence_id"],
            ]
        )
    for g in data["qa"]["gates"]:
        out.append(["Release gate", g["name"], g["status"], g["evidence"], g["id"]])
    return out


def workbook_specs(data: dict[str, Any]) -> list[dict[str, Any]]:
    pages = data["pages"]
    findings = data["findings"]
    sources = data["sources"]
    opps = data["opportunities"]
    actions = data["actions"]
    content = data["content_assets"]
    dep = data["deployment"]
    source = {s["kind"]: s for s in sources}
    reason = (
        "Connect and validate the required provider before publishing metrics, scores or targets."
    )
    invh = [
        "URL",
        "Title",
        "Meta Description",
        "H1",
        "Status Code",
        "Word Count",
        "Canonical",
        "Schema Types",
        "Internal Links",
        "External Links",
        "Images Total",
        "Images Missing Alt",
        "Page Type",
        "Title Length",
        "Meta Desc Length",
        "Title Issue",
        "Meta Desc Issue",
        "H1 Issue",
        "Schema Issue",
        "Alt Text Issue",
    ]
    inv = [inventory(p) for p in pages]
    errors = [r for p, r in zip(pages, inv, strict=True) if p.get("status_code") != 200]
    title = [
        [
            p["normalized_url"],
            p.get("title") or "Unavailable",
            len(p.get("title") or ""),
            ti(p),
            p.get("status_code"),
            ptype(p),
            p["evidence_id"],
        ]
        for p in pages
        if ti(p) != "OK"
    ]
    meta = [
        [
            p["normalized_url"],
            p.get("meta_description") or "Unavailable",
            len(p.get("meta_description") or ""),
            mi(p),
            p.get("status_code"),
            ptype(p),
            p["evidence_id"],
        ]
        for p in pages
        if mi(p) != "OK"
    ]
    h1 = [
        [
            p["normalized_url"],
            p.get("h1") or "Unavailable",
            hi(p),
            p.get("status_code"),
            ptype(p),
            p["evidence_id"],
        ]
        for p in pages
        if hi(p) != "OK"
    ]
    canonical = [
        [
            p["normalized_url"],
            p.get("canonical_url") or "Unavailable",
            cm(p),
            ptype(p),
            p["evidence_id"],
            "observed" if cm(p) == "Yes" else "withheld_pending_agency_admin",
        ]
        for p in pages
    ]
    dup = duplicate_rows(pages)
    products = [p for p in pages if ptype(p) == "Product"]
    collections = [p for p in pages if ptype(p) == "Collection"]
    mrows = dep["metadata_review"]
    links = vals(
        dep["internal_link_candidates"],
        [
            "source_url",
            "anchor",
            "target_url",
            "link_type",
            "approval_status",
            "observed_status",
            "rationale",
            "evidence_ids",
        ],
    )
    h1opt = vals(
        mrows,
        [
            "url",
            "current_h1",
            "h1_issue",
            "proposed_h1",
            "target_keyword",
            "priority",
            "evidence_id",
            "approval_status",
        ],
    )
    metaopt = vals(
        mrows,
        [
            "url",
            "current_meta_description",
            "proposed_meta_description",
            "meta_description_issue",
            "meta_description_length",
            "priority",
            "evidence_id",
            "approval_status",
        ],
    )
    titleopt = vals(
        mrows,
        [
            "url",
            "current_title",
            "proposed_title",
            "title_issue",
            "title_length",
            "priority",
            "evidence_id",
            "approval_status",
        ],
    )
    redirects = vals(
        dep["redirect_candidates"],
        [
            "source_url",
            "target_url",
            "status_code",
            "reason",
            "evidence_id",
            "approval_status",
            "included_in_deployment",
        ],
    )
    gaps = [
        [
            o["cluster"],
            "Unavailable",
            "Evidence-backed refresh",
            o["intent"],
            o["target_url"],
            "Existing page",
            o["decision"],
            ", ".join(o["evidence_ids"]),
        ]
        for o in opps
    ]
    cannibal = [
        [
            "Target uniqueness",
            f"{len({o['target_url'] for o in opps})} unique targets / {len(opps)} opportunities",
            "PASS",
            "P1",
            "Canonical opportunity map",
        ]
    ] + [[r[1], r[2], "REVIEW", "P2", r[0]] for r in dup[:200]]
    baseline = [
        ["Unique normalized pages", len(pages), "Crawl", "Deduplicated"],
        ["Successful pages", sum(p.get("status_code") == 200 for p in pages), "Crawl", "HTTP 200"],
        [
            "Non-200 pages",
            sum(p.get("status_code") != 200 for p in pages),
            "Crawl",
            "Disposition required",
        ],
        ["Findings", len(findings), "Rules engine", "Aggregated; page-level rows retained"],
        ["Actions", len(actions), "Planning", "Evidence-linked"],
        ["Content assets", len(content), "Content map", "Unique targets"],
        [
            "Internal-link rows",
            len(dep["internal_link_candidates"]),
            "Crawl graph",
            "Observed and review candidates",
        ],
        ["Metadata rows", len(mrows), "On-page", "Successful non-utility pages"],
        [
            "Evidence coverage",
            data["run"]["evidence_coverage"],
            "Scorecard",
            "Overall score withheld below 70%",
        ],
        ["Wrong-domain URLs", data["qa"]["wrong_domain_urls"], "QA", "Must be zero"],
        ["GSC baseline", "Unavailable", "GSC", source["gsc"]["unavailable_reason"]],
        ["GA4 baseline", "Unavailable", "GA4", source["ga4"]["unavailable_reason"]],
        ["SEMrush baseline", "Unavailable", "SEMrush", source["semrush"]["unavailable_reason"]],
        [
            "PageSpeed baseline",
            "Unavailable",
            "PageSpeed",
            source["pagespeed"]["unavailable_reason"],
        ],
    ]
    backlink = [
        unavailable(
            "Backlink Overview",
            [
                "Domain",
                "total",
                "domains_num",
                "ips_num",
                "follows_num",
                "nofollows_num",
                "score",
                "trust_score",
                "urls_num",
                "ipclassc_num",
                "texts_num",
                "forms_num",
                "frames_num",
                "images_num",
            ],
            reason,
        ),
        unavailable(
            "Kakawa Backlinks",
            [
                "page_ascore",
                "source_url",
                "target_url",
                "anchor",
                "ascore",
                "trust_score",
                "first_seen",
                "last_seen",
            ],
            reason,
        ),
        unavailable(
            "Referring Domains",
            [
                "domain",
                "backlinks",
                "refdomains",
                "ascore",
                "first_seen",
                "last_seen",
                "ip",
                "country",
            ],
            reason,
        ),
        unavailable(
            "Anchor Text Analysis",
            ["anchor", "total", "domains", "first_seen", "last_seen"],
            reason,
        ),
        unavailable(
            "Top Linked Pages",
            [
                "page_url",
                "page_title",
                "page_ascore",
                "backlinks_count",
                "domains_count",
                "ip_count",
                "ips_count",
                "first_seen",
            ],
            reason,
        ),
        unavailable(
            "Competitor Ref Domains",
            [
                "Competitor Domain",
                "Referring domain",
                "Backlinks",
                "Authority Score",
                "First seen",
                "Last seen",
            ],
            reason,
        ),
        unavailable(
            "Historical Backlinks",
            [
                "date",
                "backlinks_num",
                "backlinks_lost_num",
                "backlinks_new_num",
                "domains_num",
                "domains_new_num",
                "domains_lost_num",
                "score",
            ],
            reason,
        ),
        unavailable("TLD Distribution", ["zone", "domains_num", "backlinks_num"], reason),
        unavailable("Authority Score Profile", ["ascore", "domains_num"], reason),
        unavailable(
            "Link Gap Opportunities",
            ["Referring Domain", "Links To Competitor", "Backlinks", "Domain Score"],
            reason,
        ),
        unavailable(
            "Competitor Overview",
            [
                "Domain",
                "Authority Score",
                "Total Backlinks",
                "Referring Domains",
                "Follow Links",
                "Nofollow Links",
            ],
            reason,
        ),
        unavailable(
            "Competitor Backlink Profiles",
            [
                "Competitor",
                "Total Referring Domains",
                "Shared with Kakawa",
                "Unique to Competitor",
                "Overlap %",
                "Gap Opportunity",
            ],
            reason,
        ),
        unavailable(
            "Backlink Competitors",
            [
                "Competitor Domain",
                "Authority Score",
                "Similarity Score",
                "Common Referring Domains",
                "Total Referring Domains",
                "Total Backlinks",
            ],
            reason,
        ),
        unavailable(
            "Backlink Matrix",
            [
                "Referring Domain",
                "Authority Score",
                "Targets Total",
                "Kakawa Chocolates BL",
                "Competitor BL",
                "Evidence Status",
            ],
            reason,
        ),
    ]
    competitors = [
        unavailable(
            "Domain Comparison",
            [
                "Domain",
                "Total Backlinks",
                "Referring Domains",
                "Referring IPs",
                "Follow Links",
                "Nofollow Links",
                "Authority Score",
            ],
            source["semrush"]["unavailable_reason"],
        ),
        unavailable(
            "Keyword Source Analysis",
            ["Source", "Keyword Count", "Status", "As of"],
            source["semrush"]["unavailable_reason"],
        ),
        unavailable(
            "Category Gap Analysis",
            ["Category", "Total Keywords", "Total Volume", "Status", "Evidence"],
            source["semrush"]["unavailable_reason"],
        ),
    ]
    contentinv = [
        [
            p["normalized_url"],
            p.get("title") or "Unavailable",
            p.get("meta_description") or "Unavailable",
            p.get("h1") or "Unavailable",
            p.get("status_code"),
            "Unavailable",
            ptype(p),
            "Unavailable",
            p.get("internal_links", 0),
            "Unavailable",
            "Not scored",
            p["evidence_id"],
        ]
        for p in pages
    ]
    cro = [
        [
            f["id"],
            f["category"],
            f["title"],
            f["impact"],
            f["priority"],
            f["description"],
            ", ".join(f["evidence_ids"]),
        ]
        for f in findings
    ] + [
        [
            f"CRO-{i:03d}",
            "Decision clarity",
            f"Review CTA and hierarchy for {c['target_url']}",
            "Manual and GA4 validation required",
            "P3",
            "Align one next action to the approved intent.",
            ", ".join(c["evidence_ids"]),
        ]
        for i, c in enumerate(content, 1)
    ]
    quick = [
        [
            f"QW-{i:03d}",
            "On-page clarity",
            r["url"],
            f"Resolve {r['title_issue']}, {r['meta_description_issue']} and {r['h1_issue']}",
            r["priority"],
            "Low-medium",
            r["evidence_id"],
        ]
        for i, r in enumerate(mrows, 1)
        if "Missing" in {r["title_issue"], r["meta_description_issue"], r["h1_issue"]}
    ][:150]
    productrows = [
        [
            p["normalized_url"],
            p.get("title") or "Unavailable",
            p.get("meta_description") or "Unavailable",
            p.get("h1") or "Unavailable",
            p.get("status_code"),
            "Unavailable",
            "Unavailable",
            "Unavailable",
            "Unavailable",
            len(p.get("title") or ""),
            len(p.get("meta_description") or ""),
            "Unavailable",
            "Not scored",
            p["evidence_id"],
        ]
        for p in products
    ]
    collectionrows = [
        [
            p["normalized_url"],
            p.get("title") or "Unavailable",
            p.get("meta_description") or "Unavailable",
            p.get("h1") or "Unavailable",
            p.get("status_code"),
            "Unavailable",
            "Unavailable",
            len(p.get("title") or ""),
            "Yes" if p.get("status_code") == 200 else "No",
            "Not scored",
            p["evidence_id"],
        ]
        for p in collections
    ]
    echecks = [
        ["Product URL inventory", "AVAILABLE", "P1", f"Review {len(products)} product URLs."],
        [
            "Collection URL inventory",
            "AVAILABLE",
            "P1",
            f"Review {len(collections)} collection URLs.",
        ],
        ["Product schema", "UNAVAILABLE", "P1", "Import structured-data extraction."],
        ["Prices and availability", "UNAVAILABLE", "P1", "Validate live page-specific evidence."],
        ["Ratings and reviews", "WITHHELD", "P1", "Never fabricate ratings."],
        ["Breadcrumbs", "UNAVAILABLE", "P2", "Import rendered and schema evidence."],
        ["Pagination canonicals", "REVIEW", "P1", "Validate graph in staging."],
        ["Image alt coverage", "UNAVAILABLE", "P2", "Import image inventory."],
        ["Internal links", "AVAILABLE", "P2", f"Review {len(links)} graph records."],
        ["Checkout tracking", "UNAVAILABLE", "P1", "Connect GA4."],
        ["Core Web Vitals", "UNAVAILABLE", "P1", "Connect PageSpeed."],
        ["Mobile UX", "NOT_RUN", "P2", "Manual rendered review required."],
        ["Accessibility", "NOT_RUN", "P2", "Run keyboard and screen-reader checks."],
        ["Schema approval", "GATED", "P1", "Agency-admin approval required."],
        ["Disavow", "DISABLED", "P1", "Evidence and approval conditions not met."],
    ]
    schema = [
        [
            p["normalized_url"],
            p.get("title") or "Unavailable",
            "Unavailable",
            p.get("status_code"),
            "Unavailable",
            "Unavailable",
            "Unavailable",
            p["evidence_id"],
        ]
        for p in products + collections
    ]
    local = [
        [n, "UNAVAILABLE", "Approved local source not connected", r, pr]
        for n, r, pr in [
            ("GBP ownership", "Connect approved GBP export.", "P1"),
            ("NAP consistency", "Reconcile verified facts.", "P1"),
            ("Categories", "Validate from GBP.", "P1"),
            ("Opening hours", "Validate current hours.", "P1"),
            ("Reviews", "Import official evidence.", "P1"),
            ("Citation consistency", "Import BrightLocal evidence.", "P2"),
            ("Local schema", "Require fact pack and approval.", "P1"),
        ]
    ]
    readiness = [
        ["Evidence", "Approved-domain crawl", "AVAILABLE", 1, "P1", "Maintain lineage."],
        ["Evidence", "GSC query data", "UNAVAILABLE", 0, "P1", "Connect GSC."],
        ["Structure", "Title and H1 extraction", "AVAILABLE", 1, "P2", "Resolve page issues."],
        ["Structure", "Schema extraction", "UNAVAILABLE", 0, "P1", "Import JSON-LD evidence."],
        ["Content", "Word count/depth", "UNAVAILABLE", 0, "P2", "Upload richer crawl data."],
        ["Content", "Distinct targets", "AVAILABLE", 1, "P1", f"{len(opps)} targets mapped."],
        ["Authority", "Backlinks", "UNAVAILABLE", 0, "P2", "Connect provider."],
        ["Entity", "Verified facts", "AVAILABLE", 1, "P1", "Maintain dated fact pack."],
        ["Commerce", "Product schema", "UNAVAILABLE", 0, "P1", "Validate page properties."],
        ["Trust", "Claims ledger", "AVAILABLE", 1, "P1", "Resolve every claim."],
        ["Technical", "Indexation graph", "AVAILABLE", 1, "P1", "Validate before deployment."],
        ["Technical", "Performance", "UNAVAILABLE", 0, "P1", "Connect PageSpeed."],
        ["QA", "Human approval", "REQUIRED", 0, "P1", "Gate 1 and Gate 2 mandatory."],
    ]
    geopages = [
        [
            p["normalized_url"],
            p.get("title") or "Unavailable",
            p.get("h1") or "Unavailable",
            "Unavailable",
            "Unavailable",
            ptype(p),
            "Unavailable",
            "Unavailable",
            "Unavailable",
            "Unavailable",
            "Withheld",
            "UNAVAILABLE",
            p["evidence_id"],
        ]
        for p in pages
    ]
    tracking = [
        [n, "UNAVAILABLE", "No validated analytics/tag export", r, pr]
        for n, r, pr in [
            ("GA4 property", "Connect OAuth and verify scope.", "P1"),
            ("Data stream", "Confirm hostname filters.", "P1"),
            ("Consent mode", "Review implementation.", "P1"),
            ("Purchase event", "Validate payload/deduplication.", "P1"),
            ("Lead events", "Define conversions.", "P1"),
            ("Checkout events", "Validate funnel events.", "P2"),
            ("Search Console link", "Verify property match.", "P1"),
            ("PII leakage", "Review payloads.", "P1"),
            ("Baseline freeze", "Approve before forecasting.", "P1"),
            ("Monitoring", "Define weekly checks.", "P2"),
        ]
    ]
    gapsummary = [
        [
            o["target_url"],
            next(c["title"] for c in content if c["target_url"] == o["target_url"]),
            "200 observed",
            "Existing page",
            o["cluster"],
            "Unavailable",
            1,
            "Unavailable",
            o["intent"],
            "Refresh",
            o["decision"],
            "P2",
            "Medium",
            ", ".join(o["evidence_ids"]),
        ]
        for o in opps
    ]
    keyword = [
        [
            o["cluster"],
            "Unavailable",
            "Unavailable",
            "Unavailable",
            "Evidence-backed refresh",
            o["intent"],
            "Topic proxy",
            o["target_url"],
            next(c["title"] for c in content if c["target_url"] == o["target_url"]),
            "Observed",
            "Existing page",
            "Distinct target; metrics withheld",
            ", ".join(o["evidence_ids"]),
        ]
        for o in opps
    ]
    prevent = [
        [
            o["cluster"],
            "LOW",
            o["target_url"],
            o["cluster"],
            "One approved target",
            "Refresh existing target",
            "No competing URL",
            ", ".join(o["evidence_ids"]),
        ]
        for o in opps
    ]
    pillars = [
        [
            c["title"],
            1,
            "Unavailable",
            c["primary_topic"],
            "Unavailable",
            "P2",
            c["target_url"],
            ", ".join(c["evidence_ids"]),
        ]
        for c in content
    ]
    funnel = [
        [k, v, "Unavailable", "Metrics withheld"]
        for k, v in sorted(Counter(c["intent"] for c in content).items())
    ]
    newpages = [
        [
            c["target_url"],
            c["primary_topic"],
            "Unavailable",
            1,
            "Existing-page refresh; no new URL",
            c["evidence_ids"][0],
        ]
        for c in content
    ]
    executive = [
        ["Mapped opportunities", len(opps)],
        ["Distinct target URLs", len({o["target_url"] for o in opps})],
        ["Keyword volume coverage", "Unavailable"],
        ["Ranking coverage", "Unavailable"],
        ["New URLs approved", 0],
        ["Existing-page refreshes", len(content)],
        ["Evidence as of", data["run"]["evidence_as_of"]],
    ]
    category = [
        [
            c["intent"],
            c["asset_type"],
            c["title"],
            c["target_url"],
            "Observed",
            "Existing page",
            "P2",
            c["primary_topic"],
            "Unavailable",
            "Unavailable",
            "Unavailable",
            "Unavailable",
            "Refresh existing target",
            c["evidence_ids"][0],
        ]
        for c in content
    ]
    urlmap = [
        [
            c["target_url"],
            c["title"],
            "Observed",
            "Existing page",
            c["primary_topic"],
            "Unavailable",
            "Unavailable",
            "Unavailable",
            1,
            "Unavailable",
            c["intent"],
            "P2",
            "Refresh existing target",
            c["evidence_ids"][0],
        ]
        for c in content
    ]
    tofu = [
        [
            c["title"],
            c["target_url"],
            "Existing",
            c["intent"],
            c["primary_topic"],
            "Unavailable",
            "Unavailable",
            c["target_url"],
            c["summary"],
            "Evidence-led scope",
            "P2",
            c["evidence_ids"][0],
        ]
        for c in content
        if any(x in c["intent"] for x in ("education", "informational", "support"))
    ]
    architecture = [
        [
            p["normalized_url"],
            p["normalized_url"],
            "None proposed",
            ptype(p),
            "Retain unless equivalence evidence supports change",
            p.get("status_code"),
            p["evidence_id"],
            "review_ready",
        ]
        for p in pages
    ]
    a13 = [
        [
            a["phase"],
            a["week"],
            a["id"],
            a["phase"],
            a["action"],
            "See evidence",
            "Unavailable",
            a["priority"],
            a["effort"],
            a["owner"],
            a["action"],
            a["kpi"],
            a["notes"],
            ", ".join(a["evidence_ids"]),
            a["approval_class"],
        ]
        for a in actions
    ]
    a14 = [
        [
            a["phase"],
            a["week"],
            a["id"],
            a["phase"],
            a["action"],
            "See evidence",
            "Unavailable",
            a["priority"],
            a["effort"],
            a["status"],
            a["owner"],
            a["action"],
            a["kpi"],
            a["notes"],
            ", ".join(a["evidence_ids"]),
            a["approval_class"],
        ]
        for a in actions
    ]
    a8 = [
        [
            a["id"],
            a["action"],
            a["phase"],
            a["priority"],
            "No forecast - baseline unavailable",
            a["week"],
            "Controlled action",
            a["owner"],
            a["approval_class"],
            ", ".join(a["evidence_ids"]),
        ]
        for a in actions
    ]
    checks = qa_rows(data)
    sc = Counter(r[2] for r in checks)
    cats = defaultdict(Counter)
    for cat, _, status, _, _ in checks:
        cats[cat][status] += 1
    qsum = [
        ["Total checks", len(checks)],
        ["Passed", sc["PASS"]],
        ["Review", sc["REVIEW"]],
        ["Failed", sc["FAIL"]],
        ["Critical failures", data["qa"]["critical_failures"]],
        ["High failures", data["qa"]["high_failures"]],
        ["Release status", data["qa"]["release_status"]],
    ]
    qcat = [
        [k, sum(v.values()), v["PASS"], v["FAIL"], round(v["PASS"] / max(1, sum(v.values())), 4)]
        for k, v in sorted(cats.items())
    ]
    specs = [
        spec(
            "01_Audit_Reports/Backlink_Audit_Report.xlsx",
            "Backlink Audit Report",
            backlink,
            data,
            "UNAVAILABLE",
            "Connect backlink evidence before authority scoring.",
        ),
        spec(
            "01_Audit_Reports/Baseline_Performance_Analysis.xlsx",
            "Baseline Performance Analysis",
            [
                sheet(
                    "Baseline Performance",
                    ["Metric", "Current Value", "Source", "Notes"],
                    baseline,
                    "Canonical baseline plus truthful unavailable states.",
                )
            ],
            data,
        ),
        spec(
            "01_Audit_Reports/Competitor_Landscape_Analysis.xlsx",
            "Competitor Landscape Analysis",
            competitors,
            data,
            "UNAVAILABLE",
            "No competitor claims without approved market evidence.",
        ),
        spec(
            "01_Audit_Reports/Content_Audit_Workbook.xlsx",
            "Content Audit Workbook",
            [
                sheet(
                    "Full Page Inventory",
                    [
                        "URL",
                        "Title",
                        "Meta Description",
                        "H1",
                        "Status Code",
                        "Word Count",
                        "Page Type",
                        "Schema Types",
                        "Internal Links",
                        "External Links",
                        "Content Quality",
                        "Evidence",
                    ],
                    contentinv,
                    "Full 357-page normalized inventory.",
                ),
                sheet(
                    "Thin Content",
                    ["URL", "Title", "Word Count", "Page Type", "Status", "Evidence"],
                    [
                        [
                            "UNAVAILABLE",
                            "UNAVAILABLE",
                            "Not captured",
                            "UNAVAILABLE",
                            "NOT SCORED",
                            "Upload richer CDX/CDD/XML crawl data",
                        ]
                    ],
                    "Thin-content scoring withheld without word count.",
                ),
                sheet(
                    "Duplicate Content",
                    [
                        "URL",
                        "Duplicate Type",
                        "Duplicate Value",
                        "Word Count",
                        "Page Type",
                        "Evidence",
                    ],
                    dup,
                    "Exact duplicate-field clusters; human intent review required.",
                ),
                sheet(
                    "Content Gap Analysis",
                    [
                        "Keyword / Topic",
                        "Search Volume",
                        "Category",
                        "Funnel Stage",
                        "Target URL",
                        "Page Type",
                        "Action",
                        "Evidence",
                    ],
                    gaps,
                    "20 distinct supported refresh opportunities; volume withheld.",
                ),
                sheet(
                    "Cannibalization Detection",
                    ["Analysis Area", "Finding", "Status", "Priority", "Evidence"],
                    cannibal,
                    "One target per opportunity; duplicates trigger review.",
                ),
            ],
            data,
        ),
        spec(
            "01_Audit_Reports/CRO_UX_Findings.xlsx",
            "CRO and UX Findings",
            [
                sheet(
                    "CRO & UX Findings",
                    [
                        "Finding ID",
                        "Category",
                        "Finding",
                        "Impact",
                        "Priority",
                        "Recommendation",
                        "Evidence",
                    ],
                    cro,
                    "Behavioural conclusions withheld until GA4 is connected.",
                    "priority",
                    5,
                ),
                sheet(
                    "Quick Wins",
                    [
                        "Finding ID",
                        "Category",
                        "URL",
                        "Recommendation",
                        "Priority",
                        "Estimated Effort",
                        "Evidence",
                    ],
                    quick,
                    "Low-risk review queue; not a forecast.",
                    "priority",
                    5,
                ),
            ],
            data,
        ),
        spec(
            "01_Audit_Reports/Ecommerce_Audit_Report.xlsx",
            "Ecommerce Audit Report",
            [
                sheet(
                    "Product Pages",
                    [
                        "URL",
                        "Title",
                        "Meta Description",
                        "H1",
                        "Status Code",
                        "Word Count",
                        "Schema Types",
                        "Images Total",
                        "Images Missing Alt",
                        "Title Length",
                        "Meta Desc Length",
                        "Has Product Schema",
                        "Content Quality",
                        "Evidence",
                    ],
                    productrows,
                    "Full product URL inventory.",
                ),
                sheet(
                    "Collection Pages",
                    [
                        "URL",
                        "Title",
                        "Meta Description",
                        "H1",
                        "Status Code",
                        "Word Count",
                        "Schema Types",
                        "Title Length",
                        "Is Live",
                        "Content Quality",
                        "Evidence",
                    ],
                    collectionrows,
                    "Full collection/pagination inventory.",
                ),
                sheet(
                    "E-commerce Checklist",
                    ["Check Item", "Status", "Priority", "Recommendation"],
                    echecks,
                    "Evidence status and implementation gate.",
                    "priority",
                    3,
                ),
                sheet(
                    "Schema Coverage",
                    [
                        "URL",
                        "Title",
                        "Schema Types",
                        "Status Code",
                        "Has Product Schema",
                        "Has BreadcrumbList",
                        "Has Organization",
                        "Evidence",
                    ],
                    schema,
                    "Coverage not inferred without extraction.",
                ),
            ],
            data,
        ),
        spec(
            "01_Audit_Reports/GBP_Local_Audit.xlsx",
            "GBP and Local Audit",
            [
                sheet(
                    "Local SEO Audit",
                    ["Check", "Current Status", "Issue", "Recommendation", "Priority"],
                    local,
                    "Local scoring withheld until GBP/BrightLocal evidence.",
                    "priority",
                    5,
                )
            ],
            data,
            "UNAVAILABLE",
        ),
        spec(
            "01_Audit_Reports/GEO_AEO_Readiness_Scorecard.xlsx",
            "GEO and AEO Readiness Scorecard",
            [
                sheet(
                    "Readiness Scorecard",
                    [
                        "Category",
                        "Check Item",
                        "Current Status",
                        "Score",
                        "Priority",
                        "Recommendation",
                    ],
                    readiness,
                    "Control flags, not unsupported performance scores.",
                    "priority",
                    5,
                ),
                sheet(
                    "Page-Level GEO Analysis",
                    [
                        "URL",
                        "Title",
                        "H1",
                        "Schema Types",
                        "Word Count",
                        "Page Type",
                        "Has Schema",
                        "Has FAQ",
                        "Has Product Schema",
                        "Content Depth",
                        "GEO Score",
                        "GEO Readiness",
                        "Evidence",
                    ],
                    geopages,
                    "Page scores withheld without structured content evidence.",
                ),
                sheet(
                    "AEO Recommendations",
                    ["Category", "Recommendation", "Priority", "Impact"],
                    [[r[0], r[5], r[4], "Evidence-gated"] for r in readiness[1:]],
                    "Evidence-gated recommendations.",
                ),
            ],
            data,
        ),
        spec(
            "01_Audit_Reports/Technical_Audit_Report.xlsx",
            "Technical Audit Report",
            [
                sheet("Full Site Inventory", invh, inv, "Complete normalized page inventory."),
                sheet(
                    "Error Pages",
                    invh,
                    errors,
                    "Non-200 pages; no generic destination is invented.",
                ),
                sheet(
                    "Title Tag Issues",
                    [
                        "URL",
                        "Title",
                        "Title Length",
                        "Title Issue",
                        "Status Code",
                        "Page Type",
                        "Evidence",
                    ],
                    title,
                    "Deterministic availability/length checks.",
                ),
                sheet(
                    "Meta Description Issues",
                    [
                        "URL",
                        "Meta Description",
                        "Meta Desc Length",
                        "Meta Desc Issue",
                        "Status Code",
                        "Page Type",
                        "Evidence",
                    ],
                    meta,
                    "Deterministic availability/length checks.",
                ),
                sheet(
                    "H1 Issues",
                    ["URL", "H1", "H1 Issue", "Status Code", "Page Type", "Evidence"],
                    h1,
                    "Captured availability/multiplicity checks.",
                ),
                sheet(
                    "Schema Issues",
                    ["URL", "Schema Types", "Schema Issue", "Page Type", "Evidence"],
                    [
                        [
                            p["normalized_url"],
                            "Unavailable",
                            "Not assessed - extraction unavailable",
                            ptype(p),
                            p["evidence_id"],
                        ]
                        for p in pages
                    ],
                    "Absence is not asserted without extraction.",
                ),
                sheet(
                    "Thin Content",
                    ["URL", "Title", "Word Count", "Page Type", "Evidence"],
                    [
                        [
                            "UNAVAILABLE",
                            "UNAVAILABLE",
                            "Not captured",
                            "UNAVAILABLE",
                            "Upload richer crawl data",
                        ]
                    ],
                    "Withheld without word count.",
                ),
                sheet(
                    "Image Alt Text Issues",
                    [
                        "URL",
                        "Images Total",
                        "Images Missing Alt",
                        "Alt Text Issue",
                        "Page Type",
                        "Evidence",
                    ],
                    [
                        [
                            p["normalized_url"],
                            "Unavailable",
                            "Unavailable",
                            "Not assessed",
                            ptype(p),
                            p["evidence_id"],
                        ]
                        for p in pages
                    ],
                    "Requires image-level crawl data.",
                ),
                sheet(
                    "Canonical Issues",
                    ["URL", "Canonical", "Canonical Match", "Page Type", "Evidence", "Approval"],
                    canonical,
                    "Observed comparison; changes remain admin-gated.",
                ),
            ],
            data,
        ),
        spec(
            "01_Audit_Reports/Tracking_Audit_Report.xlsx",
            "Tracking Audit Report",
            [
                sheet(
                    "Tracking Audit",
                    ["Check", "Status", "Details", "Recommendation", "Priority"],
                    tracking,
                    "No health claims without validated analytics evidence.",
                    "priority",
                    5,
                )
            ],
            data,
            "UNAVAILABLE",
        ),
        spec(
            "02_Strategy_Documents/Cannibalization_Resolution_Plan.xlsx",
            "Cannibalization Resolution Plan",
            [
                sheet(
                    "Cannibalization Issues",
                    [
                        "Keyword Cluster",
                        "Cannibalizing URL 1",
                        "Cannibalizing URL 2",
                        "Recommended Action",
                        "Content to Merge",
                        "Priority",
                        "Evidence",
                    ],
                    [
                        [
                            r[2],
                            r[0],
                            "See duplicate register",
                            "Review intent/equivalence",
                            "No automatic merge",
                            "P2",
                            r[5],
                        ]
                        for r in dup[:250]
                    ],
                    "Duplicate fields do not prove keyword cannibalization.",
                )
            ],
            data,
        ),
        spec(
            "02_Strategy_Documents/Content_Gap_Analysis.xlsx",
            "Content Gap Analysis",
            [
                sheet(
                    "Gap Summary",
                    [
                        "Target URL",
                        "Page Title",
                        "Status",
                        "Page Type",
                        "Primary Keyword",
                        "Primary Volume",
                        "Total Keywords Mapped",
                        "Combined Cluster Volume",
                        "Funnel Stage",
                        "Gap Type",
                        "Action Required",
                        "Priority",
                        "Est. Effort",
                        "Evidence",
                    ],
                    gapsummary,
                    "Market metrics withheld.",
                ),
                sheet(
                    "Keyword-Level Details",
                    [
                        "Keyword",
                        "Search Volume",
                        "CPC ($)",
                        "Competition",
                        "Product Category",
                        "Funnel Stage",
                        "Keyword Role",
                        "Target URL",
                        "Page Title",
                        "Page Status",
                        "Page Type",
                        "Mapping Rationale",
                        "Evidence",
                    ],
                    keyword,
                    "Topics are proxies, not measured demand.",
                ),
                sheet(
                    "Cannibalization Prevention",
                    [
                        "Risk Group",
                        "Risk Level",
                        "Competing Pages",
                        "Competing Keywords",
                        "Issue Description",
                        "Resolution Strategy",
                        "Implementation Notes",
                        "Evidence",
                    ],
                    prevent,
                    "One intent, one target.",
                ),
            ],
            data,
        ),
        spec(
            "02_Strategy_Documents/Content_Strategy.xlsx",
            "Content Strategy",
            [
                sheet(
                    "Content Pillars",
                    [
                        "Content Pillar",
                        "Total Keywords",
                        "Total Search Volume",
                        "Top Keyword",
                        "Top Keyword Volume",
                        "Priority",
                        "Target URL",
                        "Evidence",
                    ],
                    pillars,
                    "20 supported refresh pillars; no padding.",
                ),
                sheet(
                    "Funnel Distribution",
                    ["Funnel Stage / Intent", "Assets", "Total Volume", "Availability Note"],
                    funnel,
                    "Asset distribution available; volume withheld.",
                ),
                sheet(
                    "New Pages Required",
                    [
                        "Target URL",
                        "Primary Keyword",
                        "Total Cluster Volume",
                        "Keywords in Cluster",
                        "Page Type / Decision",
                        "Evidence",
                    ],
                    newpages,
                    "No new URL approved; refresh existing targets.",
                ),
            ],
            data,
        ),
        spec(
            "02_Strategy_Documents/Master_Keyword_Universe.xlsx",
            "Master Keyword Universe",
            [
                sheet(
                    "Executive Summary",
                    ["Metric", "Value"],
                    executive,
                    "Demand/rankings withheld until providers connect.",
                ),
                sheet(
                    "Category & URL Mapping",
                    [
                        "L1 Category",
                        "L2 Sub-Category",
                        "Page Title",
                        "Target URL",
                        "Status",
                        "Page Type",
                        "Priority",
                        "Primary Keyword",
                        "Primary KW Volume",
                        "Secondary Keywords",
                        "Combined Cluster Volume",
                        "Est. Products",
                        "Notes",
                        "Evidence",
                    ],
                    category,
                    "Evidence-backed topic mapping.",
                ),
                sheet(
                    "Keyword Research Mapping",
                    [
                        "Keyword / Topic",
                        "Search Volume",
                        "CPC ($)",
                        "Competition",
                        "Product Category",
                        "Funnel Stage",
                        "Target URL",
                        "Page Status",
                        "Page Type",
                        "Keyword Role",
                        "Evidence",
                    ],
                    [
                        [r[0], r[1], r[2], r[3], r[4], r[5], r[7], r[9], r[10], r[6], r[12]]
                        for r in keyword
                    ],
                    "Topics are not presented as measured keywords.",
                ),
                sheet(
                    "URL Mapping",
                    [
                        "Target URL",
                        "Page Title",
                        "Status",
                        "Page Type",
                        "Primary Keyword",
                        "Primary Vol",
                        "Secondary Keywords",
                        "Tertiary Keywords",
                        "Total Keywords",
                        "Combined Volume",
                        "Funnel Stage",
                        "Priority",
                        "Action Required",
                        "Evidence",
                    ],
                    urlmap,
                    "One accountable target per opportunity.",
                ),
                sheet(
                    "TOFU & MOFU Content Strategy",
                    [
                        "Blog Post Title",
                        "Target URL",
                        "Status",
                        "Funnel Stage",
                        "Primary Keyword",
                        "Search Volume",
                        "Supporting Keywords",
                        "Internal Link Target",
                        "Content Brief",
                        "Word Count Target",
                        "Priority",
                        "Evidence",
                    ],
                    tofu,
                    "Only evidence-supported existing-page work.",
                ),
            ],
            data,
        ),
        spec(
            "02_Strategy_Documents/URL_Architecture_Map.xlsx",
            "URL Architecture Map",
            [
                sheet(
                    "URL Architecture Map",
                    [
                        "Current URL",
                        "Proposed New URL",
                        "Redirect Type",
                        "New Category/Silo",
                        "Rationale",
                        "Status Code",
                        "Evidence",
                        "Approval",
                    ],
                    architecture,
                    "Retain observed URLs unless graph evidence supports change.",
                )
            ],
            data,
            approval=True,
        ),
        spec(
            "03_Action_Plan/16_Week_Action_Plan.xlsx",
            "16 Week Action Plan",
            [
                sheet(
                    "16-Week Action Plan",
                    [
                        "Phase",
                        "Week",
                        "Task #",
                        "Category",
                        "Description",
                        "Pages/Items",
                        "Est. Search Volume",
                        "Priority",
                        "Est. Effort",
                        "Owner",
                        "Deliverable",
                        "KPI / Success Metric",
                        "Notes",
                        "Evidence",
                        "Approval",
                    ],
                    a13,
                    "48 evidence-linked actions; forecasts withheld.",
                    "priority",
                    8,
                )
            ],
            data,
        ),
        spec(
            "03_Action_Plan/16_Week_Atomic_Action_Plan.xlsx",
            "16 Week Atomic Action Plan",
            [
                sheet(
                    "Atomic Action Plan",
                    [
                        "Phase",
                        "Week",
                        "Task #",
                        "Task Category",
                        "Task Description",
                        "Pages/Items",
                        "Total Search Volume",
                        "Priority",
                        "Est. Effort",
                        "Status",
                        "Owner",
                        "Deliverable/Output",
                        "KPI/Success Metric",
                        "Notes",
                        "Evidence",
                        "Approval",
                    ],
                    a14,
                    "Atomic canonical plan with evidence and approval.",
                    "priority",
                    8,
                )
            ],
            data,
        ),
        spec(
            "03_Action_Plan/Atomic_Action_Plan.xlsx",
            "Atomic Action Plan",
            [
                sheet(
                    "16-Week Action Plan",
                    [
                        "Task ID",
                        "Task Description",
                        "Category",
                        "Priority",
                        "Estimated Traffic Impact",
                        "Implementation Week",
                        "Deliverable Type",
                        "Owner",
                        "Approval",
                        "Evidence",
                    ],
                    a8,
                    "No invented traffic estimate.",
                    "priority",
                    4,
                )
            ],
            data,
        ),
        spec(
            "04_Implementation_Deliverables/Link_Building/Citation_List.xlsx",
            "Citation List",
            [
                unavailable(
                    "Citation Sources",
                    [
                        "Citation Source",
                        "URL",
                        "Category",
                        "Current Status",
                        "NAP Consistent",
                        "Action Required",
                        "Priority",
                    ],
                    "GBP/BrightLocal and verified NAP evidence not supplied.",
                )
            ],
            data,
            "UNAVAILABLE",
        ),
        spec(
            "04_Implementation_Deliverables/Link_Building/Internal_Link_Map.xlsx",
            "Internal Link Map",
            [
                sheet(
                    "Internal Link Map",
                    [
                        "Source Page URL",
                        "Anchor Text",
                        "Target Page URL",
                        "Link Type",
                        "Approval",
                        "Observed Status",
                        "Rationale",
                        "Evidence",
                    ],
                    links,
                    "Observed parent links plus review-gated candidates.",
                )
            ],
            data,
            approval=True,
        ),
        spec(
            "04_Implementation_Deliverables/Link_Building/Outreach_Target_List.xlsx",
            "Outreach Target List",
            [
                unavailable(
                    "Outreach Targets",
                    [
                        "Domain",
                        "Domain Authority",
                        "Contact Type",
                        "Outreach Angle",
                        "Target Page",
                        "Priority",
                        "Status",
                    ],
                    "No approved backlink/contact evidence; targets are not invented.",
                )
            ],
            data,
            "UNAVAILABLE",
        ),
        spec(
            "04_Implementation_Deliverables/On_Page_Optimizations/H1_Tags.xlsx",
            "H1 Tag Optimizations",
            [
                sheet(
                    "H1 Tag Optimizations",
                    [
                        "URL",
                        "Current H1",
                        "Issue",
                        "New Optimized H1",
                        "Target Keyword",
                        "Priority",
                        "Evidence",
                        "Approval",
                    ],
                    h1opt,
                    "Evidence-safe proposals; keywords unavailable.",
                )
            ],
            data,
            approval=True,
        ),
        spec(
            "04_Implementation_Deliverables/On_Page_Optimizations/Meta_Description_Optimizations.xlsx",
            "Meta Description Optimizations",
            [
                sheet(
                    "Sheet1",
                    [
                        "URL",
                        "Current Meta Description",
                        "New Written Meta Description",
                        "Issue",
                        "Current Length",
                        "Priority",
                        "Evidence",
                        "Approval",
                    ],
                    metaopt,
                    "Review-ready descriptions avoid unsupported claims.",
                )
            ],
            data,
            approval=True,
        ),
        spec(
            "04_Implementation_Deliverables/On_Page_Optimizations/Meta_Tags.xlsx",
            "Meta Tags",
            [
                sheet(
                    "Title Tags",
                    [
                        "URL",
                        "Current Title",
                        "New Written Title",
                        "Issue",
                        "Current Length",
                        "Priority",
                        "Evidence",
                        "Approval",
                    ],
                    titleopt,
                    "Review-ready title proposals.",
                ),
                sheet(
                    "Meta Descriptions",
                    [
                        "URL",
                        "Current Meta Description",
                        "New Written Meta Description",
                        "Issue",
                        "Current Length",
                        "Priority",
                        "Evidence",
                        "Approval",
                    ],
                    metaopt,
                    "Human approval required.",
                ),
            ],
            data,
            approval=True,
        ),
        spec(
            "04_Implementation_Deliverables/On_Page_Optimizations/Title_Tag_Optimizations.xlsx",
            "Title Tag Optimizations",
            [
                sheet(
                    "Sheet1",
                    [
                        "URL",
                        "Current Title",
                        "New Written Title",
                        "Issue",
                        "Current Length",
                        "Priority",
                        "Evidence",
                        "Approval",
                    ],
                    titleopt,
                    "Review-ready title proposals.",
                )
            ],
            data,
            approval=True,
        ),
        spec(
            "04_Implementation_Deliverables/Technical_Fixes/Canonical_Fixes.xlsx",
            "Canonical Fixes",
            [
                sheet(
                    "Sheet1",
                    [
                        "URL",
                        "Canonical URL",
                        "Canonical Match",
                        "Page Type",
                        "Evidence",
                        "Approval",
                    ],
                    canonical,
                    "Observed comparison; no automatic deployment.",
                )
            ],
            data,
            approval=True,
        ),
        spec(
            "04_Implementation_Deliverables/Technical_Fixes/Redirect_Map.xlsx",
            "Redirect Map",
            [
                sheet(
                    "Redirect Map",
                    [
                        "Source URL",
                        "Destination URL",
                        "Status Code",
                        "Reason",
                        "Evidence",
                        "Approval",
                        "Deployable",
                    ],
                    redirects,
                    "Non-200 sources listed; targets withheld until equivalence validation.",
                )
            ],
            data,
            approval=True,
        ),
        spec(
            "06_QA/QC_Report_v12.xlsx",
            "Quality Control Report v12",
            [
                sheet(
                    "QC Results",
                    ["Category", "Test", "Status", "Detail", "Evidence"],
                    checks,
                    "Page-level and release-level QA.",
                ),
                sheet("Summary", ["Metric", "Value"], qsum, "Canonical release summary."),
                sheet(
                    "Category Breakdown",
                    ["Category", "Total", "Passed", "Failed", "Pass Rate"],
                    qcat,
                    "Category reconciliation.",
                ),
            ],
            data,
        ),
        spec(
            "06_QA/QC_Report_v13.xlsx",
            "Quality Control Report v13",
            [
                sheet(
                    "QC Results",
                    ["Check", "Status", "Detail", "Category", "Evidence"],
                    [[r[1], r[2], r[3], r[0], r[4]] for r in checks],
                    "Expanded canonical QA; no stale manual counts.",
                )
            ],
            data,
        ),
    ]
    if len(specs) != 29:
        raise ValueError(f"Expected 29 workbooks, built {len(specs)}")
    return specs
