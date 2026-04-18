"""Add title column to conversations

Short auto-generated summary of the conversation (5–8 words) used as the
sidebar label so users can scan their history without reading the first
message. Nullable; smalltalk/OOS flows leave it empty and the UI falls
back to the first user message.

Revision ID: 009
Revises: 008
Create Date: 2026-04-17 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("title", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "title")
