"""Upload scanning: real file type from content, archive safety, encodings, entity detection."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.datacenter.scanning import (
    UploadRejected,
    detect_csv_entity,
    detect_kind,
    parse_xlsx,
)
from app.datacenter.templates import entity_csv, inventory_workbook
from tests.helpers import minimal_docx, minimal_pdf, sample_workbook


def rejected(code: str, filename: str, data: bytes) -> None:
    with pytest.raises(UploadRejected) as exc:
        detect_kind(filename, data)
    assert exc.value.code == code, exc.value.message


def test_detects_each_supported_kind() -> None:
    assert detect_kind("units.csv", b"project_code,unit_number\nSG,A-1\n") == "csv"
    assert detect_kind("inventory.xlsx", sample_workbook()) == "xlsx"
    assert detect_kind("brochure.pdf", minimal_pdf("hello")) == "pdf"
    assert detect_kind("faq.docx", minimal_docx("hello")) == "docx"


def test_extension_must_match_content() -> None:
    rejected("extension_mismatch", "units.csv", minimal_pdf("x"))
    rejected("extension_mismatch", "brochure.pdf", b"a,b\n1,2\n")
    rejected("extension_mismatch", "data.xlsx", minimal_docx("x"))


def test_empty_binary_and_bad_encoding() -> None:
    rejected("empty_file", "units.csv", b"")
    rejected("unsupported_type", "units.csv", b"\x00\x01\x02binary")
    rejected("bad_encoding", "units.csv", "naïve".encode("latin-1"))


def _zip(entries: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return out.getvalue()


def test_macros_are_refused() -> None:
    data = _zip({"xl/workbook.xml": b"<x/>", "xl/vbaProject.bin": b"macro"})
    rejected("macros_not_allowed", "book.xlsx", data)


def test_zip_bomb_ratio_is_refused() -> None:
    data = _zip({"xl/workbook.xml": b"<x/>", "xl/sheet.xml": b"0" * 5_000_000})
    rejected("unsafe_file", "book.xlsx", data)


def test_other_zip_files_are_refused() -> None:
    rejected("unsupported_type", "archive.xlsx", _zip({"readme.txt": b"hi"}))


def test_corrupt_zip() -> None:
    rejected("corrupt_file", "book.xlsx", b"PK\x03\x04not really a zip")


def test_csv_entity_detection() -> None:
    assert detect_csv_entity("units.csv", [], None) == "units"
    assert detect_csv_entity("march_price_lists.csv", [], None) == "price_lists"
    assert detect_csv_entity("upload.csv", ["project_code", "name", "milestones"], None) == (
        "payment_plans"
    )
    assert detect_csv_entity("whatever.csv", [], "offers") == "offers"
    assert detect_csv_entity("upload.csv", ["project_code", "name"], None) == "amenities"
    with pytest.raises(UploadRejected) as exc:
        detect_csv_entity("upload.csv", ["project_code", "colour"], None)
    assert exc.value.code == "unknown_entity"


def test_workbook_parsing_ignores_readme_and_reports_unknown_sheets() -> None:
    parsed = parse_xlsx(sample_workbook({"Notes": [["anything"]]}))
    assert [s.entity for s in parsed.sheets] == [
        "projects",
        "unit_types",
        "units",
        "price_lists",
        "offers",
        "payment_plans",
        "amenities",
    ]
    assert parsed.ignored_sheets == ["Notes"]
    units = next(s for s in parsed.sheets if s.entity == "units")
    assert len(units.rows) == 14 and units.rows[0][0] == 2


def test_workbook_without_data_sheets() -> None:
    from openpyxl import Workbook

    wb = Workbook()
    out = io.BytesIO()
    wb.save(out)
    with pytest.raises(UploadRejected) as exc:
        parse_xlsx(out.getvalue())
    assert exc.value.code == "no_data_sheets"


def test_templates() -> None:
    assert entity_csv("units").decode("utf-8-sig").startswith("project_code,unit_number,")
    parsed = parse_xlsx(inventory_workbook())
    assert len(parsed.sheets) == 7 and all(s.rows == [] for s in parsed.sheets)

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(inventory_workbook()))
    readme = [str(c.value) for row in wb["README"].iter_rows() for c in row if c.value]
    assert any("rupees (INR)" in line for line in readme)
    assert any("YYYY-MM-DD" in line for line in readme)
    assert any("square feet" in line.lower() for line in readme)
