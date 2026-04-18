"""Devchat (developer-RAG) endpoint — thin wrapper around ``DevChatOrchestrator``.

Mirrors ``app/api/routes/support.py``: the route resolves dependencies,
constructs the orchestrator, and hands streaming off via SSE.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.db.repositories.chunks import ChunksRepository
from app.db.repositories.sources import SourcesRepository
from app.devchat.orchestrator import DevChatOrchestrator

logger = logging.getLogger("[api]")

router = APIRouter()


class DevChatQueryRequest(BaseModel):
    query: str
    language: str = "typescript"
    source_ids: list[str] | None = None
    top_k: int = 8
    include_wiki: bool = True
    include_code: bool = True
    provider: str | None = None  # optional per-query provider override


@router.post("/devchat/query")
async def devchat_query_endpoint(body: DevChatQueryRequest, request: Request):
    sf = request.app.state.session_factory
    orchestrator = DevChatOrchestrator(
        chunks_repo=ChunksRepository(sf),
        sources_repo=SourcesRepository(sf),
        llm_client=request.app.state.llm_client,
        redis=request.app.state.redis,
    )
    return EventSourceResponse(
        orchestrator.stream(
            query=body.query,
            language=body.language,
            source_ids=body.source_ids,
            top_k=body.top_k,
            include_wiki=body.include_wiki,
            include_code=body.include_code,
            provider=body.provider,
        ),
        media_type="text/event-stream",
    )
