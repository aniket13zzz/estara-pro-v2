"""Knowledge store: brochures, FAQs and legal documents (PDF/DOCX).

Text is extracted, chunked, embedded through the embeddings adapter and stored in
``document_chunks``. Only approved documents are searchable. This store is for descriptive
questions; it is never used for prices or availability (those come from the Truth Layer).
"""

from __future__ import annotations

import io
import re
import uuid
from dataclasses import dataclass

from docx import Document as DocxDocument
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import RecordStatus
from app.datacenter.scanning import UploadRejected
from app.db.models import Document, DocumentChunk
from app.providers.embeddings import Embedder

MAX_PDF_PAGES = 200
MAX_TEXT_CHARS = 500_000
CHUNK_CHARS = 900
CHUNK_OVERLAP = 120
MAX_RESULTS = 3
DOC_TYPES = ("brochure", "faq", "legal", "other")


def extract_text(kind: str, data: bytes) -> str:
    if kind == "pdf":
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise UploadRejected("encrypted_pdf", "Password-protected PDFs are not accepted.")
            if len(reader.pages) > MAX_PDF_PAGES:
                raise UploadRejected(
                    "too_many_pages", f"PDFs are limited to {MAX_PDF_PAGES} pages."
                )
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except UploadRejected:
            raise
        except (PdfReadError, ValueError, KeyError, TypeError) as exc:
            raise UploadRejected("corrupt_file", "The PDF could not be read.") from exc
    elif kind == "docx":
        try:
            doc = DocxDocument(io.BytesIO(data))
        except Exception as exc:  # python-docx raises several types for damaged files
            raise UploadRejected("corrupt_file", "The Word document could not be read.") from exc
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        text = "\n\n".join(parts)
    else:
        raise UploadRejected("unsupported_type", f"{kind} is not a document type.")
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        raise UploadRejected(
            "no_text", "No text found. Scanned (image-only) documents are not supported."
        )
    return text[:MAX_TEXT_CHARS]


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split on paragraph boundaries into chunks of about ``size`` characters."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        while len(para) > size:  # very long paragraph: hard split with overlap
            if current:
                chunks.append(current)
                current = ""
            chunks.append(para[:size])
            para = para[size - overlap :]
        if current and len(current) + len(para) + 2 > size:
            chunks.append(current)
            tail = current[-overlap:]
            current = f"{tail}\n\n{para}" if overlap else para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


async def ingest_document(
    db: AsyncSession,
    embedder: Embedder,
    *,
    upload_id: uuid.UUID,
    title: str,
    doc_type: str,
    project_id: uuid.UUID | None,
    text: str,
) -> Document:
    """Create a draft document with embedded chunks (not searchable until approved)."""
    chunks = chunk_text(text)
    vectors = await embedder.embed(chunks, "document")
    document = Document(
        upload_id=upload_id,
        project_id=project_id,
        title=title,
        doc_type=doc_type,
        status=RecordStatus.DRAFT,
    )
    db.add(document)
    await db.flush()
    for index, (content, vector) in enumerate(zip(chunks, vectors, strict=True)):
        db.add(
            DocumentChunk(
                document_id=document.id,
                chunk_index=index,
                content=content,
                token_count=len(content.split()),
                embedding=vector,
            )
        )
    await db.flush()
    return document


@dataclass(frozen=True)
class KnowledgeHit:
    document_id: uuid.UUID
    title: str
    doc_type: str
    chunk_index: int
    content: str
    distance: float


async def search_knowledge(
    db: AsyncSession, embedder: Embedder, query: str, *, limit: int = MAX_RESULTS
) -> list[KnowledgeHit]:
    """Up to three chunks from approved documents, closest first (cosine distance)."""
    limit = max(1, min(limit, MAX_RESULTS))
    [vector] = await embedder.embed([query], "query")
    distance = DocumentChunk.embedding.cosine_distance(vector)
    rows = await db.execute(
        select(DocumentChunk, Document, distance.label("distance"))
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(Document.status == RecordStatus.APPROVED, DocumentChunk.embedding.is_not(None))
        .order_by(distance)
        .limit(limit)
    )
    return [
        KnowledgeHit(
            document_id=doc.id,
            title=doc.title,
            doc_type=doc.doc_type,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            distance=float(dist),
        )
        for chunk, doc, dist in rows.tuples()
    ]
