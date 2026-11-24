"""Data Center validation rules (pure: no database)."""

from __future__ import annotations

from datetime import date

import pytest

from app.core.enums import RecordStatus, Severity
from app.datacenter.scanning import ParsedFile, Sheet, parse_csv
from app.datacenter.validation import (
    ExistingIndex,
    ExistingRow,
    Finding,
    ValidationResult,
    parse_milestones,
    validate,
)

PROJECT = "project_code,name,city,possession_date"
UNIT = "project_code,unit_number,unit_type_code,bhk,property_type,price_inr,availability"
PRICE = "project_code,unit_type_code,unit_number,base_price_inr,price_max_inr"


def run(entity: str, *lines: str, existing: ExistingIndex | None = None) -> ValidationResult:
    data = ("\n".join(lines) + "\n").encode()
    parsed = parse_csv(f"{entity}.csv", data, None)
    return validate(parsed, existing or {})


def codes(result: ValidationResult, severity: Severity = Severity.ERROR) -> list[str]:
    return [f.code for f in result.findings if f.severity == severity]


def only(result: ValidationResult, code: str) -> Finding:
    found = [f for f in result.findings if f.code == code]
    assert len(found) == 1, result.findings
    return found[0]


def existing_project(
    code: str = "SG", status: RecordStatus = RecordStatus.APPROVED, **data: object
) -> ExistingIndex:
    values = {
        "project_code": code,
        "name": "Sunrise Greens",
        "city": "Kolkata",
        "locality": None,
        "address": None,
        "description": None,
        "rera_number": None,
        "possession_date": None,
        "valid_from": None,
        "valid_to": None,
        **data,
    }
    return {"projects": {(code,): [ExistingRow("p1", status, 1, values)]}}  # type: ignore[dict-item]


# ----------------------------------------------------------------------------- valid data


def test_valid_rows_pass_and_are_normalised() -> None:
    result = run("projects", PROJECT, "SG,Sunrise Greens,Kolkata,2027-12-31")
    assert result.findings == []
    [row] = result.rows
    assert row.key == ("SG",)
    assert row.data["possession_date"] == "2027-12-31"
    assert row.change == "new"


def test_money_accepts_grouping_and_rupee_sign() -> None:
    result = run(
        "units",
        UNIT,
        'SG,A-1,,2,apartment,"₹72,00,000",available',
        "SG,A-2,,2,Apartment,7200000.00,Available",
        existing=existing_project(),
    )
    assert codes(result) == []
    assert [r.data["price_inr"] for r in result.rows] == [7_200_000, 7_200_000]
    assert result.rows[1].data["property_type"] == "apartment"


def test_excel_date_cells_are_accepted() -> None:
    sheet = Sheet(
        "projects",
        ["project_code", "name", "city", "possession_date"],
        [
            (
                2,
                {
                    "project_code": "SG",
                    "name": "X",
                    "city": "Kolkata",
                    "possession_date": date(2027, 12, 31),
                },
            )
        ],
    )
    result = validate(ParsedFile([sheet]), {})
    assert result.findings == []
    assert result.rows[0].data["possession_date"] == "2027-12-31"


# ------------------------------------------------------------------------- column checks


def test_missing_required_column() -> None:
    result = run("projects", "project_code,name", "SG,Sunrise")
    assert only(result, "missing_column").column == "city"
    assert result.rows == []


def test_unknown_column_is_a_warning() -> None:
    result = run("projects", PROJECT + ",colour", "SG,X,Kolkata,,blue")
    assert codes(result) == []
    assert only(result, "unknown_column").severity == Severity.WARNING


def test_empty_file() -> None:
    assert codes(run("projects", PROJECT)) == ["empty_file"]


# --------------------------------------------------------------------------- value checks


def test_required_value() -> None:
    result = run("projects", PROJECT, "SG,,Kolkata,")
    f = only(result, "required")
    assert (f.row_number, f.column) == (2, "name")


@pytest.mark.parametrize("price", ["0", "-500000"])
def test_price_must_be_positive(price: str) -> None:
    result = run(
        "units", UNIT, f"SG,A-1,,2,apartment,{price},available", existing=existing_project()
    )
    assert codes(result) == ["price_not_positive"]


def test_price_in_words_is_rejected_with_a_hint() -> None:
    result = run(
        "units", UNIT, "SG,A-1,,2,apartment,72 lakh,available", existing=existing_project()
    )
    f = only(result, "invalid_number")
    assert "not lakh/crore words" in f.message


