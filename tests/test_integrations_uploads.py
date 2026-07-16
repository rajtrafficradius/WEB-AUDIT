from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from integrations.uploads import ImportLimits, UploadValidationError, validate_import


def write_csv(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="")
    return path


def test_valid_csv_returns_hash_shape_and_headers(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "evidence.csv", "url,status\nhttps://example.com/,200\n")
    report = validate_import(path, allowed_root=tmp_path)
    assert report.media_type == "text/csv"
    assert len(report.sha256) == 64
    assert report.sheets[0].headers == ("url", "status")
    assert report.sheets[0].row_count == 2


@pytest.mark.parametrize("payload", ["name,value\nsafe,=2+2\n", "name,value\nsafe,@cmd\n"])
def test_csv_rejects_formula_injection(tmp_path: Path, payload: str) -> None:
    path = write_csv(tmp_path / "unsafe.csv", payload)
    with pytest.raises(UploadValidationError, match="formula") as caught:
        validate_import(path)
    assert caught.value.code == "formula"


def test_csv_allows_negative_numeric_observation(tmp_path: Path) -> None:
    report = validate_import(write_csv(tmp_path / "numeric.csv", "change\n-12.5\n"))
    assert report.sheets[0].row_count == 2


def test_csv_rejects_duplicate_and_blank_headers(tmp_path: Path) -> None:
    with pytest.raises(UploadValidationError) as duplicate:
        validate_import(write_csv(tmp_path / "duplicate.csv", "URL,url\na,b\n"))
    assert duplicate.value.code == "duplicate_header"
    with pytest.raises(UploadValidationError) as blank:
        validate_import(write_csv(tmp_path / "blank.csv", "url,\na,b\n"))
    assert blank.value.code == "blank_header"


def test_csv_parser_errors_are_safe_validation_failures(tmp_path: Path) -> None:
    oversized_field = "x" * 150_000
    path = write_csv(tmp_path / "malformed.csv", f"name,value\nsafe,{oversized_field}\n")
    with pytest.raises(UploadValidationError) as caught:
        validate_import(path)
    assert caught.value.code == "malformed_csv"
    assert caught.value.safe_message == "CSV structure could not be parsed safely."


def test_valid_xlsx_is_scanned_without_extraction(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["url", "status"])
    sheet.append(["https://example.com/", 200])
    path = tmp_path / "evidence.xlsx"
    workbook.save(path)
    report = validate_import(path)
    assert report.sheets[0].headers == ("url", "status")
    assert report.sheets[0].row_count == 2


def test_xlsx_rejects_formulas(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["url", "score"])
    sheet.append(["https://example.com/", "=1+1"])
    path = tmp_path / "formula.xlsx"
    workbook.save(path)
    with pytest.raises(UploadValidationError) as caught:
        validate_import(path)
    assert caught.value.code == "formula"


def test_xlsx_rejects_external_relationships(tmp_path: Path) -> None:
    path = tmp_path / "external.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            "<Relationships><Relationship TargetMode='External' Target='https://attacker.test'/></Relationships>",
        )
        archive.writestr("xl/worksheets/sheet1.xml", "<worksheet/>")
    with pytest.raises(UploadValidationError) as caught:
        validate_import(path)
    assert caught.value.code == "external_link"


def test_xlsx_rejects_macros_and_unsafe_compression(tmp_path: Path) -> None:
    macro = tmp_path / "macro.xlsx"
    with zipfile.ZipFile(macro, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
        archive.writestr("xl/vbaProject.bin", b"not-executable-test-fixture")
    with pytest.raises(UploadValidationError) as caught:
        validate_import(macro)
    assert caught.value.code == "active_content"

    bomb = tmp_path / "bomb.xlsx"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
        archive.writestr("xl/worksheets/sheet1.xml", "A" * 20_000)
    with pytest.raises(UploadValidationError) as compressed:
        validate_import(bomb, limits=ImportLimits(max_compression_ratio=2))
    assert compressed.value.code == "zip_bomb"


def test_xlsx_rejects_duplicate_normalized_archive_paths(tmp_path: Path) -> None:
    path = tmp_path / "ambiguous.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
        archive.writestr("XL\\WORKBOOK.XML", "<different/>")
        archive.writestr("xl/worksheets/sheet1.xml", "<worksheet/>")
    with pytest.raises(UploadValidationError) as caught:
        validate_import(path)
    assert caught.value.code == "duplicate_part"


def test_upload_root_blocks_path_escape_and_legacy_extensions(tmp_path: Path) -> None:
    permitted = tmp_path / "permitted"
    permitted.mkdir()
    outside = write_csv(tmp_path / "outside.csv", "name\nvalue\n")
    with pytest.raises(UploadValidationError) as caught:
        validate_import(outside, allowed_root=permitted)
    assert caught.value.code == "path_escape"
    legacy = tmp_path / "legacy.xlsm"
    legacy.write_bytes(b"not a workbook")
    with pytest.raises(UploadValidationError) as legacy_error:
        validate_import(legacy)
    assert legacy_error.value.code == "active_or_legacy"
