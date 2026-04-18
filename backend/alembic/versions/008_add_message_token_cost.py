"""Add token + cost columns to messages

Records the LLM usage and USD cost for each assistant message so per-
conversation totals can be computed without re-running the LLM. Nullable:
historical rows and non-assistant rows (user / action) stay null.

Revision ID: 008
Revises: 007
Create Date: 2026-04-16 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("input_tokens", sa.Integer, nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("output_tokens", sa.Integer, nullable=True),
    )
    # Numeric(10,6) covers six decimal places of a dollar — enough for any
    # per-message cost we'd realistically see (sub-cent precision).
    op.add_column(
        "messages",
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "cost_usd")
    op.drop_column("messages", "output_tokens")
    op.drop_column("messages", "input_tokens")
