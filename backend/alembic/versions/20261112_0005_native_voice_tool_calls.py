"""Native voice (ADR 0014): one row per Vapi tool call; numbers to check on each call.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('vapi_tool_calls',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('tool_call_id', sa.String(length=200), nullable=False),
    sa.Column('vapi_call_id', sa.String(length=200), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('arguments', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_vapi_tool_calls')),
    sa.UniqueConstraint('tool_call_id', name=op.f('uq_vapi_tool_calls_tool_call_id'))
    )
    op.create_index('ix_vapi_tool_calls_vapi_call_id', 'vapi_tool_calls', ['vapi_call_id'], unique=False)
    op.add_column('calls', sa.Column('unverified_numbers', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('calls', 'unverified_numbers')
    op.drop_index('ix_vapi_tool_calls_vapi_call_id', table_name='vapi_tool_calls')
    op.drop_table('vapi_tool_calls')
