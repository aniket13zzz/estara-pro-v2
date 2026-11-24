"""Row validation for Data Center imports.

Pure functions: they take parsed sheets plus an index of what already exists in the database
and return normalised rows and findings. Findings with severity ``error`` block approval;
``warning`` findings are shown in the preview but do not block.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.enums import RecordStatus, Severity
from app.datacenter.entities import ENTITY_BY_NAME, ENTITY_ORDER, Column, Entity
from app.datacenter.scanning import CellValue, ParsedFile, Sheet

Key = tuple[str, ...]
NormValue = str | int | list[dict[str, str]] | None

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FORMULA_START = ("=", "@")


@dataclass(frozen=True)
class Finding:
    entity: str | None
    row_number: int | None
    column: str | None
    severity: Severity
    code: str
    message: str


@dataclass
class Row:
    entity: str
    row_number: int
    data: dict[str, NormValue]
    key: Key
    change: str = "new"  # new | changed | unchanged
    changed_fields: list[str] = field(default_factory=list)
    has_error: bool = False


@dataclass(frozen=True)
class ExistingRow:
    id: Any
    status: RecordStatus
    version: int
    data: dict[str, NormValue]


# entity -> key -> rows (every status)
ExistingIndex = dict[str, dict[Key, list[ExistingRow]]]


@dataclass
class ValidationResult:
    rows: list[Row]
    findings: list[Finding]

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.WARNING]

    def summary(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for row in self.rows:
            s = out.setdefault(
                row.entity,
                {"rows": 0, "new": 0, "changed": 0, "unchanged": 0, "errors": 0, "warnings": 0},
            )
            s["rows"] += 1
            s[row.change] += 1
        for f in self.findings:
            if f.entity in out:
                out[f.entity]["errors" if f.severity == Severity.ERROR else "warnings"] += 1
        return out


class CellError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------------- value parsing


def _as_text(value: CellValue) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def _as_decimal(value: CellValue, column: Column) -> Decimal:
    if isinstance(value, int | float):
        return Decimal(str(value))
    text = _as_text(value)
    cleaned = re.sub(r"(?i)^(₹|rs\.?|inr)\s*", "", text).replace(",", "").replace(" ", "")
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        hint = (
            " Write the full amount in rupees (e.g. 7200000), not lakh/crore words."
            if (column.kind == "money")
            else ""
        )
        raise CellError("invalid_number", f"'{text}' is not a number.{hint}") from None
    if not number.is_finite():
        raise CellError("invalid_number", f"'{text}' is not a number.")
    return number


def parse_value(column: Column, value: CellValue) -> NormValue:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    kind = column.kind
    if kind == "text":
        text = _as_text(value)
        if text.startswith(FORMULA_START):
            raise CellError("formula", f"'{text[:30]}' looks like a spreadsheet formula.")
        if len(text) > column.max_length:
            raise CellError("too_long", f"Longer than {column.max_length} characters.")
        return text
    if kind == "int":
        number = _as_decimal(value, column)
        if number != number.to_integral_value():
            raise CellError("invalid_number", f"'{_as_text(value)}' must be a whole number.")
        if column.min_value is not None and number < column.min_value:
            raise CellError("invalid_number", f"Must be at least {column.min_value}.")
        return int(number)
    if kind == "money":
        number = _as_decimal(value, column)
        if number != number.to_integral_value():
            raise CellError("invalid_number", "Amounts must be whole rupees.")
        if number <= 0:
            raise CellError("price_not_positive", "Amounts must be greater than 0.")
        if number > 10**13:
            raise CellError("invalid_number", "Amount is implausibly large.")
        return int(number)
    if kind == "area":
        number = _as_decimal(value, column)
        if number <= 0:
            raise CellError("area_not_positive", "Area must be greater than 0.")
        if number >= Decimal("100000000"):
            raise CellError("invalid_number", "Area is implausibly large.")
        return format(number.quantize(Decimal("0.01")).normalize(), "f")
    if kind == "percent":
        number = _as_decimal(value, column)
        if not (0 < number <= 100):
            raise CellError("invalid_percent", "Percent must be more than 0 and at most 100.")
        return format(number.normalize(), "f")
    if kind == "date":
        if isinstance(value, date):
            return value.isoformat()
        text = _as_text(value)
        if not ISO_DATE.match(text):
            raise CellError("invalid_date", f"'{text}' is not a date in YYYY-MM-DD format.")
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            raise CellError("invalid_date", f"'{text}' is not a real date.") from None
    if kind == "choice":
        text = _as_text(value).lower().replace(" ", "_").replace("-", "_")
        if text not in column.choices:
            raise CellError(
                "invalid_choice",
                f"'{_as_text(value)}' must be one of: {', '.join(column.choices)}.",
            )
        return text
    if kind == "milestones":
        return parse_milestones(_as_text(value))
    raise AssertionError(kind)


def parse_milestones(text: str) -> list[dict[str, str]]:
    stages: list[dict[str, str]] = []
    total = Decimal(0)
    for part in (p.strip() for p in text.split(";")):
        if not part:
            continue
        stage, sep, pct = part.rpartition(":")
        stage = stage.strip()
        if not sep or not stage:
            raise CellError("invalid_milestones", f"'{part}' must look like Stage:percent.")
        if stage.startswith(FORMULA_START):
            raise CellError("formula", f"'{stage[:30]}' looks like a spreadsheet formula.")
        try:
            value = Decimal(pct.strip().rstrip("%"))
        except InvalidOperation:
            raise CellError("invalid_milestones", f"'{pct.strip()}' is not a percent.") from None
        if value <= 0:
            raise CellError("invalid_milestones", f"'{stage}' must be more than 0 percent.")
        total += value
        stages.append({"stage": stage, "percent": format(value.normalize(), "f")})
    if not stages:
        raise CellError("invalid_milestones", "No stages found.")
    if total != 100:
        raise CellError(
            "invalid_milestones",
            f"Milestones add up to {format(total.normalize(), 'f')}%, not 100%.",
        )
    return stages


# ---------------------------------------------------------------------------- validation


def entity_key(entity: Entity, data: dict[str, NormValue]) -> Key:
    return tuple(str(data.get(c) or "") for c in entity.key)


def _normalise_sheet(sheet: Sheet, findings: list[Finding]) -> list[Row]:
    entity = ENTITY_BY_NAME[sheet.entity]
    present = set(sheet.header)
    missing = [c for c in entity.required if c not in present]
    for name in missing:
        findings.append(
            Finding(
                entity.name,
                None,
                name,
                Severity.ERROR,
                "missing_column",
                f"Required column '{name}' is missing.",
            )
        )
    for name in sorted(present - set(entity.column_names)):
        findings.append(
            Finding(
                entity.name,
                None,
                name,
                Severity.WARNING,
                "unknown_column",
                f"Column '{name}' is not in the template; ignored.",
            )
        )
    if missing:
        return []
    if not sheet.rows:
        findings.append(
            Finding(
                entity.name, None, None, Severity.ERROR, "empty_file", "There are no data rows."
            )
        )
        return []

    rows: list[Row] = []
    for row_number, cells in sheet.rows:
        data: dict[str, NormValue] = {}
        bad = False
        for column in entity.columns:
            try:
                data[column.name] = parse_value(column, cells.get(column.name))
            except CellError as exc:
                findings.append(
                    Finding(
                        entity.name, row_number, column.name, Severity.ERROR, exc.code, str(exc)
                    )
                )
                data[column.name] = None
                bad = True
                continue
            if column.required and data[column.name] is None:
                findings.append(
                    Finding(
                        entity.name,
                        row_number,
                        column.name,
                        Severity.ERROR,
                        "required",
                        f"'{column.name}' is required.",
                    )
                )
                bad = True
        bad |= _row_rules(entity, row_number, data, findings)
        rows.append(Row(entity.name, row_number, data, entity_key(entity, data), has_error=bad))
    return rows


def _err(entity: str, row: int, column: str | None, code: str, msg: str) -> Finding:
    return Finding(entity, row, column, Severity.ERROR, code, msg)


def _row_rules(entity: Entity, n: int, d: dict[str, NormValue], out: list[Finding]) -> bool:
    before = len(out)
    vf, vt = d.get("valid_from"), d.get("valid_to")
    if isinstance(vf, str) and isinstance(vt, str) and vt < vf:
        out.append(_err(entity.name, n, "valid_to", "date_range", "valid_to is before valid_from."))
    if entity.name == "price_lists":
        if d.get("unit_type_code") and d.get("unit_number"):
            out.append(
                _err(
                    entity.name,
                    n,
                    "unit_number",
                    "conflicting_fields",
                    "Give unit_type_code or unit_number, not both.",
                )
            )
        low, high = d.get("base_price_inr"), d.get("price_max_inr")
        if isinstance(low, int) and isinstance(high, int) and high < low:
            out.append(
                _err(
                    entity.name,
                    n,
                    "price_max_inr",
                    "price_range",
                    "price_max_inr is lower than base_price_inr.",
                )
            )
    if entity.name == "offers" and d.get("discount_inr") and d.get("discount_percent"):
        out.append(
            _err(
                entity.name,
                n,
                "discount_percent",
                "conflicting_fields",
                "Give discount_inr or discount_percent, not both.",
            )
        )
    return len(out) > before


def _same_data(a: dict[str, NormValue], b: dict[str, NormValue]) -> bool:
    return a == b


def _diff(a: dict[str, NormValue], b: dict[str, NormValue]) -> list[str]:
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def validate(parsed: ParsedFile, existing: ExistingIndex) -> ValidationResult:
    findings: list[Finding] = [
        Finding(
            None,
            None,
            None,
            Severity.WARNING,
            "unknown_sheet",
            f"Sheet '{name}' is not a data type and was ignored.",
        )
        for name in parsed.ignored_sheets
    ]
    seen_entities: set[str] = set()
    rows: list[Row] = []
    for sheet in sorted(parsed.sheets, key=lambda s: ENTITY_ORDER[s.entity]):
        if sheet.entity in seen_entities:
            findings.append(
                Finding(
                    sheet.entity,
                    None,
                    None,
                    Severity.ERROR,
                    "duplicate_sheet",
                    f"More than one '{sheet.entity}' sheet.",
                )
            )
            continue
        seen_entities.add(sheet.entity)
        rows.extend(_normalise_sheet(sheet, findings))

    _check_duplicates(rows, findings)
    _check_references(rows, existing, findings)
    _check_cross_entity(rows, existing, findings)
    _compare_with_existing(rows, existing, findings)
    return ValidationResult(rows=rows, findings=findings)


def _check_duplicates(rows: list[Row], findings: list[Finding]) -> None:
    groups: dict[tuple[str, Key], list[Row]] = defaultdict(list)
    for row in rows:
        if all(row.key):
            groups[(row.entity, row.key)].append(row)
    for (entity, key), group in groups.items():
        first = group[0]
        for other in group[1:]:
            other.has_error = True
            label = " / ".join(k for k in key if k)
            if _same_data(first.data, other.data):
                findings.append(
                    _err(
                        entity,
                        other.row_number,
                        None,
                        "duplicate_in_file",
                        f"{label} repeats row {first.row_number}.",
                    )
                )
            else:
                fields = ", ".join(
                    f"{f} {first.data.get(f)!s} vs {other.data.get(f)!s}"
                    for f in _diff(first.data, other.data)
                )
                findings.append(
                    _err(
                        entity,
                        other.row_number,
                        None,
                        "contradiction",
                        f"{label} also appears in row {first.row_number} with "
                        f"different values: {fields}.",
                    )
                )


def _live(rows: list[ExistingRow]) -> list[ExistingRow]:
    return [r for r in rows if r.status != RecordStatus.ARCHIVED]


def _check_references(rows: list[Row], existing: ExistingIndex, findings: list[Finding]) -> None:
    in_batch: dict[str, set[Key]] = defaultdict(set)
    for row in rows:
        in_batch[row.entity].add(row.key)
    for row in rows:
        entity = ENTITY_BY_NAME[row.entity]
        for ref in entity.references:
            values = tuple(str(row.data.get(c) or "") for c in ref.columns)
            if not all(values):
                continue
            target = ENTITY_BY_NAME[ref.entity]
            # Target keys may be longer (price_lists); references only use full keys here.
            if values in in_batch[ref.entity]:
                continue
            live = _live(existing.get(ref.entity, {}).get(values, []))
            label = " / ".join(values)
            if not live:
                row.has_error = True
                findings.append(
                    _err(
                        row.entity,
                        row.row_number,
                        ref.columns[-1],
                        ref.error_code,
                        f"{target.label[:-1]} {label} does not exist. Add it in "
                        f"the {target.name} sheet or upload it first.",
                    )
                )
            elif all(r.status == RecordStatus.DRAFT for r in live):
                findings.append(
                    Finding(
                        row.entity,
                        row.row_number,
                        ref.columns[-1],
                        Severity.WARNING,
                        "references_unapproved",
                        f"{target.label[:-1]} {label} awaits approval; its "
                        "facts stay unavailable until approved.",
                    )
                )


def _current_data(existing: ExistingIndex, entity: str, key: Key) -> dict[str, NormValue] | None:
    """Data for a key from this batch's view of the world: approved row, else latest draft."""
    rows = _live(existing.get(entity, {}).get(key, []))
    approved = [r for r in rows if r.status == RecordStatus.APPROVED]
    pick = approved or sorted(rows, key=lambda r: r.version)
    return pick[-1].data if pick else None


