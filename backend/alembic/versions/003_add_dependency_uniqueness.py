"""Add uniqueness constraint to code_dependencies

Revision ID: 003
Revises: 002
Create Date: 2024-01-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove duplicate rows before adding the constraint
    op.execute("""
        DELETE FROM code_dependencies a
        USING code_dependencies b
        WHERE a.id > b.id
          AND a.source_id = b.source_id
          AND a.from_file = b.from_file
          AND a.to_file = b.to_file
    """)
    op.create_unique_constraint(
        "uq_dep_source_from_to",
        "code_dependencies",
        ["source_id", "from_file", "to_file"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_dep_source_from_to", "code_dependencies")
