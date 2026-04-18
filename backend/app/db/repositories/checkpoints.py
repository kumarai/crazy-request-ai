from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import IndexCheckpoint, IndexJob, WikiCheckpoint

logger = logging.getLogger("[db]")


class CheckpointsRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get_index_checkpoint(
        self, source_id: UUID
    ) -> dict[str, Any] | None:
        async with self._sf() as session:
            cp = await session.get(IndexCheckpoint, source_id)
            if not cp:
                return None
            return {
                c.key: getattr(cp, c.key)
                for c in IndexCheckpoint.__table__.columns
            }

    async def upsert_index_checkpoint(
        self, source_id: UUID, commit_sha: str
    ) -> None:
        async with self._sf() as session:
            stmt = (
                insert(IndexCheckpoint)
                .values(
                    source_id=source_id,
                    last_commit_sha=commit_sha,
                    last_indexed_at=func.now(),
                )
                .on_conflict_do_update(
                    index_elements=["source_id"],
                    set_={
                        "last_commit_sha": commit_sha,
                        "last_indexed_at": func.now(),
                    },
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def get_wiki_checkpoint(
        self, source_id: UUID, page_slug: str
    ) -> dict[str, Any] | None:
        async with self._sf() as session:
            cp = await session.get(WikiCheckpoint, (source_id, page_slug))
            if not cp:
                return None
            return {
                c.key: getattr(cp, c.key)
                for c in WikiCheckpoint.__table__.columns
            }

    async def upsert_wiki_checkpoint(
        self,
        source_id: UUID,
        page_slug: str,
        gitlab_page_id: int | None = None,
        last_updated_at: Any = None,
    ) -> None:
        async with self._sf() as session:
            stmt = (
                insert(WikiCheckpoint)
                .values(
                    source_id=source_id,
                    page_slug=page_slug,
                    gitlab_page_id=gitlab_page_id,
                    last_updated_at=last_updated_at,
                    indexed_at=func.now(),
                )
                .on_conflict_do_update(
                    constraint="wiki_checkpoint_pkey",
                    set_={
                        "gitlab_page_id": gitlab_page_id,
                        "last_updated_at": last_updated_at,
                        "indexed_at": func.now(),
                    },
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def create_job(
        self,
        source_id: UUID,
        celery_task_id: str | None = None,
        triggered_by: str = "schedule",
    ) -> dict[str, Any]:
        async with self._sf() as session:
            job = IndexJob(
                source_id=source_id,
                celery_task_id=celery_task_id,
                status="pending",
                triggered_by=triggered_by,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return job.to_dict()

    async def update_job_celery_id(self, job_id: UUID, celery_task_id: str) -> None:
        async with self._sf() as session:
            stmt = (
                update(IndexJob)
                .where(IndexJob.id == job_id)
                .values(celery_task_id=celery_task_id)
            )
            await session.execute(stmt)
            await session.commit()

    async def update_job_status(
        self,
        job_id: UUID,
        status: str,
        error: str | None = None,
        stats: dict[str, Any] | None = None,
    ) -> None:
        async with self._sf() as session:
            values: dict[str, Any] = {"status": status}
            if status == "running":
                values["started_at"] = func.now()
            elif status in ("done", "failed"):
                values["finished_at"] = func.now()
                values["error"] = error
                values["stats"] = stats or {}

            stmt = update(IndexJob).where(IndexJob.id == job_id).values(**values)
            await session.execute(stmt)
            await session.commit()

    async def get_job(self, job_id: UUID) -> dict[str, Any] | None:
        async with self._sf() as session:
            job = await session.get(IndexJob, job_id)
            if not job:
                return None
            return job.to_dict()

    async def list_jobs(
        self,
        source_id: UUID | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        async with self._sf() as session:
            stmt = select(IndexJob).order_by(
                IndexJob.started_at.desc().nullslast()
            )
            if source_id:
                stmt = stmt.where(IndexJob.source_id == source_id)
            stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return [row.to_dict() for row in result.scalars().all()]

    async def get_latest_job(
        self, source_id: UUID
    ) -> dict[str, Any] | None:
        async with self._sf() as session:
            stmt = (
                select(IndexJob)
                .where(IndexJob.source_id == source_id)
                .order_by(IndexJob.started_at.desc().nullslast())
                .limit(1)
            )
            result = await session.execute(stmt)
            job = result.scalar_one_or_none()
            if not job:
                return None
            return job.to_dict()
