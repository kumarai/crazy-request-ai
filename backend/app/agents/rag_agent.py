from __future__ import annotations

import logging
from uuid import UUID

from pydantic_ai import Agent, RunContext

from app.agents.models import RAGResult, RetrievedChunk
from app.rag.retriever import Retriever

logger = logging.getLogger("[agent]")

# Default model; overridden at .run() time via llm_client.agent_model("summary")
rag_agent = Agent(
    model="openai:gpt-4o-mini",
    output_type=RAGResult,
    system_prompt="""\
You are a retrieval agent for an internal developer knowledge platform.
Retrieve the most relevant indexed content for developer queries.
You work ONLY with indexed sources — do not use general programming knowledge.
Use all available tools. Prefer breadth (multiple search strategies).
""",
)


class RAGAgentDeps:
    def __init__(self, retriever: Retriever) -> None:
        self.retriever = retriever


@rag_agent.tool
async def expand_query(
    ctx: RunContext[RAGAgentDeps], query: str
) -> list[str]:
    """Generate expanded search queries using HyDE."""
    return await ctx.deps.retriever._hyde.generate_search_queries(query)


@rag_agent.tool
async def vector_search(
    ctx: RunContext[RAGAgentDeps],
    texts: list[str],
    source_ids: list[str] | None = None,
    use_summary: bool = False,
) -> list[dict]:
    """Search chunks by vector similarity."""
    results = []
    parsed_ids = [UUID(s) for s in source_ids] if source_ids else None
    for text in texts:
        embedding = await ctx.deps.retriever._embedder.embed_text(text)
        hits = await ctx.deps.retriever._chunks.vector_search(
            embedding, limit=40, source_ids=parsed_ids, use_summary=use_summary
        )
        results.extend(hits)
    return results


@rag_agent.tool
async def bm25_search(
    ctx: RunContext[RAGAgentDeps],
    queries: list[str],
    source_ids: list[str] | None = None,
) -> list[dict]:
    """Search chunks using BM25 full-text search."""
    results = []
    parsed_ids = [UUID(s) for s in source_ids] if source_ids else None
    for q in queries:
        hits = await ctx.deps.retriever._chunks.bm25_search(
            q, limit=40, source_ids=parsed_ids
        )
        results.extend(hits)
    return results


@rag_agent.tool
async def symbol_search(
    ctx: RunContext[RAGAgentDeps], symbol: str
) -> list[dict]:
    """Search for a specific symbol (class, function, type) by name."""
    return await ctx.deps.retriever._chunks.symbol_search(symbol)


@rag_agent.tool
async def domain_tag_search(
    ctx: RunContext[RAGAgentDeps], tags: list[str]
) -> list[dict]:
    """Search chunks by domain tags."""
    return await ctx.deps.retriever._chunks.domain_tag_search(tags)


@rag_agent.tool
async def graph_expand(
    ctx: RunContext[RAGAgentDeps], file_paths: list[str]
) -> list[dict]:
    """Get related chunks via dependency graph."""
    return await ctx.deps.retriever._chunks.get_graph_neighbors(file_paths)


@rag_agent.tool
async def fuse_and_rerank(
    ctx: RunContext[RAGAgentDeps],
    result_lists: list[list[dict]],
    query: str,
) -> list[dict]:
    """Fuse multiple result lists with RRF and rerank."""
    from app.rag.fusion import reciprocal_rank_fusion

    fused = reciprocal_rank_fusion(result_lists, k=60, top_n=20)
    return await ctx.deps.retriever._reranker.rerank(query, fused)
