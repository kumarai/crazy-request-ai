from __future__ import annotations

import argparse
import asyncio
import logging
import re
from pathlib import Path

import asyncpg
from alembic import command
from alembic.config import Config

from app.config import settings

logger = logging.getLogger("[db.migrations]")

ALEMBIC_HEAD = "head"
LEGACY_BASELINE_REVISION = "004"
MIGRATION_LOCK_ID = 7246314921317231
DATABASE_READY_RETRIES = 30
DATABASE_READY_SLEEP_SECONDS = 2.0

LEGACY_CORE_TABLES = frozenset(
    {
        "sources",
        "code_chunks",
        "code_dependencies",
        "index_checkpoint",
        "wiki_checkpoint",
        "index_jobs",
    }
)
APP_TABLES = LEGACY_CORE_TABLES | {"credentials"}
_VECTOR_TYPE_RE = re.compile(r"vector\((\d+)\)")


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _alembic_config() -> Config:
    backend_root = _backend_root()
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("prepend_sys_path", str(backend_root))
    return config


def _stamp_legacy_schema() -> None:
    logger.info(
        "Stamping legacy unmanaged schema at Alembic revision %s",
        LEGACY_BASELINE_REVISION,
    )
    command.stamp(_alembic_config(), LEGACY_BASELINE_REVISION)


def _upgrade_head() -> None:
    logger.info("Running Alembic upgrade %s", ALEMBIC_HEAD)
    command.upgrade(_alembic_config(), ALEMBIC_HEAD)


async def _connect_with_retry() -> asyncpg.Connection:
    last_error: Exception | None = None

    for attempt in range(1, DATABASE_READY_RETRIES + 1):
        try:
            return await asyncpg.connect(settings.database_url)
        except Exception as exc:  # pragma: no cover - network/db availability
            last_error = exc
            logger.warning(
                "Database not ready for migrations (%d/%d): %s",
                attempt,
                DATABASE_READY_RETRIES,
                exc,
            )
            await asyncio.sleep(DATABASE_READY_SLEEP_SECONDS)

    raise RuntimeError("Database never became ready for migrations") from last_error


async def _existing_tables(connection: asyncpg.Connection) -> set[str]:
    rows = await connection.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_name = ANY($1::text[])
        """,
        list(APP_TABLES | {"alembic_version"}),
    )
    return {row["table_name"] for row in rows}


async def _current_vector_dims(
    connection: asyncpg.Connection,
) -> dict[str, int]:
    rows = await connection.fetch(
        """
        SELECT
            a.attname AS column_name,
            format_type(a.atttypid, a.atttypmod) AS column_type
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = 'code_chunks'
          AND n.nspname = current_schema()
          AND a.attname IN ('embedding', 'summary_embedding')
          AND a.attnum > 0
          AND NOT a.attisdropped
        """
    )
    dims: dict[str, int] = {}
    for row in rows:
        column_type = row["column_type"] or ""
        match = _VECTOR_TYPE_RE.fullmatch(column_type)
        if match:
            dims[row["column_name"]] = int(match.group(1))
    return dims


async def _align_vector_dimensions(connection: asyncpg.Connection) -> None:
    if "code_chunks" not in await _existing_tables(connection):
        return

    dims = await _current_vector_dims(connection)
    target_dim = settings.llm_embedding_dimensions
    current = dims.get("embedding")

    if current is None or current == target_dim:
        return

    logger.warning(
        "Aligning code_chunks vector columns from %s to %s dimensions; "
        "existing embeddings will be cleared and must be rebuilt.",
        current,
        target_dim,
    )

    await connection.execute("DROP INDEX IF EXISTS idx_chunks_embedding")
    await connection.execute("DROP INDEX IF EXISTS idx_chunks_summary_embedding")
    await connection.execute(
        """
        UPDATE code_chunks
        SET embedding = NULL,
            summary_embedding = NULL
        WHERE embedding IS NOT NULL
           OR summary_embedding IS NOT NULL
        """
    )
    await connection.execute(
        f"ALTER TABLE code_chunks ALTER COLUMN embedding TYPE vector({target_dim})"
    )
    await connection.execute(
        f"ALTER TABLE code_chunks ALTER COLUMN summary_embedding TYPE vector({target_dim})"
    )
    await connection.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_chunks_embedding
        ON code_chunks USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = {settings.ivfflat_lists})
        """
    )
    await connection.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_chunks_summary_embedding
        ON code_chunks USING ivfflat (summary_embedding vector_cosine_ops)
        WITH (lists = {settings.ivfflat_lists})
        """
    )


async def upgrade_database() -> None:
    connection = await _connect_with_retry()

    try:
        await connection.execute("SELECT pg_advisory_lock($1)", MIGRATION_LOCK_ID)

        existing_tables = await _existing_tables(connection)
        has_alembic_version = "alembic_version" in existing_tables
        has_app_tables = bool(existing_tables & APP_TABLES)

        if not has_alembic_version and has_app_tables:
            if not LEGACY_CORE_TABLES.issubset(existing_tables):
                table_list = ", ".join(sorted(existing_tables))
                raise RuntimeError(
                    "Database has unmanaged partial schema and cannot be auto-stamped. "
                    f"Existing tables: {table_list}"
                )

            await asyncio.to_thread(_stamp_legacy_schema)

        await asyncio.to_thread(_upgrade_head)
        await _align_vector_dimensions(connection)
        logger.info("Database migrations completed")
    finally:
        try:
            await connection.execute("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK_ID)
        finally:
            await connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Alembic database migrations.")
    parser.add_argument(
        "command",
        choices=["upgrade"],
        help="Migration action to run.",
    )
    args = parser.parse_args(argv)

    if args.command == "upgrade":
        asyncio.run(upgrade_database())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
