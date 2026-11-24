"""Upload scanning and parsing.

Scanning checks the real file type from its bytes (never trusting the name alone), size limits,
archive safety for XLSX/DOCX (zip-bomb ratios, entry counts, macros), and that CSV is UTF-8 text.
Parsing turns CSV/XLSX into plain rows of strings, numbers and dates; nothing is evaluated.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from openpyxl import load_workbook

from app.datacenter.entities import ENTITY_BY_NAME

FileKind = Literal["csv", "xlsx", "pdf", "docx"]
DATA_KINDS: frozenset[str] = frozenset({"csv", "xlsx"})
DOCUMENT_KINDS: frozenset[str] = frozenset({"pdf", "docx"})

MAX_ZIP_ENTRIES = 2000
MAX_ZIP_UNCOMPRESSED = 100 * 1024 * 1024
MAX_ZIP_RATIO = 200
MAX_ROWS_PER_SHEET = 5000
MACRO_MARKERS = ("vbaproject.bin", "activex", "macrosheets")

CellValue = str | int | float | date | None


class UploadRejected(Exception):
    """The file was refused before parsing. ``code`` is machine-readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Sheet:
    entity: str
    header: list[str]
    # (row_number in the file, cells by header name)
    rows: list[tuple[int, dict[str, CellValue]]] = field(default_factory=list)


@dataclass
class ParsedFile:
    sheets: list[Sheet]
    ignored_sheets: list[str] = field(default_factory=list)


def _extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _check_zip(data: bytes) -> set[str]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UploadRejected("corrupt_file", "The file is not a valid Office document.") from exc
    infos = archive.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise UploadRejected("unsafe_file", "The file contains too many internal parts.")
    total = 0
    for info in infos:
        total += info.file_size
        if info.compress_size and info.file_size / info.compress_size > MAX_ZIP_RATIO:
            raise UploadRejected("unsafe_file", "The file is compressed suspiciously; refused.")
        lowered = info.filename.lower()
        if any(marker in lowered for marker in MACRO_MARKERS):
            raise UploadRejected(
                "macros_not_allowed",
                "Files with macros or ActiveX are not accepted. Save as a plain .xlsx/.docx.",
            )
    if total > MAX_ZIP_UNCOMPRESSED:
        raise UploadRejected("unsafe_file", "The file expands to more than 100 MB; refused.")
    return {i.filename for i in infos}


def detect_kind(filename: str, data: bytes) -> FileKind:
    """Identify the file from its content and check the extension agrees."""
    if not data:
        raise UploadRejected("empty_file", "The file is empty.")
    ext = _extension(filename)
    kind: FileKind
    if data.startswith(b"%PDF-"):
        kind = "pdf"
    elif data.startswith(b"PK\x03\x04"):
        names = _check_zip(data)
        if "xl/workbook.xml" in names:
            kind = "xlsx"
        elif "word/document.xml" in names:
            kind = "docx"
        else:
            raise UploadRejected("unsupported_type", "Only .xlsx and .docx Office files work.")
    else:
        if b"\x00" in data[:8192]:
            raise UploadRejected("unsupported_type", "Binary files are not accepted.")
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise UploadRejected(
                "bad_encoding", "CSV files must be UTF-8. In Excel use 'CSV UTF-8' when saving."
            ) from exc
        kind = "csv"
    if ext != kind:
        raise UploadRejected(
            "extension_mismatch",
            f"The file content is {kind.upper()} but the name ends in .{ext or '(none)'}.",
        )
    return kind


def normalise_header(name: object) -> str:
    text = str(name or "").strip().lower()
    return re.sub(r"[\s\-]+", "_", text)


def _cell(value: object) -> CellValue:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int | float):
        return value
    text = str(value).strip()
    return text or None


def _rows_from_matrix(entity: str, matrix: list[list[object]], first_row: int = 1) -> Sheet:
    # header = first non-empty row
    header_index = next(
        (i for i, row in enumerate(matrix) if any(_cell(v) is not None for v in row)), None
    )
    if header_index is None:
        return Sheet(entity=entity, header=[])
    header = [normalise_header(h) for h in matrix[header_index]]
    while header and not header[-1]:
        header.pop()
    sheet = Sheet(entity=entity, header=header)
    for offset, raw in enumerate(matrix[header_index + 1 :], start=header_index + 1):
        cells = {h: _cell(raw[i]) if i < len(raw) else None for i, h in enumerate(header) if h}
        if all(v is None for v in cells.values()):
            continue
        if len(sheet.rows) >= MAX_ROWS_PER_SHEET:
            raise UploadRejected(
                "too_many_rows", f"{entity}: more than {MAX_ROWS_PER_SHEET} rows; split the file."
            )
        sheet.rows.append((offset + first_row, cells))
    return sheet


def detect_csv_entity(filename: str, header: list[str], requested: str | None) -> str:
    if requested:
        if requested not in ENTITY_BY_NAME:
            raise UploadRejected("unknown_entity", f"Unknown data type {requested!r}.")
        return requested
    stem = filename.rsplit(".", 1)[0].lower()
    by_name = [n for n in ENTITY_BY_NAME if stem == n or stem.endswith("_" + n)]
    if len(by_name) == 1:
        return by_name[0]
    present = set(header)
    candidates = [e for e in ENTITY_BY_NAME.values() if set(e.required) <= present]
    if candidates:
        # Prefer the most specific template (payment_plans' required columns include amenities').
        best = max(len(e.required) for e in candidates)
        top = [e.name for e in candidates if len(e.required) == best]
        if len(top) == 1:
            return top[0]
    raise UploadRejected(
        "unknown_entity",
        "Could not tell which data this CSV holds. Name it after the template "
        "(e.g. units.csv) or choose the data type when uploading.",
    )


def parse_csv(filename: str, data: bytes, entity: str | None) -> ParsedFile:
    text = data.decode("utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    matrix: list[list[object]] = [list(r) for r in csv.reader(io.StringIO(text), dialect)]
    probe = _rows_from_matrix("?", matrix[:2])
    name = detect_csv_entity(filename, probe.header, entity)
    return ParsedFile(sheets=[_rows_from_matrix(name, matrix)])


def parse_xlsx(data: bytes) -> ParsedFile:
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises many types for damaged files
        raise UploadRejected("corrupt_file", "The workbook could not be read.") from exc
    parsed = ParsedFile(sheets=[])
    try:
        for ws in workbook.worksheets:
            entity = normalise_header(ws.title)
            if entity not in ENTITY_BY_NAME:
                if entity not in {"readme", "read_me", "instructions"}:
                    parsed.ignored_sheets.append(ws.title)
                continue
            matrix: list[list[object]] = [list(r) for r in ws.iter_rows(values_only=True)]
            parsed.sheets.append(_rows_from_matrix(entity, matrix))
    finally:
        workbook.close()
    if not parsed.sheets:
        raise UploadRejected(
            "no_data_sheets",
            "No sheet is named after a data type (projects, unit_types, units, price_lists, "
            "offers, payment_plans, amenities). Use the template workbook.",
        )
    return parsed


def parse_data_file(kind: FileKind, filename: str, data: bytes, entity: str | None) -> ParsedFile:
    if kind == "csv":
        return parse_csv(filename, data, entity)
    if kind == "xlsx":
        return parse_xlsx(data)
    raise UploadRejected("unsupported_type", f"{kind} is not a data file.")
