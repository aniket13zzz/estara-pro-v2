"""Step W: WhatsApp opt-in time on customers; once-only visit notice times on site visits.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("customers", sa.Column("whatsapp_opt_in_at", TS, nullable=True))
    for name in ("requested_notice_at", "confirmed_notice_at", "reminder_sent_at"):
        op.add_column("site_visits", sa.Column(name, TS, nullable=True))


def downgrade() -> None:
    for name in ("reminder_sent_at", "confirmed_notice_at", "requested_notice_at"):
        op.drop_column("site_visits", name)
    op.drop_column("customers", "whatsapp_opt_in_at")
