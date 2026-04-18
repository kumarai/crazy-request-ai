"""Add conversations, messages, and tool_calls tables for customer support

Revision ID: 006
Revises: 005
Create Date: 2026-04-15 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("customer_id", sa.Text, nullable=False),
        sa.Column("channel", sa.Text, nullable=False, server_default="web"),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("last_specialist", sa.Text, nullable=True),
        sa.Column("last_handoff_json", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("rolling_summary", sa.Text, nullable=True),
        sa.Column(
            "unresolved_facts_json",
            sa.dialects.postgresql.JSONB,
            nullable=True,
            server_default="[]",
        ),
        sa.Column("metadata_json", sa.dialects.postgresql.JSONB, nullable=True),
    )
    op.create_index(
        "idx_conversations_customer_id",
        "conversations",
        ["customer_id", "status"],
    )

    op.create_table(
        "messages",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "conversation_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("specialist_used", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("citations_json", sa.dialects.postgresql.JSONB, nullable=True),
    )
    op.create_index(
        "idx_messages_conversation_id",
        "messages",
        ["conversation_id", "created_at"],
    )

    op.create_table(
        "tool_calls",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "conversation_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("tool_name", sa.Text, nullable=False),
        sa.Column("input_json", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("output_json", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_tool_calls_conversation_id",
        "tool_calls",
        ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_table("tool_calls")
    op.drop_table("messages")
    op.drop_table("conversations")
