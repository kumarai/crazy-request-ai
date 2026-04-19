"""Dev-only debug endpoint: parallel multi-agent orchestrator + trace.

``POST /debug/query`` runs the customer query through
:class:`DebugOrchestrator` and returns the full execution trace — the
decomposer output, each specialist branch (with retrieved sources and
tool calls), and the synthesized final answer. The frontend ``/debug``
page renders that trace as a DAG with an inspector.

This endpoint does NOT persist anything: no conversation row, no
messages, no FAQ cache writes. It's a read-only inspection surface.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.db.repositories.chunks import ChunksRepository
from app.db.repositories.sources import SourcesRepository
from app.support.customer_context import resolve_customer_context
from app.support.debug_orchestrator import DebugOrchestrator

logger = logging.getLogger("[api]")

router = APIRouter()


class DebugQueryRequest(BaseModel):
    query: str
    customer_id: str | None = None
    source_ids: list[str] | None = None
    provider: str | None = None


def _debug_endpoints_enabled() -> bool:
    """Gate the debug endpoint off in production by default.

    Set ``APP_ENV=production`` to disable. Any other value (or missing
    env var) keeps the endpoint available.
    """
    return os.environ.get("APP_ENV", "dev").lower() != "production"


@router.post("/debug/query")
async def debug_query_endpoint(
    body: DebugQueryRequest,
    request: Request,
):
    if not _debug_endpoints_enabled():
        raise HTTPException(status_code=404, detail="Not Found")

    if not body.query or not body.query.strip():
        raise HTTPException(status_code=400, detail="query is required")

    sf = request.app.state.session_factory
    chunks_repo = ChunksRepository(sf)
    sources_repo = SourcesRepository(sf)

    # If no customer_id is supplied, default to a fresh guest so the
    # auth-gated branches render as "skipped" rather than erroring.
    if body.customer_id:
        customer = await resolve_customer_context(body.customer_id, is_guest=False)
    else:
        customer = await resolve_customer_context("debug-guest", is_guest=True)

    orchestrator = DebugOrchestrator(
        chunks_repo=chunks_repo,
        sources_repo=sources_repo,
        llm_client=request.app.state.llm_client,
        redis=request.app.state.redis,
    )

    try:
        trace = await orchestrator.run(
            query=body.query,
            customer=customer,
            source_ids=body.source_ids,
            provider=body.provider,
        )
    except Exception as e:
        logger.error("Debug orchestrator failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"debug_run_failed: {e}") from e

    return trace.model_dump()
