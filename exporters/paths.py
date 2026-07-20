"""Single source of truth for the client deliverable package tree.

Every module that writes, verifies, manifests, or documents the package imports
its folder and file names from here. No other module may contain a path literal
for a package member; that is what keeps the assembler, the manifest, the tree
verifier, and the AUDIT_RESULTS.md contents table from drifting apart.

The taxonomy mirrors the V18 benchmark folder layout. Where V18 shipped
byte-identical duplicates we ship exactly one payload: duplication is a defect
the manifest actively forbids, not a target to reproduce.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

# --------------------------------------------------------------------- folders

AUDIT_REPORTS = "01_Audit_Reports"
STRATEGY_DOCUMENTS = "02_Strategy_Documents"
ACTION_PLAN = "03_Action_Plan"
IMPLEMENTATION = "04_Implementation_Deliverables"
TECHNICAL_FIXES = f"{IMPLEMENTATION}/Technical_Fixes"
ON_PAGE_OPTIMIZATIONS = f"{IMPLEMENTATION}/On_Page_Optimizations"
SCHEMA_MARKUP = f"{IMPLEMENTATION}/Schema_Markup"
LINK_BUILDING = f"{IMPLEMENTATION}/Link_Building"
SEO_CONTENT = "05_SEO_Content"
QA = "06_QA"
SLIDE_DECK = "07_Slide_Deck"

TOP_LEVEL_FOLDERS: tuple[str, ...] = (
    AUDIT_REPORTS,
    STRATEGY_DOCUMENTS,
    ACTION_PLAN,
    IMPLEMENTATION,
    SEO_CONTENT,
    QA,
    SLIDE_DECK,
)

#: Folders that are always created, even before any renderer has written a file.
REQUIRED_FOLDERS: tuple[str, ...] = (
    AUDIT_REPORTS,
    STRATEGY_DOCUMENTS,
    ACTION_PLAN,
    TECHNICAL_FIXES,
    ON_PAGE_OPTIMIZATIONS,
    SCHEMA_MARKUP,
    QA,
    SLIDE_DECK,
)

# ----------------------------------------------------------------------- files

SUMMARY_MARKDOWN = "AUDIT_RESULTS.md"

# 01_Audit_Reports
TECHNICAL_AUDIT_XLSX = f"{AUDIT_REPORTS}/Technical_Audit_Report.xlsx"
CONTENT_AUDIT_XLSX = f"{AUDIT_REPORTS}/Content_Audit_Workbook.xlsx"
BACKLINK_AUDIT_XLSX = f"{AUDIT_REPORTS}/Backlink_Audit_Report.xlsx"
COMPETITOR_LANDSCAPE_XLSX = f"{AUDIT_REPORTS}/Competitor_Landscape_Analysis.xlsx"
ECOMMERCE_AUDIT_XLSX = f"{AUDIT_REPORTS}/Ecommerce_Audit_Report.xlsx"
GEO_AEO_XLSX = f"{AUDIT_REPORTS}/GEO_AEO_Readiness_Scorecard.xlsx"
CRO_UX_XLSX = f"{AUDIT_REPORTS}/CRO_UX_Findings.xlsx"
TRACKING_AUDIT_XLSX = f"{AUDIT_REPORTS}/Tracking_Audit_Report.xlsx"
GBP_LOCAL_XLSX = f"{AUDIT_REPORTS}/GBP_Local_Audit.xlsx"
BASELINE_PERFORMANCE_XLSX = f"{AUDIT_REPORTS}/Baseline_Performance_Analysis.xlsx"
ENTERPRISE_AUDIT_PDF = f"{AUDIT_REPORTS}/Enterprise_SEO_Audit_Report.pdf"

# 02_Strategy_Documents
MASTER_KEYWORD_UNIVERSE_XLSX = f"{STRATEGY_DOCUMENTS}/Master_Keyword_Universe.xlsx"
CONTENT_GAP_XLSX = f"{STRATEGY_DOCUMENTS}/Content_Gap_Analysis.xlsx"
CONTENT_STRATEGY_XLSX = f"{STRATEGY_DOCUMENTS}/Content_Strategy.xlsx"
CONTENT_STRATEGY_DOCX = f"{STRATEGY_DOCUMENTS}/Content_Strategy.docx"
CONTENT_STRATEGY_PDF = f"{STRATEGY_DOCUMENTS}/Content_Strategy.pdf"
URL_ARCHITECTURE_XLSX = f"{STRATEGY_DOCUMENTS}/URL_Architecture_Map.xlsx"
CANNIBALIZATION_XLSX = f"{STRATEGY_DOCUMENTS}/Cannibalization_Resolution_Plan.xlsx"
SEO_STRATEGY_DOCX = f"{STRATEGY_DOCUMENTS}/SEO_Strategy.docx"
SEO_STRATEGY_PDF = f"{STRATEGY_DOCUMENTS}/SEO_Strategy.pdf"

# 03_Action_Plan
ACTION_PLAN_XLSX = f"{ACTION_PLAN}/16_Week_Action_Plan.xlsx"
ACTION_PLAN_CSV = f"{ACTION_PLAN}/16_Week_Action_Plan.csv"
ACTION_PLAN_PDF = f"{ACTION_PLAN}/16_Week_Action_Plan.pdf"

# 04_Implementation_Deliverables
REDIRECT_MAP_CSV = f"{TECHNICAL_FIXES}/Redirect_Map.csv"
REDIRECT_MAP_XLSX = f"{TECHNICAL_FIXES}/Redirect_Map.xlsx"
CANONICAL_FIXES_XLSX = f"{TECHNICAL_FIXES}/Canonical_Fixes.xlsx"
ROBOTS_RECOMMENDATIONS_TXT = f"{TECHNICAL_FIXES}/Robots_txt_Recommendations.txt"
TITLE_TAG_XLSX = f"{ON_PAGE_OPTIMIZATIONS}/Title_Tag_Optimizations.xlsx"
META_DESCRIPTION_XLSX = f"{ON_PAGE_OPTIMIZATIONS}/Meta_Description_Optimizations.xlsx"
H1_TAGS_XLSX = f"{ON_PAGE_OPTIMIZATIONS}/H1_Tags.xlsx"
INTERNAL_LINK_MAP_XLSX = f"{ON_PAGE_OPTIMIZATIONS}/Internal_Link_Map.xlsx"
SCHEMA_ORGANIZATION_JSON = f"{SCHEMA_MARKUP}/Schema_Organization.json"
SCHEMA_LOCAL_BUSINESS_JSON = f"{SCHEMA_MARKUP}/Schema_LocalBusiness.json"
SCHEMA_PRODUCT_TEMPLATE_JSON = f"{SCHEMA_MARKUP}/Schema_Product_Template.json"
REFERRING_DOMAINS_XLSX = f"{LINK_BUILDING}/Referring_Domains.xlsx"
LINK_GAP_XLSX = f"{LINK_BUILDING}/Link_Gap_Opportunities.xlsx"

# 06_QA
QA_REPORT_PDF = f"{QA}/QA_Report.pdf"
QA_REPORT_JSON = f"{QA}/QA_Report.json"
QC_REPORT_XLSX = f"{QA}/QC_Report.xlsx"
EVIDENCE_INDEX_CSV = f"{QA}/evidence_index.csv"
ISSUE_REGISTER_CSV = f"{QA}/issue_register.csv"
AVAILABILITY_MATRIX_CSV = f"{QA}/availability_matrix.csv"
GENERATION_LEDGER_CSV = f"{QA}/generation_ledger.csv"
SOURCE_COVERAGE_CSV = f"{QA}/source_coverage.csv"
CHANGE_LOG_CSV = f"{QA}/change_log.csv"
PACKAGE_MANIFEST_JSON = f"{QA}/package-manifest.json"
CHECKSUMS_SHA256 = f"{QA}/checksums.sha256"

# 07_Slide_Deck
DECK_PPTX = f"{SLIDE_DECK}/Executive_Deck.pptx"
DECK_PDF = f"{SLIDE_DECK}/Executive_Deck.pdf"
DECK_HTML = f"{SLIDE_DECK}/Executive_Deck.html"

# -------------------------------------------------------------- control files

#: Basenames that the manifest never enumerates (they cover the manifest itself).
CONTROL_BASENAMES = frozenset({"package-manifest.json", "checksums.sha256"})

#: Concrete control paths for the current tree plus the legacy 06_QA_and_Manifest
#: tree, so older packages and the legacy Kakawa builder keep verifying.
LEGACY_QA = "06_QA_and_Manifest"
CONTROL_FILES = frozenset(
    {
        PACKAGE_MANIFEST_JSON,
        CHECKSUMS_SHA256,
        f"{LEGACY_QA}/package-manifest.json",
        f"{LEGACY_QA}/checksums.sha256",
    }
)


def qa_folder(package_root: Path) -> Path:
    """The QA folder of an existing package, tolerating the legacy layout.

    New packages use ``06_QA``. Packages assembled by the legacy Kakawa builder
    use ``06_QA_and_Manifest``; their control files must stay where their
    verifier expects them rather than being silently relocated.
    """
    root = Path(package_root)
    if not (root / QA).is_dir() and (root / LEGACY_QA).is_dir():
        return root / LEGACY_QA
    return root / QA


def is_control_file(relative: str) -> bool:
    """True when ``relative`` is a manifest control file in any QA folder."""
    parts = PurePosixPath(relative).parts
    if len(parts) != 2:
        return relative in CONTROL_FILES
    folder, name = parts
    return name in CONTROL_BASENAMES and folder.startswith("06_")


# ------------------------------------------------ artifact typing and approval

#: Longest-prefix wins, so ``04_.../On_Page_Optimizations`` beats bare ``04_...``.
FOLDER_PROFILES: dict[str, tuple[str, str]] = {
    AUDIT_REPORTS: ("audit_workbook", "approved"),
    STRATEGY_DOCUMENTS: ("strategy_document", "approved"),
    ACTION_PLAN: ("action_plan", "approved"),
    IMPLEMENTATION: ("deployment_asset", "withheld_pending_approval"),
    TECHNICAL_FIXES: ("deployment_asset", "withheld_pending_approval"),
    ON_PAGE_OPTIMIZATIONS: ("onpage_proposal", "withheld_pending_editorial_review"),
    SCHEMA_MARKUP: ("schema_template", "withheld_pending_approval"),
    LINK_BUILDING: ("link_opportunity", "withheld_pending_approval"),
    SEO_CONTENT: ("content_brief", "withheld_pending_human_approval"),
    QA: ("qa_control", "approved"),
    SLIDE_DECK: ("executive_deck", "approved"),
}

#: Suffixes that may be declared a cross-format derivative of a sibling payload.
DERIVED_SUFFIXES: tuple[str, ...] = (".pdf", ".html", ".csv")

#: Preference order for the source a derivative is declared against.
DERIVATIVE_SOURCE_SUFFIXES: tuple[str, ...] = (".pptx", ".docx", ".xlsx")

#: CSVs under 04_Implementation_Deliverables that must not ship header-only when
#: the run data actually holds rows for them: relative path -> (section, key).
DEPLOYMENT_CSV_SOURCES: dict[str, tuple[str, str]] = {
    REDIRECT_MAP_CSV: ("deployment", "redirect_candidates"),
}


def entry_profile(relative: str) -> tuple[str, str]:
    """Return ``(artifact_type, approval_state)`` for a package-relative path."""
    posix = PurePosixPath(relative)
    if len(posix.parts) == 1:
        return ("summary_markdown", "approved")
    parent = posix.parent.as_posix()
    candidates = [key for key in FOLDER_PROFILES if parent == key or parent.startswith(key + "/")]
    if not candidates:
        return ("package_file", "approved")
    return FOLDER_PROFILES[max(candidates, key=len)]


def package_path(root: Path, relative: str) -> Path:
    """Resolve a package-relative POSIX path against the package root."""
    return Path(root).joinpath(*PurePosixPath(relative).parts)


def ensure_folders(root: Path) -> None:
    """Create the folders every package always carries."""
    for folder in REQUIRED_FOLDERS:
        package_path(root, folder).mkdir(parents=True, exist_ok=True)


def content_asset_path(root: Path, filename: str) -> Path:
    """Path for one ``CONTENT-NN_<slug>.docx`` asset."""
    return package_path(root, SEO_CONTENT) / filename
