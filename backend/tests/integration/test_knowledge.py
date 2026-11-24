"""Knowledge store: PDF/DOCX extraction, chunking, embeddings, approved-only retrieval."""

from __future__ import annotations

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.datacenter.knowledge import chunk_text, search_knowledge
from app.db.models import Document, DocumentChunk
from app.providers.embeddings.local import HashingEmbedder
from tests.helpers import (
    ADMIN,
    approve,
    import_and_approve,
    login,
    make_users,
    minimal_docx,
    minimal_pdf,
    sample_csv,
    upload,
    upload_ok,
)

EMBEDDER = HashingEmbedder()


async def test_only_approved_documents_are_searchable(
    app: FastAPI, api: AsyncClient, db: AsyncSession
) -> None:
    app.state.embedder = EMBEDDER
    await make_users(db)
    h = await login(api, ADMIN)
    await import_and_approve(api, h, "projects.csv", sample_csv("projects"))

    pool = minimal_docx(
        "Sunrise Greens has a rooftop swimming pool and a clubhouse.",
        "The clubhouse has a gym and an indoor games room.",
    )
    approved = await upload_ok(
        api,
        h,
        "greens-brochure.docx",
        pool,
        doc_type="brochure",
        project_code="SG",
        title="Greens brochure",
    )
    assert approved["entity_type"] == "document" and approved["status"] == "validated"
    assert approved["summary"]["chunks"] >= 1
    pending = await upload_ok(
        api, h, "faq.pdf", minimal_pdf("Swimming pool timings 6am to 9pm"), doc_type="faq"
    )

    # Nothing approved yet: nothing found.
    db.expire_all()
    assert await search_knowledge(db, EMBEDDER, "swimming pool") == []

    assert (await approve(api, h, approved["id"])).status_code == 200
    db.expire_all()
    hits = await search_knowledge(db, EMBEDDER, "Is there a swimming pool?")
    assert hits and {h.title for h in hits} == {"Greens brochure"}
    assert "swimming pool" in hits[0].content
    assert pending["status"] == "validated"  # still a draft, never returned

    # Archived documents disappear from search.
    r = await api.post(f"/api/v1/datacenter/batches/{approved['id']}/archive", headers=h)
    assert r.status_code == 200
    db.expire_all()
    assert await search_knowledge(db, EMBEDDER, "swimming pool") == []


async def test_results_are_capped_at_three(
    app: FastAPI, api: AsyncClient, db: AsyncSession
) -> None:
    app.state.embedder = EMBEDDER
    await make_users(db)
    h = await login(api, ADMIN)
    paragraphs = [f"Paragraph {i} about parking and security. " * 30 for i in range(8)]
    batch = await upload_ok(api, h, "faq.docx", minimal_docx(*paragraphs), doc_type="faq")
    assert batch["summary"]["chunks"] >= 4
    await approve(api, h, batch["id"])
    db.expire_all()
    assert len(await search_knowledge(db, EMBEDDER, "parking", limit=10)) == 3
    chunks = await db.scalar(select(func.count()).select_from(DocumentChunk))
    assert chunks == batch["summary"]["chunks"]


async def test_pdf_text_is_extracted(app: FastAPI, api: AsyncClient, db: AsyncSession) -> None:
    app.state.embedder = EMBEDDER
    await make_users(db)
    h = await login(api, ADMIN)
    batch = await upload_ok(api, h, "faq.pdf", minimal_pdf("Possession is planned by phase"))
    await approve(api, h, batch["id"])
    db.expire_all()
    [hit] = await search_knowledge(db, EMBEDDER, "possession", limit=1)
    assert "Possession is planned" in hit.content
    doc = await db.scalar(select(Document))
    assert doc is not None and doc.doc_type == "brochure"


async def test_document_upload_errors(app: FastAPI, api: AsyncClient, db: AsyncSession) -> None:
    await make_users(db)
    h = await login(api, ADMIN)
    # The sample config uses Gemini and the test environment has no key.
    r = await upload(api, h, "faq.docx", minimal_docx("hello"))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "embeddings_not_configured"

    app.state.embedder = EMBEDDER
    r = await upload(api, h, "faq.docx", minimal_docx("hello"), doc_type="poster")
    assert r.json()["detail"]["code"] == "invalid_doc_type"
    r = await upload(api, h, "faq.docx", minimal_docx("hello"), project_code="ZZ")
    assert r.json()["detail"]["code"] == "unknown_project"
    r = await upload(api, h, "empty.docx", minimal_docx(""))
    assert r.json()["detail"]["code"] == "no_text"


def test_chunking_keeps_paragraphs_and_overlaps() -> None:
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(10))
    chunks = chunk_text(text, size=500, overlap=50)
    assert len(chunks) > 3
    assert all(len(c) <= 500 + 60 for c in chunks)
    assert chunks[0].startswith("Paragraph 0.")
    long = chunk_text("x" * 2000, size=500, overlap=100)
    assert len(long) >= 4 and all(len(c) <= 500 for c in long)
