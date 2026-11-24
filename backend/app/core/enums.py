"""Enumerations shared by config, database, Truth Layer, tools and tests.

These are domain vocabularies, not client settings. Languages are NOT listed here:
supported languages come only from ``agent.languages`` in the client config.
"""

from enum import StrEnum


class FactType(StrEnum):
    """The fact types the Truth Layer serves. One enum everywhere (ADR 0009 added amenity)."""

    PRICE = "price"
    AVAILABILITY = "availability"
    OFFER = "offer"
    PAYMENT_PLAN = "payment_plan"
    POSSESSION = "possession"
    AREA = "area"
    AMENITY = "amenity"


class Role(StrEnum):
    CLIENT_ADMIN = "client_admin"
    SALES_STAFF = "sales_staff"


class RecordStatus(StrEnum):
    """Status of a commercial (Truth) row. Staleness is computed, never stored."""

    DRAFT = "draft"
    APPROVED = "approved"
    ARCHIVED = "archived"


class Channel(StrEnum):
    VOICE = "voice"
    WEB_CHAT = "web_chat"
    WHATSAPP = "whatsapp"
    EMAIL = "email"


class IdentityType(StrEnum):
    PHONE = "phone"
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    WEB_SESSION = "web_session"


class LeadState(StrEnum):
    NEW_ENQUIRY = "new_enquiry"
    ENGAGED = "engaged"
    QUALIFIED = "qualified"
    VISIT_REQUESTED = "visit_requested"
    VISIT_CONFIRMED = "visit_confirmed"
    HANDED_OFF = "handed_off"
    NURTURING = "nurturing"
    WON = "won"
    LOST = "lost"


class LeadBand(StrEnum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class VisitState(StrEnum):
    REQUESTED = "requested"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageSender(StrEnum):
    CUSTOMER = "customer"
    AGENT = "agent"
    STAFF = "staff"
    SYSTEM = "system"


class HandoffStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    CLOSED = "closed"


class FollowUpStatus(StrEnum):
    SCHEDULED = "scheduled"
    SENT = "sent"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ImportBatchStatus(StrEnum):
    UPLOADED = "uploaded"
    VALIDATED = "validated"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"
    ARCHIVED = "archived"


class Severity(StrEnum):
    """Validation finding or alert severity."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class WebhookProvider(StrEnum):
    VAPI = "vapi"
    WHATSAPP = "whatsapp"
    EMAIL = "email"


class UsageKind(StrEnum):
    CALL_MINUTES = "call_minutes"
    LLM_TOKENS = "llm_tokens"
    WHATSAPP_MESSAGE = "whatsapp_message"
    EMAIL_MESSAGE = "email_message"
    EMBEDDING_TOKENS = "embedding_tokens"


# Lead-scoring weight keys the Stage 3 scoring rules understand.
SCORING_WEIGHT_KEYS: frozenset[str] = frozenset(
    {"budget_match", "location_match", "timeline_under_6m", "loan_ready", "visit_requested"}
)

# Requirement fields that ``lead_scoring.qualified_requires`` may name.
REQUIREMENT_FIELDS: frozenset[str] = frozenset(
    {"budget", "location", "property_type", "bhk", "timeline", "loan", "purpose"}
)

# pgvector column size for document_chunks.embedding. Fixed for the template (ADR 0005 / plan).
EMBEDDING_DIMENSIONS = 768
