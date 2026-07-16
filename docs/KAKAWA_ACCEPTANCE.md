# Kakawa Chocolates v19 acceptance contract

Package target: `exports/Kakawa_Chocolates_Enterprise_SEO_Package_v19/`  
Comparison deliverable: `exports/Kakawa_v18_vs_v19_Quality_Comparison.pdf`  
Last contract review: 2026-07-15

## Status and evidence rule

This document is an acceptance contract, not an acceptance certificate and not a claim that a live run occurred. The current result is authoritative only when the target package contains a machine-readable QA report, internal manifest and checksums that verify against the actual files.

If any required file is absent, any required check has no current evidence, or the package was produced only from replay/fixture data, the corresponding acceptance status is **NOT EVALUATED** or **UNAVAILABLE**, not pass.

No live GSC, GA4, SEMrush, PageSpeed or OpenAI result is asserted by this document. Credentials are deployment inputs and must never be embedded in the package. A source without credentials or authorization is reported unavailable with its reason and coverage impact.

## Benchmark purpose

Kakawa v19 is the production acceptance case for the Studio. It must demonstrate that the system can turn a bounded, approved Kakawa evidence set into a coherent enterprise SEO package whose claims, URLs, counts, priorities, deployment proposals, content and cross-format derivatives reconcile.

The v18 package is read-only negative-regression input. Its presence does not authorize copying its claims into v19. V19 may retain a v18 statement only when it is independently supported by current canonical evidence and a current as-of date.

## Approved scope

The project administrator records the exact approved-domain list before collection. The expected primary boundary is the authorized Kakawa `.com.au` property, such as `kakawachocolates.com.au`, plus only explicitly approved real subdomains. The crawler must not treat a `.com` lookalike or unrelated domain as in scope.

Required run context:

| Field | Required evidence |
|---|---|
| Client/project identity | Canonical `Client` and `Project` IDs |
| Primary and approved domains | Canonical project record and package scope statement |
| Locale/country | Expected `en-AU` / `AU`, or documented approved change |
| Profile | Enterprise |
| Rule version | Canonical run and QA ledger |
| Source cutoff/as-of | Canonical run and every measured report |
| Crawl budget and stop reason | Crawler configuration and reconciliation report |
| Stratification | Method/seed/strata when sitemap inventory exceeds 25,000-page budget |
| Approvers | Gate 1, Gate 2, high-risk and package approval records |

The system never changes the Kakawa website and never submits anything to an external platform.

## Source availability matrix

The package contains a row for every expected source, even when unavailable.

| Source | Expected mode | Evidence when available | Truthful unavailable example |
|---|---|---|---|
| Approved-domain crawler | Fresh Enterprise collection | Snapshot hash, captured time, pages/failures, budgets, robots and stop reason | Authorization or safe network collection unavailable |
| PageSpeed Insights | Up to 200 deterministic samples | Sample selection, locale/device, capture time, returned metrics and source hash | API key absent/rejected, rate limit, timeout or upstream error |
| Google Search Console | Up to authorized 16-month scope | Property, period, dimensions, row count, capture time and hash | OAuth/token/property scope unavailable |
| Google Analytics 4 | Up to authorized 16-month scope | Property, period, dimensions/metrics, row count and hash | OAuth/token/property scope unavailable |
| SEMrush | Keyword/competitor evidence | Database/market, report, period/capture and hash | API key/report access unavailable |
| Ahrefs/Screaming Frog | Validated upload when supplied | Import hash, schema version, mapping and row reconciliation | File not supplied or validation rejected |
| BrightLocal/GBP | Validated upload when supplied | Import hash, location scope, schema/mapping and reconciliation | File not supplied or validation rejected |
| OpenAI | Evidence-bound drafting only | Requested/returned model ID, prompt version, hashes, token/cost ledger, status | API key/SDK/provider unavailable or output refused/invalid |

Unavailable sources reduce evidence coverage. A health score is omitted below 70 percent weighted evidence coverage. A forecast is omitted unless a source-qualified baseline and explicit assumptions support scenario bands.

## Required package inventory

The package uses the following semantic structure. Exact filenames are recorded once in the manifest; identical bytes must not be duplicated across folders.