def _check_cross_entity(rows: list[Row], existing: ExistingIndex, findings: list[Finding]) -> None:
    batch: dict[str, dict[Key, Row]] = defaultdict(dict)
    for row in rows:
        batch[row.entity].setdefault(row.key, row)

    def lookup(entity: str, key: Key) -> dict[str, NormValue] | None:
        if key in batch[entity]:
            return batch[entity][key].data
        return _current_data(existing, entity, key)

    for row in rows:
        d = row.data
        if row.entity == "units" and d.get("unit_type_code"):
            ut = lookup("unit_types", (str(d["project_code"]), str(d["unit_type_code"])))
            if ut is not None and ut.get("bhk") is not None and ut.get("bhk") != d.get("bhk"):
                row.has_error = True
                findings.append(
                    _err(
                        "units",
                        row.row_number,
                        "bhk",
                        "contradiction",
                        f"Unit {d['unit_number']} is {d['bhk']} BHK but unit type "
                        f"{d['unit_type_code']} is {ut['bhk']} BHK.",
                    )
                )

    # Same unit, different price in the same batch: units.price_inr vs a unit price list.
    for row in rows:
        d = row.data
        if row.entity != "price_lists" or not d.get("unit_number"):
            continue
        unit_row = batch["units"].get((str(d["project_code"]), str(d["unit_number"])))
        if unit_row is None or unit_row.data.get("price_inr") is None:
            continue
        unit_price = unit_row.data["price_inr"]
        low, high = d.get("base_price_inr"), d.get("price_max_inr") or d.get("base_price_inr")
        if not (low == unit_price == high):
            row.has_error = unit_row.has_error = True
            msg = (
                f"Unit {d['unit_number']} is priced {unit_price} in units (row "
                f"{unit_row.row_number}) but {low}"
                + (f"–{high}" if high != low else "")
                + f" in price_lists (row {row.row_number})."
            )
            findings.append(
                _err("price_lists", row.row_number, "base_price_inr", "contradiction", msg)
            )
            findings.append(_err("units", unit_row.row_number, "price_inr", "contradiction", msg))


def _compare_with_existing(
    rows: list[Row], existing: ExistingIndex, findings: list[Finding]
) -> None:
    for row in rows:
        current = existing.get(row.entity, {}).get(row.key, [])
        approved = [r for r in current if r.status == RecordStatus.APPROVED]
        if not approved:
            row.change = "new"
            continue
        latest = max(approved, key=lambda r: r.version)
        if _same_data(latest.data, row.data):
            row.change = "unchanged"
            findings.append(
                Finding(
                    row.entity,
                    row.row_number,
                    None,
                    Severity.WARNING,
                    "unchanged",
                    f"Same as approved version {latest.version}. Approving "
                    "re-confirms it and resets its freshness.",
                )
            )
        else:
            row.change = "changed"
            row.changed_fields = _diff(latest.data, row.data)
