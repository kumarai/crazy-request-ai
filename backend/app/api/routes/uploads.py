"""Presigned upload/download URLs for file-based sources.

Flow:
1. Frontend calls POST /sources/{id}/upload-url with filename + content_type
2. Backend returns a presigned PUT URL + the object key
3. Frontend uploads directly to MinIO/GCS (no backend proxy)
4. Frontend calls POST /sources/{id}/sync to index the uploaded files
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.db.repositories.sources import SourcesRepository
from app.storage.client import StorageClient

logger = logging.getLogger("[api]")

router = APIRouter()

_FILE_SOURCE_TYPES = {"support", "api_docs", "json"}


def _require_admin(request: Request) -> None:
    if not getattr(request.state, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin API key required")


def _get_storage(request: Request) -> StorageClient:
    client = getattr(request.app.state, "storage_client", None)
    if not client:
        raise HTTPException(status_code=503, detail="Object storage not configured")
    return client


class UploadUrlRequest(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"


class UploadUrlResponse(BaseModel):
    upload_url: str
    object_key: str


@router.post("/sources/{source_id}/upload-url")
async def get_upload_url(
    source_id: UUID, body: UploadUrlRequest, request: Request
) -> UploadUrlResponse:
    _require_admin(request)
    repo = SourcesRepository(request.app.state.session_factory)
    source = await repo.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    if source["source_type"] not in _FILE_SOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Upload only supported for source types: {sorted(_FILE_SOURCE_TYPES)}",
        )

    # Sanitize filename
    safe_name = body.filename.replace("..", "").lstrip("/")
    object_key = f"sources/{source_id}/{safe_name}"

    storage = _get_storage(request)
    upload_url = await storage.presigned_upload_url(
        object_key, content_type=body.content_type
    )

    # Store the object storage prefix in source config so index_generic knows where to look
    config = source.get("config", {})
    if config.get("storage_prefix") != f"sources/{source_id}/":
        config["storage_prefix"] = f"sources/{source_id}/"
        await repo.update_source(source_id, config=config)

    return UploadUrlResponse(upload_url=upload_url, object_key=object_key)


@router.get("/sources/{source_id}/files")
async def list_source_files(source_id: UUID, request: Request):
    repo = SourcesRepository(request.app.state.session_factory)
    source = await repo.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    storage = _get_storage(request)
    prefix = f"sources/{source_id}/"
    objects = await storage.list_objects(prefix)

    # Strip prefix for cleaner display
    for obj in objects:
        obj["name"] = obj["key"].removeprefix(prefix)
    return objects


@router.delete("/sources/{source_id}/files/{filename:path}")
async def delete_source_file(
    source_id: UUID, filename: str, request: Request
):
    _require_admin(request)
    repo = SourcesRepository(request.app.state.session_factory)
    source = await repo.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    storage = _get_storage(request)
    object_key = f"sources/{source_id}/{filename}"

    if not await storage.object_exists(object_key):
        raise HTTPException(status_code=404, detail="File not found")

    await storage.delete_object(object_key)
    return {"deleted": object_key}
