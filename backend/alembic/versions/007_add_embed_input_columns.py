"""Add embed_input, side_effects, example_call columns

Minimal migration to support the new chunks-preview and re-embed endpoints.
The bigger refactor (drop summary_embedding, change vector dim, swap embedder
to voyage-code-3) is intentionally separate — it will come as migration 008
once the new embedder path is in.

Revision ID: 007
Revises: 006
Create Date: 2026-04-16 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "code_chunks",
        sa.Column("embed_input", sa.Text, nullable=True),
    )
    op.add_column(
        "code_chunks",
        sa.Column("side_effects", sa.Text, nullable=True),
    )
    op.add_column(
        "code_chunks",
        sa.Column("example_call", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("code_chunks", "example_call")
    op.drop_column("code_chunks", "side_effects")
    op.drop_column("code_chunks", "embed_input")