### `00_Executive`

- Executive report PDF.
- Self-contained HTML presentation with embedded local assets and no machine-specific paths.
- Executive PPTX.
- Rendered slide PDF derived from the same approved deck source.

### `01_Evidence_and_Audits`

- Audit workbook with source coverage, issue register, evidence index and category audit sheets.
- Human-readable category reports as justified by available evidence.
- Source coverage/availability register.

### `02_Strategy`

- Strategy DOCX and rendered PDF.
- Keyword universe and evidence/source columns.
- Competitor intelligence with source scope and no unsupported assertions.
- Topical/cluster map.
- URL architecture and cannibalization decisions.
- Content roadmap.

### `03_Action_Plan`

- One authoritative 16-week action plan as XLSX, CSV and PDF derivatives.
- Each row includes week, owner, dependency, effort, KPI, evidence, priority inputs/tier and separate risk/approval class.
- The workbook contains a useful Gantt/timeline view and reconciles to every derivative.

### `04_Deployment_Assets`

- Proposed redirects and canonicals.
- Titles, meta descriptions and H1 proposals.
- Internal-link recommendations.
- Robots recommendations.
- Page-specific JSON-LD where supported.
- Platform notes and approval ledger.

Every high-risk file remains a proposal and requires agency-administrator approval. Generic catch-all redirects, unsafe canonical targets and unsupported schema are prohibited.

### `05_Content`

- Approved content briefs.
- Complete approved drafts in DOCX, HTML and Markdown as applicable.
- Source/fact and claim ledgers for each asset.

Twenty is a ceiling, not a target. Fewer than 20 assets is the correct result when fewer than 20 distinct opportunities pass evidence, target-URL, similarity and cannibalization gates. Arbitrary padding fails acceptance.

### `06_QA_and_Manifest`

- QA PDF, XLSX and JSON derived from one canonical result set.
- Availability matrix.
- Change log.
- Generation ledger.
- Internal manifest.
- Per-file checksum list.

The final ZIP sits adjacent to the unpacked package, with an adjacent SHA-256 checksum file. The ZIP contains its own internal manifest.

## Global hard gates

All of the following must be true for package acceptance:

| Gate ID | Requirement | Failure severity |
|---|---|---|
| KAK-G01 | Zero unresolved Critical or High QA failures | Blocking |
| KAK-G02 | Zero URL outside approved Kakawa domains in page/deployment/content targets | Blocking |
| KAK-G03 | Zero unresolved placeholders, fabricated ratings or unsupported factual claims | Blocking |
| KAK-G04 | Zero broken internal links, redirect loops, canonical cycles or unsafe targets | Blocking |
| KAK-G05 | Every measured/derived claim has evidence, as-of and applicable locale/device/scope/rule/confidence | Blocking |
| KAK-G06 | Every absent source/value has an explicit unavailable reason | Blocking |
| KAK-G07 | UI, workbook, reports, deck and manifest counts reconcile | Blocking |
| KAK-G08 | Zero unexplained duplicate files or duplicate normalized pages | Blocking |
| KAK-G09 | Zero unapproved near-duplicate or cannibalizing content assets | Blocking |
| KAK-G10 | Zero unapproved risky redirect/canonical/robots/schema/disavow asset | Blocking |
| KAK-G11 | HTML/PPTX/PDF deck is self-contained and contains no machine-specific paths | Blocking |
| KAK-G12 | Internal manifest, every file hash, final ZIP hash and adjacent checksum validate | Blocking |
| KAK-G13 | Gate 1, Gate 2 and final package approval records are current for this run/version | Blocking |
| KAK-G14 | Deployment was verified in staging before any production promotion | Blocking |

Warnings may remain only when they have an owner, rationale, impact and explicit acceptance. A skipped check is not a pass.

## Evidence and scoring gates

### URL reconciliation

1. Preserve original discovered/imported URLs.
2. Normalize with the versioned URL rule.
3. Deduplicate by `(run, normalized_url)`.
4. Report source duplicate count and normalization exclusions.
5. Assert each canonical page host is inside approved domains.
6. Reconcile crawl page count, audit workbook page count, issue denominators and manifest summary.

