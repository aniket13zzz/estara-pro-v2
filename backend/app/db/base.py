"""Declarative base, naming conventions and shared column helpers."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

from app.core.enums import RecordStatus

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def str_enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    """Store a StrEnum as VARCHAR with a CHECK constraint (no native PG enum; easier migrations)."""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


def created_at_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CommercialMixin:
    """Columns every Truth Layer table carries.

    Staleness is NOT stored. It is computed in Stage 2 from ``approved_at`` vs
    ``truth.stale_after_days`` and ``valid_to``.
    """

    status: Mapped[RecordStatus] = mapped_column(
        str_enum(RecordStatus, "status"), default=RecordStatus.DRAFT, nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)

    @declared_attr
    def approved_by(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))

    @declared_attr
    def source_batch_id(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(Uuid, ForeignKey("import_batches.id", ondelete="SET NULL"))

    @classmethod
    def commercial_constraints(cls) -> tuple[Any, ...]:
        return (
            CheckConstraint(
                "status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
                name="approved_has_approver",
            ),
            CheckConstraint(
                "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
                name="valid_range",
            ),
            CheckConstraint("version >= 1", name="version_positive"),
        )


INR = BigInteger  # money is stored as whole rupees
SHORT = String(64)
LANG = String(8)
