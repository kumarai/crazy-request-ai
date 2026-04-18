from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import CodeChunk, Source

logger = logging.getLogger("[db]")


class SourcesRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def list_sources(self) -> list[dict[str, Any]]:
        async with self._sf() as session:
            stmt = (
                select(
                    Source.id,
                    Source.name,
                    Source.source_type,
                    Source.config,
                    Source.credential_id,
                    Source.is_active,
                    Source.last_synced_at,
                    Source.created_at,
                    func.count(CodeChunk.id).label("chunk_count"),
                )
                .outerjoin(CodeChunk, CodeChunk.source_id == Source.id)
                .group_by(Source.id)
                .order_by(Source.created_at)
            )
            result = await session.execute(stmt)
            return [row._asdict() for row in result.all()]

    async def get_source(self, source_id: UUID) -> dict[str, Any] | None:
        async with self._sf() as session:
            source = await session.get(Source, source_id)
            if not source:
                return None
            return {
                c.key: getattr(source, c.key)
                for c in Source.__table__.columns
            }

    async def upsert_source(
        self,
        name: str,
        source_type: str,
        config: dict[str, Any],
        credential_id: UUID | None = None,
    ) -> dict[str, Any]:
        async with self._sf() as session:
            values = {
                "name": name,
                "source_type": source_type,
                "config": config,
                "credential_id": credential_id,
            }
            update_set = {
                "source_type": source_type,
                "config": config,
                "credential_id": credential_id,
            }
            stmt = (
                insert(Source)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["name"],
                    set_=update_set,
                )
                .returning(Source)
            )
            result = await session.execute(stmt)
            await session.commit()
            row = result.scalar_one()
            return {c.key: getattr(row, c.key) for c in Source.__table__.columns}

    async def update_source(
        self,
        source_id: UUID,
        *,
        name: str | None = None,
        source_type: str | None = None,
        config: dict | None = None,
        credential_id: UUID | None = ...,  # type: ignore[assignment]
        is_active: bool | None = None,
    ) -> dict[str, Any]:
        async with self._sf() as session:
            source = await session.get(Source, source_id)
            if not source:
                raise ValueError(f"Source {source_id} not found")
            if name is not None:
                source.name = name
            if source_type is not None:
                source.source_type = source_type
            if config is not None:
                source.config = config
            if credential_id is not ...:
                source.credential_id = credential_id
            if is_active is not None:
                source.is_active = is_active
            await session.commit()
            await session.refresh(source)
            return {c.key: getattr(source, c.key) for c in Source.__table__.columns}

    async def delete_source(self, source_id: UUID) -> bool:
        async with self._sf() as session:
            stmt = delete(Source).where(Source.id == source_id)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def update_last_synced(self, source_id: UUID) -> None:
        async with self._sf() as session:
            stmt = (
                update(Source)
                .where(Source.id == source_id)
                .values(last_synced_at=func.now())
            )
            await session.execute(stmt)
            await session.commit()

    async def list_active_sources(self) -> list[dict[str, Any]]:
        async with self._sf() as session:
            stmt = (
                select(Source)
                .where(Source.is_active.is_(True))
                .order_by(Source.created_at)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                {c.key: getattr(r, c.key) for c in Source.__table__.columns}
                for r in rows
            ]
