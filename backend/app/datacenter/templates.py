"""Downloadable import templates: one CSV per data type, and one workbook with every sheet plus
a README sheet that explains each column, units (INR, square feet) and the date format."""

from __future__ import annotations

import csv
import io

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from app.datacenter.entities import ENTITIES, ENTITY_BY_NAME, Column

KIND_LABEL = {
    "text": "Text",
    "int": "Whole number",
    "money": "Rupees (INR), whole number",
    "area": "Square feet",
    "date": "Date YYYY-MM-DD",
    "choice": "One of a list",
    "percent": "Percent (0–100)",
    "milestones": "Stage:percent; Stage:percent …",
}

GENERAL_NOTES = (
    "How to fill these templates",
    "• One row per item. Keep the header row exactly as it is; do not rename sheets.",
    "• Amounts are whole rupees (INR) without lakh/crore words: 72,00,000 or 7200000.",
    "• Areas are in square feet. Dates are YYYY-MM-DD, e.g. 2027-12-31.",
    "• Codes (project_code, unit_type_code) link the sheets together and must match exactly.",
    "• Uploading again creates a new version; the previous version is archived, never deleted.",
    "• Nothing is used by the AI agent until a client admin approves the upload.",
    "• Facts go stale after the number of days set in the client config (truth.stale_after_days) "
    "or after valid_to; re-upload and approve to refresh them.",
)


def _describe(column: Column) -> str:
    text = column.help
    if column.choices:
        text = f"{text} Allowed: {', '.join(column.choices)}.".strip()
    return text


def entity_csv(entity_name: str) -> bytes:
    entity = ENTITY_BY_NAME[entity_name]
    buffer = io.StringIO()
    csv.writer(buffer).writerow(entity.column_names)
    return ("﻿" + buffer.getvalue()).encode("utf-8")  # BOM so Excel opens UTF-8 correctly


def inventory_workbook() -> bytes:
    wb = Workbook()
    readme = wb.active
    assert readme is not None
    readme.title = "README"
    bold = Font(bold=True)
    for line in GENERAL_NOTES:
        readme.append([line])
    readme["A1"].font = bold
    readme.append([])
    header_row = readme.max_row + 1
    readme.append(["Sheet", "Column", "Required", "Type", "Description", "Example"])
    for cell in readme[header_row]:
        cell.font = bold
    for entity in ENTITIES:
        for column in entity.columns:
            readme.append(
                [
                    entity.name,
                    column.name,
                    "yes" if column.required else "",
                    KIND_LABEL[column.kind],
                    _describe(column),
                    column.example,
                ]
            )
    for index, width in enumerate((16, 22, 10, 28, 70, 40), start=1):
        readme.column_dimensions[get_column_letter(index)].width = width

    for entity in ENTITIES:
        ws = wb.create_sheet(entity.name)
        ws.append(list(entity.column_names))
        for index, column in enumerate(entity.columns, start=1):
            ws.cell(row=1, column=index).font = bold
            ws.column_dimensions[get_column_letter(index)].width = max(14, len(column.name) + 4)
            if column.kind == "date":
                for row in range(2, 502):
                    ws.cell(row=row, column=index).number_format = "yyyy-mm-dd"
        ws.freeze_panes = "A2"

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
