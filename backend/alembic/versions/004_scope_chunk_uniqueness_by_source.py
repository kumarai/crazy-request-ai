"""Scope chunk uniqueness by source_id

Revision ID: 004
Revises: 003
Create Date: 2024-01-04 00:00:00.000000

Existing rows may have been cross-source overwritten under the old
(file_path, name, start_line) constraint. After this migration,
run a full reindex on all sources to restore correct data.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop old constraint
    op.drop_constraint(
        "code_chunks_file_path_name_start_line_key", "code_chunks", type_="unique"
    )
    # Create new source-scoped constraint
    op.create_unique_constraint(
        "uq_chunk_source_file_name_line",
        "code_chunks",
        ["source_id", "file_path", "name", "start_line"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_chunk_source_file_name_line", "code_chunks", type_="unique")
    op.create_unique_constraint(
        "code_chunks_file_path_name_start_line_key",
        "code_chunks",
        ["file_path", "name", "start_line"],
    )
