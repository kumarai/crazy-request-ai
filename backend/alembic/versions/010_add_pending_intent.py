"""Add pending_intent_json column to conversations.

When the auth gate fires for a guest (they tried to place an order,
pay a bill, book an appointment, or view billing), the orchestrator
stashes the original ``{specialist, query}`` in this column before
handing the turn off to the ``account`` specialist. After the guest
signs in, the account agent asks the customer to confirm a resume,
and if they accept the orchestrator replays the stored query against
the original specialist. Cleared after a resume or on sign-out so
later turns start from a clean slate.

Revision ID: 010
Revises: 009
Create Date: 2026-04-17 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("pending_intent_json", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "pending_intent_json")