def test_fractional_rupees_rejected() -> None:
    result = run(
        "units", UNIT, "SG,A-1,,2,apartment,7200000.50,available", existing=existing_project()
    )
    assert codes(result) == ["invalid_number"]


@pytest.mark.parametrize("area", ["0", "-10"])
def test_area_must_be_positive(area: str) -> None:
    result = run(
        "unit_types",
        "project_code,unit_type_code,name,property_type,bhk,carpet_area_sqft",
        f"SG,2BHK,Two,apartment,2,{area}",
        existing=existing_project(),
    )
    assert codes(result) == ["area_not_positive"]


@pytest.mark.parametrize("value", ["31/12/2027", "2027-13-01", "2026-02-30", "Dec 2027"])
def test_possession_date_format(value: str) -> None:
    result = run("projects", PROJECT, f"SG,X,Kolkata,{value}")
    assert codes(result) == ["invalid_date"]


def test_whole_numbers_only_for_bhk() -> None:
    result = run("units", UNIT, "SG,A-1,,2.5,apartment,,available", existing=existing_project())
    assert codes(result) == ["invalid_number"]


def test_choice_values() -> None:
    result = run("units", UNIT, "SG,A-1,,2,castle,,on hold", existing=existing_project())
    assert codes(result) == ["invalid_choice", "invalid_choice"]


def test_formula_like_text_is_rejected() -> None:
    result = run("projects", PROJECT, 'SG,"=HYPERLINK(""x"")",Kolkata,')
    assert codes(result) == ["formula"]


def test_text_length_limit() -> None:
    result = run("projects", PROJECT, f"SG,{'x' * 201},Kolkata,")
    assert codes(result) == ["too_long"]


def test_valid_to_before_valid_from() -> None:
    result = run(
        "projects", PROJECT + ",valid_from,valid_to", "SG,X,Kolkata,,2027-01-01,2026-01-01"
    )
    assert codes(result) == ["date_range"]


def test_percent_range() -> None:
    result = run(
        "offers",
        "project_code,title,discount_percent",
        "SG,Big sale,150",
        existing=existing_project(),
    )
    assert codes(result) == ["invalid_percent"]


def test_offer_cannot_have_both_discounts() -> None:
    result = run(
        "offers",
        "project_code,title,discount_inr,discount_percent",
        "SG,Sale,100000,5",
        existing=existing_project(),
    )
    assert codes(result) == ["conflicting_fields"]


def test_price_list_target_and_range() -> None:
    result = run(
        "price_lists",
        PRICE,
        "SG,2BHK,A-1,7200000,",
        "SG,3BHK,,9000000,8000000",
        existing=existing_project(),
    )
    assert "conflicting_fields" in codes(result)
    assert "price_range" in codes(result)


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("Booking:10; Agreement:20; Possession:70", True),
        ("Booking:10%;Possession:90%", True),
        ("Booking:10; Possession:80", False),
        ("Booking 10; Possession:90", False),
        ("Booking:ten; Possession:90", False),
        ("", False),
    ],
)
def test_milestones(text: str, ok: bool) -> None:
    if ok:
        stages = parse_milestones(text)
        assert sum(float(s["percent"]) for s in stages) == 100
    else:
        with pytest.raises(ValueError):
            parse_milestones(text)


# -------------------------------------------------------------------- references


def test_unknown_project_reference() -> None:
    result = run("units", UNIT, "ZZ,A-1,,2,apartment,,available")
    f = only(result, "unknown_project")
    assert "ZZ does not exist" in f.message


def test_reference_to_project_in_same_workbook_is_fine() -> None:
    projects = Sheet(
        "projects",
        ["project_code", "name", "city"],
        [(2, {"project_code": "NEW", "name": "New", "city": "Kolkata"})],
    )
    units = Sheet(
        "units",
        UNIT.split(","),
        [
            (
                2,
                {
                    "project_code": "NEW",
                    "unit_number": "A-1",
                    "bhk": 2,
                    "property_type": "apartment",
                    "availability": "available",
                },
            )
        ],
    )
    result = validate(ParsedFile([units, projects]), {})
    assert codes(result) == []


