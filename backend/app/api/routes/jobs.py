from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from app.db.repositories.checkpoints import CheckpointsRepository

logger = logging.getLogger("[api]")

router = APIRouter()


@router.get("/jobs/{job_id}")
async def get_job(job_id: UUID, request: Request):
    repo = CheckpointsRepository(request.app.state.session_factory)
    job = await repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job["id"] = str(job["id"])
    job["source_id"] = str(job["source_id"])
    return job


@router.get("/jobs")
async def list_jobs(
    request: Request,
    source_id: str | None = Query(default=None),
    limit: int = Query(default=20, le=100),
):
    repo = CheckpointsRepository(request.app.state.session_factory)
    parsed_source_id = UUID(source_id) if source_id else None
    jobs = await repo.list_jobs(source_id=parsed_source_id, limit=limit)
    for j in jobs:
        j["id"] = str(j["id"])
        j["source_id"] = str(j["source_id"])
    return jobs
