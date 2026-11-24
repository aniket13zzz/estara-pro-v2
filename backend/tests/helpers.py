"""Shared test helpers: users, login, uploads, sample inventory, small PDF/DOCX builders."""

from __future__ import annotations

import io
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from httpx import AsyncClient, Response
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CSRF_HEADER
from app.auth.service import create_user
from app.core.enums import Role
from app.datacenter.entities import ENTITIES
from tests.conftest import NOW, TEST_PASSWORD

SAMPLE_DIR = Path(__file__).parent / "fixtures" / "sample_inventory"
ADMIN = "admin@example.com"
STAFF = "staff@example.com"
ORIGIN = {"Origin": "https://dashboard.test"}


async def make_users(db: AsyncSession) -> None:
    await create_user(db, email=ADMIN, name="Admin", password=TEST_PASSWORD, role=Role.CLIENT_ADMIN)
    await create_user(db, email=STAFF, name="Staff", password=TEST_PASSWORD, role=Role.SALES_STAFF)
    await db.commit()


async def login(api: AsyncClient, email: str) -> dict[str, str]:
    """Sign in and return headers for state-changing requests (Origin + CSRF)."""
    r = await api.post(
        "/api/v1/auth/login", json={"email": email, "password": TEST_PASSWORD}, headers=ORIGIN
    )
    assert r.status_code == 200, r.text
    return {**ORIGIN, CSRF_HEADER: r.json()["csrf_token"]}


async def upload(
    api: AsyncClient,
    headers: dict[str, str],
    filename: str,
    content: bytes,
    **form: str,
) -> Response:
    return await api.post(
        "/api/v1/datacenter/uploads",
        files={"file": (filename, content)},
        data=form,
        headers=headers,
    )


async def upload_ok(
    api: AsyncClient, headers: dict[str, str], filename: str, content: bytes, **form: str
) -> dict[str, Any]:
    r = await upload(api, headers, filename, content, **form)
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


async def approve(api: AsyncClient, headers: dict[str, str], batch_id: str) -> Response:
    return await api.post(f"/api/v1/datacenter/batches/{batch_id}/approve", headers=headers)


async def import_and_approve(
    api: AsyncClient, headers: dict[str, str], filename: str, content: bytes
) -> dict[str, Any]:
    batch = await upload_ok(api, headers, filename, content)
    assert batch["status"] == "validated", batch
    r = await approve(api, headers, batch["id"])
    assert r.status_code == 200, r.text
    approved: dict[str, Any] = r.json()
    return approved


def sample_csv(entity: str) -> bytes:
    return (SAMPLE_DIR / f"{entity}.csv").read_bytes()


async def load_sample(api: AsyncClient, headers: dict[str, str]) -> None:
    for entity in ENTITIES:
        await import_and_approve(api, headers, f"{entity.name}.csv", sample_csv(entity.name))


def csv_bytes(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


def sample_workbook(extra_sheets: dict[str, list[list[object]]] | None = None) -> bytes:
    """All sample CSVs as one workbook (plus a README and any extra sheets)."""
    import csv

    wb = Workbook()
    readme = wb.active
    assert readme is not None
    readme.title = "README"
    readme.append(["Sample workbook"])
    for entity in ENTITIES:
        ws = wb.create_sheet(entity.name)
        text = sample_csv(entity.name).decode("utf-8-sig")
        for row in csv.reader(io.StringIO(text)):
            ws.append(row)
    for name, rows in (extra_sheets or {}).items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def later(days: int) -> datetime:
    return NOW + timedelta(days=days)


def minimal_pdf(text: str) -> bytes:
    """A one-page PDF with a single line of text (enough for pypdf to extract)."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


def minimal_docx(*paragraphs: str) -> bytes:
    from docx import Document

    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