def test_unknown_unit_type_and_unit() -> None:
    units = run("units", UNIT, "SG,A-1,9BHK,2,apartment,,available", existing=existing_project())
    assert codes(units) == ["unknown_unit_type"]
    lists = run("price_lists", PRICE, "SG,,Z-9,7200000,", existing=existing_project())
    assert codes(lists) == ["unknown_unit"]


def test_reference_to_unapproved_project_is_a_warning() -> None:
    result = run(
        "units",
        UNIT,
        "SG,A-1,,2,apartment,,available",
        existing=existing_project(status=RecordStatus.DRAFT),
    )
    assert codes(result) == []
    assert only(result, "references_unapproved").severity == Severity.WARNING


def test_archived_project_does_not_count() -> None:
    result = run(
        "units",
        UNIT,
        "SG,A-1,,2,apartment,,available",
        existing=existing_project(status=RecordStatus.ARCHIVED),
    )
    assert codes(result) == ["unknown_project"]


# --------------------------------------------------------- duplicates and contradictions


def test_duplicate_row_in_file() -> None:
    result = run(
        "units",
        UNIT,
        "SG,A-1,,2,apartment,7200000,available",
        "SG,A-1,,2,apartment,7200000,available",
        existing=existing_project(),
    )
    f = only(result, "duplicate_in_file")
    assert f.row_number == 3 and "repeats row 2" in f.message


def test_same_unit_different_price_in_file_is_a_contradiction() -> None:
    result = run(
        "units",
        UNIT,
        "SG,A-1,,2,apartment,7200000,available",
        "SG,A-1,,2,apartment,7500000,available",
        existing=existing_project(),
    )
    f = only(result, "contradiction")
    assert "price_inr 7200000 vs 7500000" in f.message


def test_unit_price_contradicts_price_list_in_same_batch() -> None:
    units = Sheet(
        "units",
        UNIT.split(","),
        [
            (
                2,
                {
                    "project_code": "SG",
                    "unit_number": "A-1",
                    "bhk": 2,
                    "property_type": "apartment",
                    "price_inr": 7_200_000,
                    "availability": "available",
                },
            )
        ],
    )
    lists = Sheet(
        "price_lists",
        PRICE.split(","),
        [(2, {"project_code": "SG", "unit_number": "A-1", "base_price_inr": 7_500_000})],
    )
    result = validate(ParsedFile([units, lists]), existing_project())
    assert codes(result) == ["contradiction", "contradiction"]
    assert {f.entity for f in result.errors} == {"units", "price_lists"}


def test_unit_bhk_must_match_its_unit_type() -> None:
    types = Sheet(
        "unit_types",
        ["project_code", "unit_type_code", "name", "property_type", "bhk"],
        [
            (
                2,
                {
                    "project_code": "SG",
                    "unit_type_code": "3BHK",
                    "name": "Three",
                    "property_type": "apartment",
                    "bhk": 3,
                },
            )
        ],
    )
    units = Sheet(
        "units",
        UNIT.split(","),
        [
            (
                2,
                {
                    "project_code": "SG",
                    "unit_number": "A-1",
                    "unit_type_code": "3BHK",
                    "bhk": 2,
                    "property_type": "apartment",
                    "availability": "available",
                },
            )
        ],
    )
    result = validate(ParsedFile([types, units]), existing_project())
    assert "is 2 BHK but unit type 3BHK is 3 BHK" in only(result, "contradiction").message


# -------------------------------------------------------------- comparison with existing


def test_identical_to_approved_version_is_unchanged_warning() -> None:
    result = run("projects", PROJECT, "SG,Sunrise Greens,Kolkata,", existing=existing_project())
    assert codes(result) == []
    assert result.rows[0].change == "unchanged"
    assert "re-confirms" in only(result, "unchanged").message


def test_changed_fields_are_listed() -> None:
    result = run(
        "projects", PROJECT, "SG,Sunrise Greens II,Kolkata,2028-01-31", existing=existing_project()
    )
    assert result.rows[0].change == "changed"
    assert result.rows[0].changed_fields == ["name", "possession_date"]


def test_summary_counts() -> None:
    result = run(
        "projects",
        PROJECT,
        "SG,Sunrise Greens,Kolkata,",
        "NEW,New,Kolkata,",
        existing=existing_project(),
    )
    assert result.summary()["projects"] == {
        "rows": 2,
        "new": 1,
        "changed": 0,
        "unchanged": 1,
        "errors": 0,
        "warnings": 1,
    }
