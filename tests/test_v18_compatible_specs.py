from __future__ import annotations

import json
from pathlib import Path

from exporters.v18_workbook_specs import workbook_specs

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = PROJECT_ROOT / "fixtures" / "replay" / "kakawa_acceptance_data.json"


def _data() -> dict:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def _sheet(specs: list[dict], path: str, name: str) -> dict:
    workbook = next(item for item in specs if item["path"] == path)
    return next(item for item in workbook["sheets"] if item["name"] == name)


def test_enhanced_acceptance_dataset_is_deep_unique_and_evidence_bound() -> None:
    data = _data()

    assert len(data["pages"]) == 357
    assert len(data["actions"]) == 48
    assert len(data["content_assets"]) == 20
    assert len(data["deployment"]["metadata_review"]) == 314
    assert len(data["deployment"]["internal_link_candidates"]) == 120
    assert len({item["target_url"] for item in data["content_assets"]}) == 20
    assert all(item["evidence_ids"] for item in data["content_assets"])
    assert all(item["evidence_ids"] for item in data["actions"])


def test_v18_compatible_workbooks_preserve_domain_specific_sheet_depth() -> None:
    specs = workbook_specs(_data())

    assert len(specs) == 29
    assert sum(len(item["sheets"]) for item in specs) == 73
    assert len(_sheet(specs, "01_Audit_Reports/Technical_Audit_Report.xlsx", "Full Site Inventory")["rows"]) == 357
    assert len(_sheet(specs, "01_Audit_Reports/Content_Audit_Workbook.xlsx", "Full Page Inventory")["rows"]) == 357
    assert len(_sheet(specs, "01_Audit_Reports/Ecommerce_Audit_Report.xlsx", "Product Pages")["rows"]) == 209
    assert len(_sheet(specs, "03_Action_Plan/16_Week_Action_Plan.xlsx", "16-Week Action Plan")["rows"]) == 48
    assert len(_sheet(specs, "04_Implementation_Deliverables/On_Page_Optimizations/Meta_Tags.xlsx", "Title Tags")["rows"]) == 314
    assert len(_sheet(specs, "06_QA/QC_Report_v12.xlsx", "QC Results")["rows"]) >= 1_000


def test_quality_benchmark_records_pre_and_post_enhancement_scores() -> None:
    benchmark = _data()["quality_benchmark"]
    scores = {item["version"]: item["total"] for item in benchmark["packages"]}

    assert scores == {
        "V18 benchmark": 69,
        "V19 before enhancement": 74,
        "V19 enhanced": 94,
    }