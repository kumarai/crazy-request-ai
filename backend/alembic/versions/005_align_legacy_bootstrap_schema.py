"""Align legacy create_all schema with Alembic-managed head.

Revision ID: 005
Revises: 004
Create Date: 2026-04-07 00:00:00.000000

This revision is intentionally idempotent. It reconciles databases that
were bootstrapped outside Alembic so they can be stamped at revision 004
and then upgraded safely to head.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS credentials (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL UNIQUE,
            credential_type TEXT NOT NULL DEFAULT 'token',
            encrypted_value TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )

    op.execute("ALTER TABLE sources ADD COLUMN IF NOT EXISTS credential_id UUID")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name = 'sources'
            ) THEN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = 'sources'::regclass
                      AND conname = 'sources_credential_id_fkey'
                ) THEN
                    ALTER TABLE sources
                    ADD CONSTRAINT sources_credential_id_fkey
                    FOREIGN KEY (credential_id)
                    REFERENCES credentials(id)
                    ON DELETE SET NULL;
                END IF;
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        DELETE FROM code_dependencies a
        USING code_dependencies b
        WHERE a.id > b.id
          AND a.source_id = b.source_id
          AND a.from_file = b.from_file
          AND a.to_file = b.to_file
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'code_dependencies'::regclass
                  AND conname = 'uq_dep_source_from_to'
            ) THEN
                ALTER TABLE code_dependencies
                ADD CONSTRAINT uq_dep_source_from_to
                UNIQUE (source_id, from_file, to_file);
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        ALTER TABLE code_chunks
        DROP CONSTRAINT IF EXISTS code_chunks_file_path_name_start_line_key
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'code_chunks'::regclass
                  AND conname = 'uq_chunk_source_file_name_line'
            ) THEN
                ALTER TABLE code_chunks
                ADD CONSTRAINT uq_chunk_source_file_name_line
                UNIQUE (source_id, file_path, name, start_line);
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        ALTER TABLE code_chunks
        ADD COLUMN IF NOT EXISTS fts_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(qualified_name, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(reuse_signal, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(purpose, '')), 'B') ||
            setweight(to_tsvector('english', coalesce(summary, '')), 'B') ||
            setweight(to_tsvector('english', coalesce(array_to_string(domain_tags, ' '), '')), 'B') ||
            setweight(to_tsvector('english', coalesce(content, '')), 'C')
        ) STORED
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chunks_embedding
        ON code_chunks USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chunks_summary_embedding
        ON code_chunks USING ivfflat (summary_embedding vector_cosine_ops)
        WITH (lists = 100)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chunks_fts
        ON code_chunks USING GIN (fts_vector)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chunks_domain_tags
        ON code_chunks USING GIN (domain_tags)
        """
    )

    op.execute("DROP INDEX IF EXISTS idx_jobs_source_id")
    op.execute(
        """
        CREATE INDEX idx_jobs_source_id
        ON index_jobs (source_id, started_at DESC)
        """
    )


def downgrade() -> None:
    # This revision only reconciles legacy bootstrap drift, so downgrade is a no-op.
    pass
