from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.config import settings
from app.db.repositories.checkpoints import CheckpointsRepository
from app.db.repositories.chunks import ChunksRepository
from app.db.repositories.sources import SourcesRepository

logger = logging.getLogger("[api]")

router = APIRouter()

_VALID_SOURCE_TYPES = {"git_repo", "gitlab_wiki", "support", "api_docs", "json", "api"}


def _require_admin(request: Request) -> None:
    if not getattr(request.state, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin API key required")


class CreateSourceRequest(BaseModel):
    name: str
    source_type: str
    credential_id: str | None = None
    config: dict = {}


class UpdateSourceRequest(BaseModel):
    name: str | None = None
    source_type: str | None = None
    credential_id: str | None = None
    config: dict | None = None
    is_active: bool | None = None


def _sanitize_config(config: dict, source_type: str) -> dict:
    """Strip fields that callers must not control via config dict."""
    sanitized = {k: v for k, v in config.items() if k != "credential_id"}

    # Validate generic source paths against allowlist
    if source_type in ("support", "api_docs", "json") and "path" in sanitized:
        raw_path = str(sanitized["path"])
        resolved = str(Path(raw_path).resolve())
        allowed = any(
            resolved == str(Path(root).resolve())
            or resolved.startswith(str(Path(root).resolve()) + "/")
            for root in settings.indexing_generic_allowed_roots
        )
        if not allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Path {raw_path!r} is outside allowed roots: {settings.indexing_generic_allowed_roots}",
            )
    return sanitized


async def _validate_credential_id(
    credential_id: str | None, request: Request
) -> UUID | None:
    if not credential_id:
        return None
    from app.db.repositories.credentials import CredentialsRepository

    creds_repo = CredentialsRepository(request.app.state.session_factory)
    cred = await creds_repo.get(UUID(credential_id))
    if not cred:
        raise HTTPException(status_code=400, detail="credential_id not found")
    return UUID(credential_id)


def _format_source(s: dict) -> dict:
    """Normalise a source dict for JSON response."""
    s["id"] = str(s["id"])
    if s.get("credential_id"):
        s["credential_id"] = str(s["credential_id"])
    return s


@router.get("/sources")
async def list_sources(request: Request):
    repo = SourcesRepository(request.app.state.session_factory)
    sources = await repo.list_sources()
    return [_format_source(s) for s in sources]


@router.post("/sources", status_code=201)
async def create_source(body: CreateSourceRequest, request: Request):
    _require_admin(request)
    if body.source_type not in _VALID_SOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source_type. Must be one of: {sorted(_VALID_SOURCE_TYPES)}",
        )

    sanitized = _sanitize_config(body.config, body.source_type)
    cred_id = await _validate_credential_id(body.credential_id, request)

    repo = SourcesRepository(request.app.state.session_factory)
    source = await repo.upsert_source(
        name=body.name,
        source_type=body.source_type,
        config=sanitized,
        credential_id=cred_id,
    )
    return _format_source(dict(source))


@router.get("/sources/{source_id}")
async def get_source(source_id: UUID, request: Request):
    repo = SourcesRepository(request.app.state.session_factory)
    source = await repo.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return _format_source(dict(source))