Wrong-domain `.com` contamination is a hard failure, even if the URL was present in v18.

### Audit scoring

- Category scores are deterministic and show rule version, applicable weight, evidence coverage and penalties.
- Overall health is null below 70 percent weighted coverage.
- Priority P1–P4 is calculated from impact, evidence confidence, reach, business criticality, dependency urgency and effort.
- Implementation risk is shown independently and never inferred from priority.
- No category with missing evidence is silently scored as healthy.

### Forecasts

A scenario band requires:

- a named canonical baseline and period;
- source/evidence IDs;
- an explicit assumption set;
- calculation version;
- low/base/high or equivalent bands labeled as scenarios, not predictions;
- limitation and confidence statement.

Without all inputs, the output states unavailable. It does not borrow v18 forecasts.

## Deployment-asset gates

### Redirects

- Each source and target is page-specific and within approved boundaries unless an authorized external destination is explicitly justified.
- Source exists or is evidenced as historical/changed.
- Target is live, indexable as intended and not itself redirected unsafely.
- No loops, excessive chains, catch-all destinations or conflicting rules.
- Rationale, owner, risk and rollback note are present.
- Administrator approval targets the exact artifact hash.

### Canonicals

- Each canonical target is approved, normalized, safe and semantically justified.
- No canonical cycles, dangling targets or conflicting declarations.
- Cross-domain canonicals are absent unless separately authorized with strong evidence and administrator approval.

### Robots

- Recommendations reference affected paths and observed evidence.
- No rule accidentally blocks required customer, asset or indexable paths.
- Environment-specific rules are separated.
- The application does not publish `robots.txt`.

### Structured data

- Page-specific type/property selection matches visible canonical facts.
- URLs and identifiers are verified.
- Ratings, review counts, products, prices, availability, organization facts and local-business facts require explicit evidence.
- Unsupported properties are omitted, not guessed.
- JSON parses and passes local schema/semantic QA; external validator status, when used, is dated and recorded.

### Disavow

Disavow output remains disabled unless every candidate has backlink evidence, documented removal attempts, documented manual-action risk and explicit agency-administrator approval. A provider toxicity score alone is insufficient. When conditions are not met, the availability matrix states that disavow was intentionally not generated.

## Content gates

Each content asset must have:

- a distinct intent, primary keyword/topic cluster and normalized target URL;
- evidence-supported opportunity and rationale;
- an approved fact pack and source evidence;
- a complete claim ledger with no pending/unsupported claim;
- only approved-domain internal links with known nonbroken status;
- no unsupported product, price, rating, award, location or service fact;
- no unresolved placeholder;
- similarity below the configured threshold against existing and v19 assets;
- an explicit cannibalization decision;
- a human reviewer and approval timestamp for the exact draft version.

If OpenAI is unavailable or refuses/returns invalid output, the asset remains unavailable or draft. Template filler is prohibited.

## Format and render gates

### XLSX

- Proper Excel tables, filters, frozen panes, print settings and deliberate widths.
- Formulas rather than pasted calculated values where appropriate.
- Useful charts tied to source ranges.
- Source/as-of metadata visible.
- Formula errors scan clean.
- Every sheet rendered and inspected at readable scale.

### DOCX

- Deliberate cover, heading hierarchy, page numbers, evidence callouts and citations.
- Real list numbering and repeated table headers.
- Meaningful image alt text where media is used.
- All pages rendered and inspected; no clipping, overlap, orphan heading or broken table.

### PPTX and slide PDF

- One clear takeaway per slide, evidence-labeled charts and readable typography.
- No external/machine-local linked assets.
- Every slide rendered and inspected for overflow, overlap and contrast.
- PPTX and slide PDF claims/counts reconcile.

### HTML

- Fully self-contained for offline review.
- Semantic headings/landmarks, keyboard operation, visible focus and reduced-motion support.
- No external scripts, fonts, analytics calls or file-system paths.
- All internal navigation/anchors resolve.

### PDF

- Page count, text extraction and metadata checks pass.
- Every page is raster-rendered and visually inspected.
- No overflow, missing glyph, hidden/white text, cutoff or broken link annotation.

