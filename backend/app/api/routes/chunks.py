from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.db.repositories.chunks import ChunksRepository
from app.indexing.embedder import Embedder
from app.llm.client import LLMClient

logger = logging.getLogger("[api]")

router = APIRouter()


@router.get("/chunks/search")
async def search_chunks(
    request: Request,
    q: str = Query(..., min_length=1),
    source_id: str | None = Query(default=None),
    limit: int = Query(default=20, le=100),
):
    chunks_repo = ChunksRepository(request.app.state.session_factory)
    llm: LLMClient = request.app.state.llm_client
    embedder = Embedder(llm, llm.resolve_model("embedding"))

    embedding = await embedder.embed_text(q)

    parsed_source_id = UUID(source_id) if source_id else None

    results = await chunks_repo.search_hybrid(
        query=q,
        embedding=embedding,
        limit=limit,
        source_id=parsed_source_id,
    )

    serialized = []
    for r in results:
        item = {
            "id": str(r["id"]),
            "source_id": str(r["source_id"]),
            "file_path": r["file_path"],
            "name": r["name"],
            "qualified_name": r["qualified_name"],
            "chunk_type": r["chunk_type"],
            "language": r["language"],
            "source_type": r["source_type"],
            "summary": r.get("summary"),
            "purpose": r.get("purpose"),
            "reuse_signal": r.get("reuse_signal"),
            "score": r.get("score", 0),
            "start_line": r.get("start_line", 0),
            "end_line": r.get("end_line", 0),
        }
        serialized.append(item)

    return serialized
