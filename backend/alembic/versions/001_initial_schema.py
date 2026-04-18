"""Initial schema

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "sources",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True,
                   server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("config", sa.dialects.postgresql.JSONB, nullable=False,
                   server_default="{}"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "code_chunks",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True,
                   server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_id", sa.dialects.postgresql.UUID(as_uuid=True),
                   sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=True),
        sa.Column("source_type", sa.Text, nullable=False, server_default="code"),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("repo_root", sa.Text, nullable=False, server_default=""),
        sa.Column("language", sa.Text, nullable=False),
        sa.Column("chunk_type", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("qualified_name", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_with_context", sa.Text, nullable=True),
        sa.Column("start_line", sa.Integer, nullable=False, server_default="0"),
        sa.Column("end_line", sa.Integer, nullable=False, server_default="0"),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("purpose", sa.Text, nullable=True),
        sa.Column("signature", sa.Text, nullable=True),
        sa.Column("reuse_signal", sa.Text, nullable=True),
        sa.Column("domain_tags", sa.dialects.postgresql.ARRAY(sa.Text), nullable=True),
        sa.Column("complexity", sa.Text, nullable=True),
        sa.Column("imports_used", sa.dialects.postgresql.ARRAY(sa.Text), nullable=True),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("summary_embedding", Vector(1536), nullable=True),
        sa.Column("metadata", sa.dialects.postgresql.JSONB, server_default="{}"),
        sa.Column("commit_sha", sa.Text, nullable=True),
        sa.Column("wiki_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("file_path", "name", "start_line"),
    )

    # FTS column — maintained by trigger since to_tsvector is STABLE, not IMMUTABLE
    op.execute("ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS fts_vector tsvector")

    op.execute("""
        CREATE OR REPLACE FUNCTION code_chunks_fts_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.fts_vector :=
                setweight(to_tsvector('english', coalesce(NEW.name, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(NEW.qualified_name, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(NEW.reuse_signal, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(NEW.purpose, '')), 'B') ||
                setweight(to_tsvector('english', coalesce(NEW.summary, '')), 'B') ||
                setweight(to_tsvector('english', coalesce(array_to_string(NEW.domain_tags, ' '), '')), 'B') ||
                setweight(to_tsvector('english', coalesce(NEW.content, '')), 'C');
            RETURN NEW;
        END $$
    """)

    op.execute("""
        CREATE TRIGGER trg_code_chunks_fts
        BEFORE INSERT OR UPDATE ON code_chunks
        FOR EACH ROW EXECUTE FUNCTION code_chunks_fts_update()
    """)

    op.create_table(
        "code_dependencies",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True,
                   server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_id", sa.dialects.postgresql.UUID(as_uuid=True),
                   sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=True),
        sa.Column("from_file", sa.Text, nullable=False),
        sa.Column("to_file", sa.Text, nullable=False),
        sa.Column("import_names", sa.dialects.postgresql.ARRAY(sa.Text),
                   server_default="{}"),
        sa.Column("dep_type", sa.Text, server_default="import"),
    )

    op.create_table(
        "index_checkpoint",
        sa.Column("source_id", sa.dialects.postgresql.UUID(as_uuid=True),
                   sa.ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("last_commit_sha", sa.Text, nullable=True),
        sa.Column("last_indexed_at", sa.DateTime(timezone=True),
                   server_default=sa.func.now()),
    )

    op.create_table(
        "wiki_checkpoint",
        sa.Column("source_id", sa.dialects.postgresql.UUID(as_uuid=True),
                   sa.ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("page_slug", sa.Text, primary_key=True),
        sa.Column("gitlab_page_id", sa.Integer, nullable=True),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True),
                   server_default=sa.func.now()),
    )

    op.create_table(
        "index_jobs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True,
                   server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_id", sa.dialects.postgresql.UUID(as_uuid=True),
                   sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=True),
        sa.Column("celery_task_id", sa.Text, nullable=True),
        sa.Column("status", sa.Text, server_default="pending"),
        sa.Column("triggered_by", sa.Text, server_default="schedule"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("stats", sa.dialects.postgresql.JSONB, server_default="{}"),
    )

    # Indexes
    op.create_index("idx_chunks_embedding", "code_chunks", ["embedding"],
                     postgresql_using="ivfflat",
                     postgresql_with={"lists": 100},
                     postgresql_ops={"embedding": "vector_cosine_ops"})
    op.create_index("idx_chunks_summary_embedding", "code_chunks", ["summary_embedding"],
                     postgresql_using="ivfflat",
                     postgresql_with={"lists": 100},
                     postgresql_ops={"summary_embedding": "vector_cosine_ops"})
    op.create_index("idx_chunks_fts", "code_chunks", ["fts_vector"],
                     postgresql_using="gin")
    op.create_index("idx_chunks_source_id", "code_chunks", ["source_id"])
    op.create_index("idx_chunks_file_path", "code_chunks", ["file_path"])
    op.create_index("idx_chunks_name", "code_chunks", ["name"])
    op.create_index("idx_chunks_source_type", "code_chunks",
                     ["source_type", "language", "chunk_type"])
    op.create_index("idx_chunks_domain_tags", "code_chunks", ["domain_tags"],
                     postgresql_using="gin")
    op.create_index("idx_jobs_source_id", "index_jobs", ["source_id",
                     sa.text("started_at DESC")])


def downgrade() -> None:
    op.drop_table("index_jobs")
    op.drop_table("wiki_checkpoint")
    op.drop_table("index_checkpoint")
    op.drop_table("code_dependencies")
    op.drop_table("code_chunks")
    op.drop_table("sources")
    op.execute("DROP TRIGGER IF EXISTS trg_code_chunks_fts ON code_chunks")
    op.execute("DROP FUNCTION IF EXISTS code_chunks_fts_update()")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS vector")