@router.put("/sources/{source_id}")
async def update_source(source_id: UUID, body: UpdateSourceRequest, request: Request):
    _require_admin(request)
    repo = SourcesRepository(request.app.state.session_factory)
    existing = await repo.get_source(source_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Source not found")

    source_type = body.source_type or existing["source_type"]
    if body.source_type and body.source_type not in _VALID_SOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source_type. Must be one of: {sorted(_VALID_SOURCE_TYPES)}",
        )

    config = existing.get("config", {})
    if body.config is not None:
        config = _sanitize_config(body.config, source_type)

    cred_id = existing.get("credential_id")
    if body.credential_id is not None:
        cred_id = await _validate_credential_id(body.credential_id, request)

    updated = await repo.update_source(
        source_id,
        name=body.name,
        source_type=body.source_type,
        config=config if body.config is not None else None,
        credential_id=cred_id,
        is_active=body.is_active,
    )
    return _format_source(dict(updated))


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: UUID, request: Request):
    _require_admin(request)
    repo = SourcesRepository(request.app.state.session_factory)
    deleted = await repo.delete_source(source_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Source not found")


@router.post("/sources/{source_id}/sync")
async def sync_source(
    source_id: UUID,
    request: Request,
    mode: str = Query(default="incremental"),
):
    _require_admin(request)
    repo = SourcesRepository(request.app.state.session_factory)
    source = await repo.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    source_type = source["source_type"]

    # Create job FIRST, pass job_id to task so worker uses the same row
    checkpoints_repo = CheckpointsRepository(request.app.state.session_factory)
    job = await checkpoints_repo.create_job(
        source_id,
        triggered_by="api",
    )
    job_id = str(job["id"])

    if source_type == "git_repo":
        from app.tasks.index_tasks import index_repo

        task = index_repo.delay(str(source_id), mode=mode, job_id=job_id)
    elif source_type == "gitlab_wiki":
        from app.tasks.index_tasks import index_wiki

        task = index_wiki.delay(str(source_id), mode=mode, job_id=job_id)
    elif source_type == "api":
        from app.tasks.index_tasks import index_api

        task = index_api.delay(str(source_id), mode=mode, job_id=job_id)
    else:
        from app.tasks.index_tasks import index_source

        task = index_source.delay(str(source_id), mode=mode, job_id=job_id)

    # Update job with celery task ID
    await checkpoints_repo.update_job_celery_id(UUID(job_id), task.id)

    return {"job_id": job_id, "status": "pending"}


@router.get("/sources/{source_id}/status")
async def source_status(source_id: UUID, request: Request):
    checkpoints_repo = CheckpointsRepository(request.app.state.session_factory)
    job = await checkpoints_repo.get_latest_job(source_id)
    if not job:
        raise HTTPException(status_code=404, detail="No jobs found")
    job["id"] = str(job["id"])
    job["source_id"] = str(job["source_id"])
    return job


# ────────────────────────────────────────────────────────────────────
# Debug: list chunks indexed for a source (shows embed_input)
# ────────────────────────────────────────────────────────────────────
_CHUNK_RESPONSE_FIELDS = (
    "id",
    "source_id",
    "source_type",
    "file_path",
    "language",
    "chunk_type",
    "name",
    "qualified_name",
    "start_line",
    "end_line",
    "summary",
    "purpose",
    "signature",
    "reuse_signal",
    "side_effects",
    "example_call",
    "domain_tags",
    "complexity",
    "embed_input",
    "content",
    "indexed_at",
)


def _format_chunk(c: dict) -> dict:
    out = {k: c.get(k) for k in _CHUNK_RESPONSE_FIELDS}
    if out.get("id") is not None:
        out["id"] = str(out["id"])
    if out.get("source_id") is not None:
        out["source_id"] = str(out["source_id"])
    # domain_tags is ARRAY(Text); None → []
    out["domain_tags"] = out.get("domain_tags") or []
    return out


@router.get("/sources/{source_id}/chunks")
async def list_source_chunks(
    source_id: UUID,
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    q: str | None = Query(default=None),
):
    """List indexed chunks for a source, newest-first.

    Used by the Sources page chunk-preview drawer to show exactly what the
    embedder produced. Filter by substring match on name/qualified_name/file_path.
    """
    chunks_repo = ChunksRepository(request.app.state.session_factory)
    chunks = await chunks_repo.list_chunks_for_source(
        source_id, limit=limit, q=q
    )
    return [_format_chunk(c) for c in chunks]


# ────────────────────────────────────────────────────────────────────
# Re-embed: rebuild embed_input + embedding without re-parsing/summarizing
# ────────────────────────────────────────────────────────────────────
@router.post("/sources/{source_id}/reembed")
async def reembed_source(source_id: UUID, request: Request):
    _require_admin(request)
    repo = SourcesRepository(request.app.state.session_factory)
    source = await repo.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    checkpoints_repo = CheckpointsRepository(request.app.state.session_factory)
    job = await checkpoints_repo.create_job(source_id, triggered_by="api")
    job_id = str(job["id"])

    from app.tasks.index_tasks import reembed_source as reembed_task

    task = reembed_task.delay(str(source_id), job_id=job_id)
    await checkpoints_repo.update_job_celery_id(UUID(job_id), task.id)

    return {"job_id": job_id, "status": "pending"}
