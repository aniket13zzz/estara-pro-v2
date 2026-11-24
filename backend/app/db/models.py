"""All ESTARA Pro tables (single tenant: no tenant_id, no RLS).

Schema overview: docs/architecture/database-design.md and docs/roadmap/phase-plan.md.
Money is whole INR (BIGINT). Areas are square feet (NUMERIC). Times are timezone-aware.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import (
    EMBEDDING_DIMENSIONS,
    Channel,
    FollowUpStatus,
    HandoffStatus,
    IdentityType,
    ImportBatchStatus,
    LeadBand,
    LeadState,
    MessageDirection,
    MessageSender,
    RecordStatus,
    Role,
    Severity,
    UsageKind,
    VisitState,
    WebhookProvider,
)
from app.db.base import (
    INR,
    LANG,
    SHORT,
    Base,
    CommercialMixin,
    TimestampMixin,
    created_at_col,
    str_enum,
    uuid_pk,
)

JSON = JSONB
EMPTY_JSON = text("'{}'::jsonb")


def fk(target: str, *, nullable: bool = True, ondelete: str = "SET NULL") -> Mapped[Any]:
    return mapped_column(Uuid, ForeignKey(target, ondelete=ondelete), nullable=nullable)


# =============================================================================================
# Auth


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (Index("uq_users_email_lower", func.lower(text("email")), unique=True),)

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(str_enum(Role, "role"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(Base):
    """Server-side dashboard session. Only SHA-256 hashes of the tokens are stored."""

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = fk("users.id", nullable=False, ondelete="CASCADE")
    created_at: Mapped[datetime] = created_at_col()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_entity", "entity_type", "entity_id"),
        Index("ix_audit_log_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_user_id: Mapped[uuid.UUID | None] = fk("users.id")
    actor_type: Mapped[str] = mapped_column(SHORT, nullable=False, server_default="user")
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(SHORT)
    entity_id: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, server_default=EMPTY_JSON)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_col()


# =============================================================================================
# Data Center


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[uuid.UUID] = uuid_pk()
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    kind: Mapped[str] = mapped_column(SHORT, nullable=False)  # data | document
    uploaded_by: Mapped[uuid.UUID | None] = fk("users.id")
    created_at: Mapped[datetime] = created_at_col()


class ImportBatch(TimestampMixin, Base):
    __tablename__ = "import_batches"
    __table_args__ = (Index("ix_import_batches_status", "status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    upload_id: Mapped[uuid.UUID | None] = fk("uploads.id")
    entity_type: Mapped[str] = mapped_column(SHORT, nullable=False)
    status: Mapped[ImportBatchStatus] = mapped_column(
        str_enum(ImportBatchStatus, "status"), nullable=False, default=ImportBatchStatus.UPLOADED
    )
    row_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_by: Mapped[uuid.UUID | None] = fk("users.id")
    approved_by: Mapped[uuid.UUID | None] = fk("users.id")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filename: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, server_default=EMPTY_JSON)


class ImportRow(Base):
    __tablename__ = "import_rows"
    __table_args__ = (UniqueConstraint("batch_id", "row_number"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    batch_id: Mapped[uuid.UUID] = fk("import_batches.id", nullable=False, ondelete="CASCADE")
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ImportValidationError(Base):
    __tablename__ = "validation_errors"
    __table_args__ = (Index("ix_validation_errors_batch_id", "batch_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    batch_id: Mapped[uuid.UUID] = fk("import_batches.id", nullable=False, ondelete="CASCADE")
    row_number: Mapped[int | None] = mapped_column(Integer)
    column_name: Mapped[str | None] = mapped_column(String(100))
    entity: Mapped[str | None] = mapped_column(SHORT)
    severity: Mapped[Severity] = mapped_column(
        str_enum(Severity, "severity"),
        nullable=False,
        default=Severity.ERROR,
        server_default=Severity.ERROR.value,
    )
    code: Mapped[str] = mapped_column(SHORT, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)


# =============================================================================================
# Inventory (Truth Layer). Each table: status, approved_by/at, valid_from/to, version, batch.


class Project(CommercialMixin, TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("code", "version"),
        Index(
            "uq_projects_code_approved",
            "code",
            unique=True,
            postgresql_where=text("status = 'approved'"),
        ),
        *CommercialMixin.commercial_constraints(),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(SHORT, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    locality: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    rera_number: Mapped[str | None] = mapped_column(String(100))
    possession_date: Mapped[date | None] = mapped_column(Date)


class Tower(CommercialMixin, TimestampMixin, Base):
    __tablename__ = "towers"
    __table_args__ = (
        UniqueConstraint("project_id", "code", "version"),
        *CommercialMixin.commercial_constraints(),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = fk("projects.id", nullable=False, ondelete="CASCADE")
    code: Mapped[str] = mapped_column(SHORT, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    floors: Mapped[int | None] = mapped_column(Integer)
    possession_date: Mapped[date | None] = mapped_column(Date)


class UnitType(CommercialMixin, TimestampMixin, Base):
    __tablename__ = "unit_types"
    __table_args__ = (
        UniqueConstraint("project_id", "code", "version"),
        CheckConstraint("bhk >= 0", name="bhk_non_negative"),
        CheckConstraint("carpet_area_sqft IS NULL OR carpet_area_sqft > 0", name="area_positive"),
        *CommercialMixin.commercial_constraints(),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = fk("projects.id", nullable=False, ondelete="CASCADE")
    code: Mapped[str] = mapped_column(SHORT, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    property_type: Mapped[str] = mapped_column(SHORT, nullable=False)  # apartment, villa, plot...
    bhk: Mapped[int] = mapped_column(Integer, nullable=False)
    carpet_area_sqft: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    super_area_sqft: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))


class Unit(CommercialMixin, TimestampMixin, Base):
    __tablename__ = "units"
    __table_args__ = (
        UniqueConstraint("project_id", "unit_number", "version"),
        Index("ix_units_search", "project_id", "status", "bhk", "price_inr"),
        CheckConstraint("price_inr IS NULL OR price_inr > 0", name="price_positive"),
        CheckConstraint("carpet_area_sqft IS NULL OR carpet_area_sqft > 0", name="area_positive"),
        CheckConstraint(
            "availability IN ('available', 'booked', 'blocked', 'sold')", name="availability"
        ),
        *CommercialMixin.commercial_constraints(),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = fk("projects.id", nullable=False, ondelete="CASCADE")
    tower_id: Mapped[uuid.UUID | None] = fk("towers.id")
    unit_type_id: Mapped[uuid.UUID | None] = fk("unit_types.id")
    unit_number: Mapped[str] = mapped_column(SHORT, nullable=False)
    floor: Mapped[int | None] = mapped_column(Integer)
    bhk: Mapped[int] = mapped_column(Integer, nullable=False)
    property_type: Mapped[str] = mapped_column(SHORT, nullable=False)
    carpet_area_sqft: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    price_inr: Mapped[int | None] = mapped_column(INR)
    availability: Mapped[str] = mapped_column(
        String(16), nullable=False, default="available", server_default="available"
    )
    facing: Mapped[str | None] = mapped_column(SHORT)


class PriceList(CommercialMixin, TimestampMixin, Base):
    __tablename__ = "price_lists"
    __table_args__ = (
        Index("ix_price_lists_project_status", "project_id", "status"),
        CheckConstraint("base_price_inr > 0", name="price_positive"),
        CheckConstraint(
            "price_max_inr IS NULL OR price_max_inr >= base_price_inr", name="price_range"
        ),
        *CommercialMixin.commercial_constraints(),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = fk("projects.id", nullable=False, ondelete="CASCADE")
    unit_type_id: Mapped[uuid.UUID | None] = fk("unit_types.id")
    unit_id: Mapped[uuid.UUID | None] = fk("units.id")
    base_price_inr: Mapped[int] = mapped_column(INR, nullable=False)
    price_max_inr: Mapped[int | None] = mapped_column(INR)
    price_per_sqft_inr: Mapped[int | None] = mapped_column(INR)
    notes: Mapped[str | None] = mapped_column(Text)


class Offer(CommercialMixin, TimestampMixin, Base):
    __tablename__ = "offers"
    __table_args__ = (
        Index("ix_offers_project_status", "project_id", "status"),
        CheckConstraint(
            "discount_percent IS NULL OR (discount_percent > 0 AND discount_percent <= 100)",
            name="discount_percent",
        ),
        CheckConstraint("discount_inr IS NULL OR discount_inr > 0", name="discount_inr"),
        *CommercialMixin.commercial_constraints(),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = fk("projects.id", nullable=False, ondelete="CASCADE")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    discount_inr: Mapped[int | None] = mapped_column(INR)
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))


class PaymentPlan(CommercialMixin, TimestampMixin, Base):
    __tablename__ = "payment_plans"
    __table_args__ = (
        Index("ix_payment_plans_project_status", "project_id", "status"),
        *CommercialMixin.commercial_constraints(),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = fk("projects.id", nullable=False, ondelete="CASCADE")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    milestones: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, server_default=text("'[]'::jsonb")
    )


class Amenity(CommercialMixin, TimestampMixin, Base):
    __tablename__ = "amenities"
    __table_args__ = (
        Index("ix_amenities_project_status", "project_id", "status"),
        *CommercialMixin.commercial_constraints(),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = fk("projects.id", nullable=False, ondelete="CASCADE")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(SHORT)


# =============================================================================================
# Knowledge (pgvector is ONLY for documents, never prices or availability)


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_status", "status"),
        CheckConstraint(
            "status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="approved_has_approver",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    upload_id: Mapped[uuid.UUID | None] = fk("uploads.id")
    project_id: Mapped[uuid.UUID | None] = fk("projects.id")
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    doc_type: Mapped[str] = mapped_column(SHORT, nullable=False)  # brochure | faq | legal | other
    language: Mapped[str | None] = mapped_column(LANG)
    status: Mapped[RecordStatus] = mapped_column(
        str_enum(RecordStatus, "status"), nullable=False, default=RecordStatus.DRAFT
    )
    approved_by: Mapped[uuid.UUID | None] = fk("users.id")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index"),
        Index(
            "ix_document_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = fk("documents.id", nullable=False, ondelete="CASCADE")
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    created_at: Mapped[datetime] = created_at_col()


# =============================================================================================
# Customers and conversations


class Customer(TimestampMixin, Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str | None] = mapped_column(String(200))
    preferred_language: Mapped[str | None] = mapped_column(LANG)
    possible_duplicate_of: Mapped[uuid.UUID | None] = fk("customers.id")
    duplicate_flagged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    whatsapp_opted_out: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    whatsapp_opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the customer agreed to WhatsApp messages: they messaged us there, or said yes on a
    # call when asked. Business-initiated messages need this (and no opt-out).
    whatsapp_opt_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    memory: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, server_default=EMPTY_JSON)
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CustomerIdentity(Base):
    """Phone, WhatsApp, email or web-session identifier.

    Values are normalised (E.164 phones, lowercase emails).
    """

    __tablename__ = "customer_identities"
    __table_args__ = (
        # A verified identifier belongs to exactly one customer. Unverified claims (for example a
        # phone number typed into web chat) may appear on several customers and are only used
        # to flag possible duplicates, never to merge (ADR 0010).
        Index(
            "uq_customer_identities_verified",
            "identity_type",
            "value",
            unique=True,
            postgresql_where=text("verified"),
        ),
        Index("ix_customer_identities_value", "value"),
        Index("ix_customer_identities_customer_id", "customer_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    customer_id: Mapped[uuid.UUID] = fk("customers.id", nullable=False, ondelete="CASCADE")
    identity_type: Mapped[IdentityType] = mapped_column(
        str_enum(IdentityType, "identity_type"), nullable=False
    )
    value: Mapped[str] = mapped_column(String(320), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_customer_id", "customer_id"),
        Index("ix_conversations_last_message_at", "last_message_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    customer_id: Mapped[uuid.UUID] = fk("customers.id", nullable=False, ondelete="CASCADE")
    channel: Mapped[Channel] = mapped_column(str_enum(Channel, "channel"), nullable=False)
    language: Mapped[str] = mapped_column(LANG, nullable=False)
    started_at: Mapped[datetime] = created_at_col()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_en: Mapped[str | None] = mapped_column(Text)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        Index(
            "uq_messages_provider_message_id",
            "channel",
            "provider_message_id",
            unique=True,
            postgresql_where=text("provider_message_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    conversation_id: Mapped[uuid.UUID] = fk("conversations.id", nullable=False, ondelete="CASCADE")
    channel: Mapped[Channel] = mapped_column(str_enum(Channel, "channel"), nullable=False)
    direction: Mapped[MessageDirection] = mapped_column(
        str_enum(MessageDirection, "direction"), nullable=False
    )
    sender: Mapped[MessageSender] = mapped_column(str_enum(MessageSender, "sender"), nullable=False)
    language: Mapped[str] = mapped_column(LANG, nullable=False)
    language_switched: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    # Brain metadata: guard interventions, detected objections, escalation, LLM attempts.
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, server_default=EMPTY_JSON)
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    delivery_status: Mapped[str | None] = mapped_column(SHORT)
    created_at: Mapped[datetime] = created_at_col()


class Call(TimestampMixin, Base):
    __tablename__ = "calls"
    __table_args__ = (
        Index("ix_calls_started_at", "started_at"),
        Index("ix_calls_customer_id", "customer_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    vapi_call_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = fk("conversations.id")
    customer_id: Mapped[uuid.UUID | None] = fk("customers.id")
    caller_phone: Mapped[str | None] = mapped_column(String(32))
    language: Mapped[str | None] = mapped_column(LANG)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    recording_url: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    summary_en: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(SHORT)
    ended_reason: Mapped[str | None] = mapped_column(String(100))
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    raw_report: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Native mode: numbers the agent said that no tool result or caller turn contained (ADR 0014).
    unverified_numbers: Mapped[list[str] | None] = mapped_column(JSON)


class VapiToolCall(TimestampMixin, Base):
    """One tool call from Vapi's model (native mode): answered once, the same answer on repeat."""

    __tablename__ = "vapi_tool_calls"
    __table_args__ = (Index("ix_vapi_tool_calls_vapi_call_id", "vapi_call_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    tool_call_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    vapi_call_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    arguments: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


# =============================================================================================
# Leads


class Lead(TimestampMixin, Base):
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_state_score", "state", "score"),
        Index("ix_leads_customer_id", "customer_id"),
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 100)", name="score_range"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    customer_id: Mapped[uuid.UUID] = fk("customers.id", nullable=False, ondelete="CASCADE")
    state: Mapped[LeadState] = mapped_column(
        str_enum(LeadState, "state"), nullable=False, default=LeadState.NEW_ENQUIRY
    )
    score: Mapped[int | None] = mapped_column(Integer)
    band: Mapped[LeadBand | None] = mapped_column(str_enum(LeadBand, "band"))
    score_reason: Mapped[str | None] = mapped_column(Text)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_channel: Mapped[Channel] = mapped_column(
        str_enum(Channel, "source_channel"), nullable=False
    )
    assigned_user_id: Mapped[uuid.UUID | None] = fk("users.id")


class LeadRequirement(TimestampMixin, Base):
    """Latest extracted requirements per lead. Changes are also logged in lead_events."""

    __tablename__ = "lead_requirements"
    __table_args__ = (
        CheckConstraint(
            "budget_min_inr IS NULL OR budget_max_inr IS NULL OR budget_max_inr >= budget_min_inr",
            name="budget_range",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("leads.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    budget_min_inr: Mapped[int | None] = mapped_column(INR)
    budget_max_inr: Mapped[int | None] = mapped_column(INR)
    locations: Mapped[list[str] | None] = mapped_column(ARRAY(String(200)))
    property_type: Mapped[str | None] = mapped_column(SHORT)
    bhk: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    timeline_months: Mapped[int | None] = mapped_column(Integer)
    loan_needed: Mapped[bool | None] = mapped_column(Boolean)
    purpose: Mapped[str | None] = mapped_column(String(16))  # self_use | investment
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, server_default=EMPTY_JSON)


class LeadEvent(Base):
    __tablename__ = "lead_events"
    __table_args__ = (Index("ix_lead_events_lead_created", "lead_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    lead_id: Mapped[uuid.UUID] = fk("leads.id", nullable=False, ondelete="CASCADE")
    event_type: Mapped[str] = mapped_column(SHORT, nullable=False)
    from_state: Mapped[LeadState | None] = mapped_column(str_enum(LeadState, "from_state"))
    to_state: Mapped[LeadState | None] = mapped_column(str_enum(LeadState, "to_state"))
    actor_type: Mapped[str] = mapped_column(SHORT, nullable=False)  # system | agent | user
    actor_user_id: Mapped[uuid.UUID | None] = fk("users.id")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, server_default=EMPTY_JSON)
    created_at: Mapped[datetime] = created_at_col()


class LeadMatch(Base):
    __tablename__ = "lead_matches"
    __table_args__ = (Index("ix_lead_matches_lead_id", "lead_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    lead_id: Mapped[uuid.UUID] = fk("leads.id", nullable=False, ondelete="CASCADE")
    project_id: Mapped[uuid.UUID] = fk("projects.id", nullable=False, ondelete="CASCADE")
    unit_id: Mapped[uuid.UUID | None] = fk("units.id")
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    match_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    reasons: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)  # {"en": ..., "hi": ...}
    created_at: Mapped[datetime] = created_at_col()


class Objection(Base):
    __tablename__ = "objections"
    __table_args__ = (Index("ix_objections_lead_id", "lead_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    lead_id: Mapped[uuid.UUID | None] = fk("leads.id", ondelete="CASCADE")
    conversation_id: Mapped[uuid.UUID | None] = fk("conversations.id")
    objection_type: Mapped[str] = mapped_column(SHORT, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(LANG, nullable=False)
    handled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = created_at_col()


# =============================================================================================
# Site visits


class SiteVisit(TimestampMixin, Base):
    __tablename__ = "site_visits"
    __table_args__ = (
        Index("ix_site_visits_date", "visit_date"),
        Index("ix_site_visits_slot", "project_id", "visit_date", "slot"),
        CheckConstraint(
            "state NOT IN ('confirmed', 'completed', 'no_show') OR confirmed_by IS NOT NULL",
            name="confirmed_by_user",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    lead_id: Mapped[uuid.UUID] = fk("leads.id", nullable=False, ondelete="CASCADE")
    customer_id: Mapped[uuid.UUID] = fk("customers.id", nullable=False, ondelete="CASCADE")
    project_id: Mapped[uuid.UUID] = fk("projects.id", nullable=False, ondelete="CASCADE")
    visit_date: Mapped[date] = mapped_column(Date, nullable=False)
    slot: Mapped[time] = mapped_column(Time, nullable=False)
    state: Mapped[VisitState] = mapped_column(
        str_enum(VisitState, "state"), nullable=False, default=VisitState.REQUESTED
    )
    requested_via: Mapped[Channel] = mapped_column(
        str_enum(Channel, "requested_via"), nullable=False
    )
    visitor_name: Mapped[str | None] = mapped_column(String(200))
    confirmed_by: Mapped[uuid.UUID | None] = fk("users.id")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # WhatsApp notices, each sent at most once (the worker fills these in).
    requested_notice_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_notice_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class SiteVisitEvent(Base):
    __tablename__ = "site_visit_events"
    __table_args__ = (Index("ix_site_visit_events_visit_id", "visit_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    visit_id: Mapped[uuid.UUID] = fk("site_visits.id", nullable=False, ondelete="CASCADE")
    from_state: Mapped[VisitState | None] = mapped_column(str_enum(VisitState, "from_state"))
    to_state: Mapped[VisitState] = mapped_column(str_enum(VisitState, "to_state"), nullable=False)
    actor_type: Mapped[str] = mapped_column(SHORT, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = fk("users.id")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, server_default=EMPTY_JSON)
    created_at: Mapped[datetime] = created_at_col()


# =============================================================================================
# Handoff and follow-ups


class Handoff(TimestampMixin, Base):
    __tablename__ = "handoffs"
    __table_args__ = (
        Index("ix_handoffs_status", "status"),
        Index("ix_handoffs_lead_id", "lead_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    lead_id: Mapped[uuid.UUID] = fk("leads.id", nullable=False, ondelete="CASCADE")
    assigned_email: Mapped[str | None] = mapped_column(String(320))  # sales_team entry in config
    assigned_user_id: Mapped[uuid.UUID | None] = fk("users.id")
    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[HandoffStatus] = mapped_column(
        str_enum(HandoffStatus, "status"), nullable=False, default=HandoffStatus.OPEN
    )
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, server_default=EMPTY_JSON)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FollowUp(TimestampMixin, Base):
    __tablename__ = "follow_ups"
    __table_args__ = (Index("ix_follow_ups_due", "status", "scheduled_for"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    lead_id: Mapped[uuid.UUID] = fk("leads.id", nullable=False, ondelete="CASCADE")
    customer_id: Mapped[uuid.UUID] = fk("customers.id", nullable=False, ondelete="CASCADE")
    channel: Mapped[Channel] = mapped_column(str_enum(Channel, "channel"), nullable=False)
    language: Mapped[str] = mapped_column(LANG, nullable=False)
    template_name: Mapped[str | None] = mapped_column(String(100))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[FollowUpStatus] = mapped_column(
        str_enum(FollowUpStatus, "status"), nullable=False, default=FollowUpStatus.SCHEDULED
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(Text)


# =============================================================================================
# Webhooks, usage, agent state


class WebhookEvent(Base):
    """Provider event IDs for idempotency and replay protection."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_id"),
        Index("ix_webhook_events_received_at", "received_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    provider: Mapped[WebhookProvider] = mapped_column(
        str_enum(WebhookProvider, "provider"), nullable=False
    )
    event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str | None] = mapped_column(String(100))
    payload_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(SHORT, nullable=False, server_default="received")
    received_at: Mapped[datetime] = created_at_col()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class Alert(TimestampMixin, Base):
    """Dashboard alert (e.g. stale data). At most one open alert per kind."""

    __tablename__ = "alerts"
    __table_args__ = (
        Index(
            "uq_alerts_open_kind",
            "kind",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(SHORT, nullable=False)
    severity: Mapped[Severity] = mapped_column(str_enum(Severity, "severity"), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, server_default=EMPTY_JSON)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (Index("ix_usage_records_kind_time", "kind", "occurred_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[UsageKind] = mapped_column(str_enum(UsageKind, "kind"), nullable=False)
    provider: Mapped[str] = mapped_column(SHORT, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    call_id: Mapped[uuid.UUID | None] = fk("calls.id")
    conversation_id: Mapped[uuid.UUID | None] = fk("conversations.id")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, server_default=EMPTY_JSON)
    occurred_at: Mapped[datetime] = created_at_col()


class AgentState(Base):
    """Single-row table holding the kill switch."""

    __tablename__ = "agent_state"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    paused_reason: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[uuid.UUID | None] = fk("users.id")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


ALL_TABLES: tuple[str, ...] = tuple(sorted(Base.metadata.tables))