## V18 negative-regression checks

The comparison report evaluates v19 against the following prohibited regression classes without treating them as automatically proven defects in any particular file:

| Regression ID | V18 benchmark risk | V19 required behavior |
|---|---|---|
| V18-R01 | `.com` domain contamination | Approved `.com.au` boundary with zero wrong-domain targets |
| V18-R02 | Duplicate pages/files | Normalized URL uniqueness and manifest duplicate reconciliation |
| V18-R03 | Unsupported schema/rating data | Fact-backed page-specific schema or omission |
| V18-R04 | Unsupported disavow candidates | Disabled unless full evidence and admin approval exist |
| V18-R05 | Generic redirects | Page-specific validated graph with safe targets and approval |
| V18-R06 | Stale QA | QA captured for exact v19 bytes/run/ruleset |
| V18-R07 | Contradictory counts | Cross-format reconciliation to canonical records |
| V18-R08 | Arbitrary content padding | Evidence-qualified distinct assets only, up to 20 |

The quality comparison must cite concrete v18 and v19 file/row/check evidence for each scored comparison. If the v18 source cannot be parsed or a category cannot be compared fairly, state unavailable rather than assume failure.

## Staging and production acceptance

### Staging

- Exact release image digest deployed.
- `/healthz/` and `/readyz/` pass at the external staging URL.
- Web, analysis, render and scheduler process checks pass.
- Authentication/recovery and all role boundaries pass.
- Enterprise run completes with source availability truthfully represented.
- Gate 1 and Gate 2 are completed by authorized reviewers.
- High-risk artifacts are approved only by an agency administrator.
- Full package renders and all hard gates pass.
- Database backup and isolated restore test pass, including representative object hashes.

### Production promotion

Promotion is permitted only after staging evidence for the exact image/package version is signed off. Production must use separate secrets, database, Redis and private bucket. After deploy, repeat readiness, role isolation, queue, storage and authorized-download smoke checks before enabling new client runs.

The Studio may be production-ready while a private source is unavailable, but the Kakawa package cannot claim evidence from that source and must meet coverage/claim rules without it.

## Machine-readable acceptance record

`06_QA_and_Manifest/QA_Report.json` should contain, at minimum:

```json
{
  "schema_version": "1.0",
  "run_id": "uuid",
  "package_version": "v19",
  "generated_at": "ISO-8601 timestamp",
  "source_cutoff_at": "ISO-8601 timestamp or null",
  "mode": "live | partial_live | replay",
  "overall_status": "pass | fail | not_evaluated",
  "credential_secrets_included": false,
  "sources": [
    {
      "source": "gsc",
      "status": "available | partial | unavailable",
      "reason": "safe explicit reason or null",
      "captured_at": "ISO-8601 timestamp or null",
      "record_count": 0,
      "snapshot_sha256": "hash or null"
    }
  ],
  "checks": [
    {
      "code": "KAK-G01",
      "version": "1.0",
      "severity": "critical",
      "status": "pass | fail | warn | skip",
      "message": "human-safe result",
      "evidence_ids": []
    }
  ],
  "counts": {},
  "manifest_sha256": "64 lowercase hex",
  "package_sha256": "64 lowercase hex"
}
```

`mode=replay` cannot prove a fresh live crawl, live provider coverage or production deployment. It may pass deterministic renderer/QA regression checks, but the live-specific gates remain not evaluated.

## Sign-off record

The package sign-off includes:

| Sign-off | Required person | Evidence |
|---|---|---|
| Technical QA | Analyst not solely responsible for the same artifact, where staffing permits | QA JSON/hash and renderer inspection record |
| Gate 1 | Agency administrator or assigned client reviewer | Canonical approval row |
| Gate 2 | Agency administrator or assigned client reviewer | Canonical approval row |
| High-risk assets | Agency administrator | One approval per exact risky artifact/version |
| Final package | Agency administrator or assigned client reviewer | Package approval tied to manifest/package hash |
| Production promotion | Authorized operator/change approver | Staging evidence and deployment change record |

No checkbox or signature in this Markdown file substitutes for the canonical approval rows and package hashes.
