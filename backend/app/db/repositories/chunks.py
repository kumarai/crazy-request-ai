from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import case, delete, func, literal, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.db.models import CodeChunk, CodeDependency

logger = logging.getLogger("[db]")

# Strip non-word chars to produce safe tsquery tokens
_TSQUERY_CLEAN = re.compile(r"[^\w\s]")
_BATCH_SIZE = 200


def _safe_tsquery(raw: str) -> str:
    """Build a safe tsquery string from arbitrary user input.

    Strips punctuation, splits on whitespace, joins with &.
    Returns empty string if no valid tokens remain.
    """
    cleaned = _TSQUERY_CLEAN.sub(" ", raw)
    tokens = [t.strip() for t in cleaned.split() if t.strip()]
    if not tokens:
        return ""
    return " & ".join(tokens)


def _ivfflat_probes_stmt() -> Any:
    """Postgres SET does not accept bind params here, so inline the validated int."""
    probes = max(1, int(settings.ivfflat_probes))
    return text(f"SET LOCAL ivfflat.probes = {probes}")


class ChunksRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # ------------------------------------------------------------------
    # Batch upsert (replaces one-at-a-time upsert)
    # ------------------------------------------------------------------
    async def upsert_chunks_batch(self, chunks: list[dict[str, Any]]) -> int:
        if not chunks:
            return 0
        async with self._sf() as session:
            total = 0
            for i in range(0, len(chunks), _BATCH_SIZE):
                batch = chunks[i : i + _BATCH_SIZE]
                for chunk in batch:
                    vals = {
                        "source_id": chunk["source_id"],
                        "source_type": chunk.get("source_type", "code"),
                        "file_path": chunk["file_path"],
                        "repo_root": chunk.get("repo_root", ""),
                        "language": chunk["language"],
                        "chunk_type": chunk["chunk_type"],
                        "name": chunk["name"],
                        "qualified_name": chunk["qualified_name"],
                        "content": chunk["content"],
                        "content_with_context": chunk.get("content_with_context"),
                        "start_line": chunk.get("start_line", 0),
                        "end_line": chunk.get("end_line", 0),
                        "summary": chunk.get("summary"),
                        "purpose": chunk.get("purpose"),
                        "signature": chunk.get("signature"),
                        "reuse_signal": chunk.get("reuse_signal"),
                        "domain_tags": chunk.get("domain_tags", []),
                        "complexity": chunk.get("complexity"),
                        "imports_used": chunk.get("imports_used", []),
                        "embedding": chunk.get("embedding"),
                        "summary_embedding": chunk.get("summary_embedding"),
                        "metadata": chunk.get("metadata", {}),
                        "commit_sha": chunk.get("commit_sha"),
                    }
                    stmt = (
                        insert(CodeChunk.__table__)
                        .values(**vals)
                        .on_conflict_do_update(
                            constraint="uq_chunk_source_file_name_line",
                            set_={
                                k: v
                                for k, v in vals.items()
                                if k not in ("source_id", "file_path", "name", "start_line")
                            }
                            | {"indexed_at": func.now()},
                        )
                    )
                    await session.execute(stmt)
                    total += 1
                # Flush per batch to reduce memory pressure
                await session.flush()
            await session.commit()
            return total

    # Keep old name as alias for backward compat
    upsert_chunks = upsert_chunks_batch

    # ------------------------------------------------------------------
    # Batch dependency upsert with uniqueness
    # ------------------------------------------------------------------
    async def upsert_dependencies_batch(
        self, deps: list[dict[str, Any]]
    ) -> int:
        if not deps:
            return 0
        async with self._sf() as session:
            count = 0
            for dep in deps:
                stmt = (
                    insert(CodeDependency)
                    .values(
                        source_id=dep["source_id"],
                        from_file=dep["from_file"],
                        to_file=dep["to_file"],
                        import_names=dep.get("import_names", []),
                        dep_type=dep.get("dep_type", "import"),
                    )
                    .on_conflict_do_update(
                        constraint="uq_dep_source_from_to",
                        set_={
                            "import_names": dep.get("import_names", []),
                            "dep_type": dep.get("dep_type", "import"),
                        },
                    )
                )
                await session.execute(stmt)
                count += 1
            await session.commit()
            return count

    upsert_dependencies = upsert_dependencies_batch

    # ------------------------------------------------------------------
    # Deletes
    # ------------------------------------------------------------------
    async def delete_chunks_for_files(
        self, file_paths: list[str], source_id: UUID
    ) -> int:
        if not file_paths:
            return 0
        async with self._sf() as session:
            stmt = delete(CodeChunk).where(
                CodeChunk.source_id == source_id,
                CodeChunk.file_path.in_(file_paths),
            )
            result = await session.execute(stmt)
            await session.commit()
            deleted = result.rowcount
            logger.info(
                "Deleted %d chunks for %d files in source %s",
                deleted,
                len(file_paths),
                source_id,
            )
            return deleted

    async def get_content_hashes(
        self, source_id: UUID
    ) -> dict[tuple[str, str, int], str]:
        """Return {(file_path, name, start_line): md5(content)} for existing chunks.

        Used during full reindex to skip summarise+embed for unchanged chunks.
        """
        import hashlib
        async with self._sf() as session:
            stmt = select(
                CodeChunk.file_path,
                CodeChunk.name,
                CodeChunk.start_line,
                CodeChunk.content,
            ).where(CodeChunk.source_id == source_id)
            result = await session.execute(stmt)
            return {
                (row.file_path, row.name, row.start_line): hashlib.md5(
                    row.content.encode()
                ).hexdigest()
                for row in result.all()
            }

    async def list_chunks_for_source(
        self,
        source_id: UUID,
        limit: int = 50,
        q: str | None = None,
    ) -> list[dict[str, Any]]:
        """List chunks for a source, optionally filtered by name/file_path substring.

        Used by the Sources page chunk-preview drawer to show exactly what the
        embedder produced. Returns newest-indexed first.
        """
        async with self._sf() as session:
            stmt = (
                select(CodeChunk)
                .where(CodeChunk.source_id == source_id)
                .order_by(CodeChunk.indexed_at.desc().nullslast())
                .limit(limit)
            )
            if q:
                like = f"%{q}%"
                stmt = stmt.where(
                    (CodeChunk.name.ilike(like))
                    | (CodeChunk.qualified_name.ilike(like))
                    | (CodeChunk.file_path.ilike(like))
                )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [row.to_dict() for row in rows]

    async def iter_chunks_for_source(
        self, source_id: UUID
    ) -> list[dict[str, Any]]:
        """Return all chunks for a source as dicts (with id + content).

        Used by reembed to rebuild embed_input + embedding without re-parsing
        or re-summarizing.
        """
        async with self._sf() as session:
            stmt = (
                select(CodeChunk)
                .where(CodeChunk.source_id == source_id)
                .order_by(CodeChunk.id)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            out = []
            for row in rows:
                d = row.to_dict()
                d["id"] = row.id  # keep UUID, not str, for update_embeddings
                out.append(d)
            return out

    async def update_embeddings(
        self, updates: list[dict[str, Any]]
    ) -> int:
        """Update embedding + embed_input for existing chunks by id.

        updates: [{"id": UUID, "embedding": list[float], "embed_input": str}, ...]
        """
        if not updates:
            return 0
        from sqlalchemy import update as _update

        async with self._sf() as session:
            count = 0
            for u in updates:
                stmt = (
                    _update(CodeChunk)
                    .where(CodeChunk.id == u["id"])
                    .values(
                        embedding=u.get("embedding"),
                        embed_input=u.get("embed_input"),
                    )
                )
                result = await session.execute(stmt)
                count += result.rowcount
            await session.commit()
            return count

    async def get_file_indexed_times(
        self, source_id: UUID
    ) -> dict[str, datetime]:
        """Return {file_path: max(indexed_at)} for all chunks in a source."""
        async with self._sf() as session:
            stmt = (
                select(
                    CodeChunk.file_path,
                    func.max(CodeChunk.indexed_at).label("last_indexed"),
                )
                .where(CodeChunk.source_id == source_id)
                .group_by(CodeChunk.file_path)
            )
            result = await session.execute(stmt)
            return {row.file_path: row.last_indexed for row in result.all()}

    async def delete_chunks_not_in_files(
        self, keep_file_paths: list[str], source_id: UUID
    ) -> int:
        """Delete stale chunks whose file_path is not in the keep list."""
        async with self._sf() as session:
            stmt = delete(CodeChunk).where(
                CodeChunk.source_id == source_id,
                ~CodeChunk.file_path.in_(keep_file_paths),
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    async def delete_chunks_for_source_slug(
        self, source_id: UUID, slug: str
    ) -> int:
        async with self._sf() as session:
            stmt = delete(CodeChunk).where(
                CodeChunk.source_id == source_id,
                CodeChunk.file_path.like(f"%/{slug}%"),
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    async def delete_dependencies_for_files(
        self, file_paths: list[str], source_id: UUID
    ) -> int:
        if not file_paths:
            return 0
        async with self._sf() as session:
            stmt = delete(CodeDependency).where(
                CodeDependency.source_id == source_id,
                CodeDependency.from_file.in_(file_paths),
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    # ------------------------------------------------------------------
    # Search methods
    # ------------------------------------------------------------------
    async def vector_search(
        self,
        embedding: list[float],
        limit: int = 40,
        source_ids: list[UUID] | None = None,
        use_summary: bool = False,
    ) -> list[dict[str, Any]]:
        col = CodeChunk.summary_embedding if use_summary else CodeChunk.embedding
        async with self._sf() as session:
            await session.execute(_ivfflat_probes_stmt())
            score_expr = (1 - col.cosine_distance(embedding)).label("score")
            stmt = (
                select(CodeChunk, score_expr)
                .where(col.isnot(None))
                .order_by(col.cosine_distance(embedding))
                .limit(limit)
            )
            if source_ids:
                stmt = stmt.where(CodeChunk.source_id.in_(source_ids))

            result = await session.execute(stmt)
            rows = result.all()
            return [
                row.CodeChunk.to_dict() | {"score": row.score}
                for row in rows
            ]

    async def bm25_search(
        self,
        query: str,
        limit: int = 40,
        source_ids: list[UUID] | None = None,
    ) -> list[dict[str, Any]]:
        ts_query = _safe_tsquery(query)
        if not ts_query:
            return []
        async with self._sf() as session:
            ts_q = func.to_tsquery("english", ts_query)
            score_expr = func.ts_rank_cd(
                text("fts_vector"), ts_q
            ).label("score")

            stmt = (
                select(CodeChunk, score_expr)
                .where(text("fts_vector @@ to_tsquery('english', :q)").bindparams(q=ts_query))
                .order_by(score_expr.desc())
                .limit(limit)
            )
            if source_ids:
                stmt = stmt.where(CodeChunk.source_id.in_(source_ids))

            result = await session.execute(stmt)
            rows = result.all()
            return [
                row.CodeChunk.to_dict() | {"score": row.score}
                for row in rows
            ]

    async def symbol_search(
        self,
        symbol: str,
        limit: int = 20,
        source_ids: list[UUID] | None = None,
    ) -> list[dict[str, Any]]:
        async with self._sf() as session:
            score_expr = func.similarity(CodeChunk.name, symbol).label("score")
            stmt = (
                select(CodeChunk, score_expr)
                .where(
                    text("name % :sym OR qualified_name ILIKE :pattern").bindparams(
                        sym=symbol, pattern=f"%{symbol}%"
                    )
                )
                .order_by(score_expr.desc())
                .limit(limit)
            )
            if source_ids:
                stmt = stmt.where(CodeChunk.source_id.in_(source_ids))
            result = await session.execute(stmt)
            rows = result.all()
            return [
                row.CodeChunk.to_dict() | {"score": row.score}
                for row in rows
            ]

    async def domain_tag_search(
        self,
        tags: list[str],
        limit: int = 20,
        source_ids: list[UUID] | None = None,
    ) -> list[dict[str, Any]]:
        normalized_tags = [tag.strip() for tag in tags if tag.strip()]
        if not normalized_tags:
            return []

        async with self._sf() as session:
            score_value = literal(0)
            for tag in normalized_tags:
                score_value = score_value + case(
                    (func.array_position(CodeChunk.domain_tags, tag).isnot(None), 1),
                    else_=0,
                )
            score_expr = score_value.label("score")

            stmt = (
                select(CodeChunk, score_expr)
                .where(score_value > 0)
                .order_by(score_expr.desc(), CodeChunk.indexed_at.desc())
                .limit(limit)
            )
            if source_ids:
                stmt = stmt.where(CodeChunk.source_id.in_(source_ids))
            result = await session.execute(stmt)
            rows = result.all()
            return [
                row.CodeChunk.to_dict() | {"score": row.score or 0}
                for row in rows
            ]

    async def get_graph_neighbors(
        self,
        file_paths: list[str],
        source_ids: list[UUID] | None = None,
    ) -> list[dict[str, Any]]:
        """Get neighbor chunks via dependency graph (both directions), scoped by source_ids."""
        async with self._sf() as session:
            # Forward: files that our files import (from_file -> to_file)
            forward = (
                select(CodeChunk)
                .join(
                    CodeDependency,
                    (CodeChunk.file_path == CodeDependency.to_file)
                    & (CodeChunk.source_id == CodeDependency.source_id),
                )
                .where(CodeDependency.from_file.in_(file_paths))
            )
            # Reverse: files that import our files (to_file -> from_file)
            reverse = (
                select(CodeChunk)
                .join(
                    CodeDependency,
                    (CodeChunk.file_path == CodeDependency.from_file)
                    & (CodeChunk.source_id == CodeDependency.source_id),
                )
                .where(CodeDependency.to_file.in_(file_paths))
            )
            if source_ids:
                forward = forward.where(CodeDependency.source_id.in_(source_ids))
                reverse = reverse.where(CodeDependency.source_id.in_(source_ids))

            combined = forward.union(reverse).limit(20)
            result = await session.execute(combined)
            return [row.to_dict() for row in result.scalars().all()]

    async def search_hybrid(
        self,
        query: str,
        embedding: list[float],
        limit: int = 20,
        source_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        ts_query = _safe_tsquery(query)
        async with self._sf() as session:
            await session.execute(_ivfflat_probes_stmt())

            if ts_query:
                score_expr = (
                    (1 - CodeChunk.embedding.cosine_distance(embedding)) * 0.6
                    + func.ts_rank_cd(
                        text("fts_vector"),
                        func.to_tsquery("english", ts_query),
                    )
                    * 0.4
                ).label("score")

                stmt = (
                    select(CodeChunk, score_expr)
                    .where(
                        (CodeChunk.embedding.isnot(None))
                        | text("fts_vector @@ to_tsquery('english', :q)").bindparams(
                            q=ts_query
                        )
                    )
                    .order_by(score_expr.desc())
                    .limit(limit)
                )
            else:
                # Fallback to vector-only if query has no valid FTS tokens
                score_expr = (
                    1 - CodeChunk.embedding.cosine_distance(embedding)
                ).label("score")
                stmt = (
                    select(CodeChunk, score_expr)
                    .where(CodeChunk.embedding.isnot(None))
                    .order_by(score_expr.desc())
                    .limit(limit)
                )

            if source_id:
                stmt = stmt.where(CodeChunk.source_id == source_id)

            result = await session.execute(stmt)
            rows = result.all()
            return [
                row.CodeChunk.to_dict() | {"score": row.score}
                for row in rows
            ]
