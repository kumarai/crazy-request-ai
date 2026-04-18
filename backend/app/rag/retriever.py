from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING
from uuid import UUID

from app.agents.models import RetrievedChunk
from app.config import settings
from app.db.repositories.chunks import ChunksRepository
from app.db.repositories.sources import SourcesRepository
from app.indexing.embedder import Embedder
from app.rag.cache import EmbeddingCache
from app.rag.fusion import deduplicate_by_trigram, reciprocal_rank_fusion
from app.rag.hyde import HyDE
from app.rag.reranker import Reranker

if TYPE_CHECKING:
    from app.llm.client import LLMClient

logger = logging.getLogger("[rag]")

# Match identifier-shaped tokens, not prose. Requires either:
#   - 2+ uppercase letters (PascalCase with >=2 humps, HTTPClient, IOError)
#   - mixed-case with a camelHump (getUserId, isAdmin)
#   - an underscore (snake_case, SCREAMING_SNAKE)
# Plain capitalized words like "The", "User" are excluded intentionally.
_IDENTIFIER_PATTERN = re.compile(
    r"\b(?:"
    r"[A-Z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]+"     # 2+ uppercase: UserService, HTTPClient
    r"|[a-z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*"    # camelCase: getUserId
    r"|[a-zA-Z][a-zA-Z0-9]*_[a-zA-Z0-9_]+"    # snake_case / SCREAMING_SNAKE
    r")\b"
)


class Retriever:
    def __init__(
        self,
        chunks_repo: ChunksRepository,
        llm: LLMClient,
        sources_repo: SourcesRepository | None = None,
        redis=None,
    ) -> None:
        self._chunks = chunks_repo
        self._sources_repo = sources_repo
        self._hyde = HyDE(llm, llm.resolve_model("summary"), redis=redis)
        self._embedder = Embedder(llm, llm.resolve_model("embedding"))
        self._reranker = Reranker(llm, llm.resolve_model("rerank"))
        self._embed_cache = EmbeddingCache(redis) if redis else None

    async def retrieve(
        self,
        query: str,
        source_ids: list[str] | None = None,
        language: str | None = None,
        top_k: int = 8,
        include_wiki: bool = True,
        include_code: bool = True,
        use_hyde: bool = True,
        use_query_expansion: bool = True,
        use_graph: bool = True,
    ) -> tuple[list[RetrievedChunk], float, int, float]:
        """
        ``use_hyde`` controls the hypothetical-code embedding (HyDE in
        the original sense — embed a fake answer alongside the question).
        ``use_query_expansion`` controls the LLM-generated alternative
        queries. They're orthogonal: callers can keep one and skip the
        other. The support orchestrator passes ``False`` for both
        because HyDE's prompts are code-domain and add noise to a
        plain-English KB; the hybrid stack (vector + summary-vector +
        BM25 + reranker) carries recall on its own.
        """
        parsed_source_ids = (
            [UUID(sid) for sid in source_ids] if source_ids else None
        )
        lang = language  # pass through None; HyDE handles the default

        # Build source_name lookup for populating chunk.source_name
        source_name_map = await self._build_source_name_map(parsed_source_ids)

        # Step 1: HyDE + query expansion. Each is independently
        # toggleable; if both are on we run them in parallel.
        hyde_code: str = ""
        expanded_queries: list[str] = []
        if use_hyde and use_query_expansion:
            hyde_code, expanded_queries = await asyncio.gather(
                self._hyde.generate_hypothetical_code(query, lang or "typescript"),
                self._hyde.generate_search_queries(query, settings.rag_expansion_queries),
            )
        elif use_hyde:
            hyde_code = await self._hyde.generate_hypothetical_code(
                query, lang or "typescript"
            )
        elif use_query_expansion:
            expanded_queries = await self._hyde.generate_search_queries(
                query, settings.rag_expansion_queries
            )
        # else: both off — no LLM calls, retrieval runs on the original
        # query only across the hybrid stack.

        all_queries = [query] + expanded_queries

        # Step 2: Embed all query texts (with cache)
        texts_to_embed = list(all_queries)
        if hyde_code:
            texts_to_embed.append(hyde_code)

        embeddings = await self._embed_with_cache(texts_to_embed)

        query_embeddings = embeddings[: len(all_queries)]
        hyde_embedding = embeddings[-1] if hyde_code else None

        # Step 3: Parallel search — ALL strategies scoped by source_ids
        search_tasks: list = []

        # Vector search on embedding column per query
        for emb in query_embeddings:
            search_tasks.append(
                self._chunks.vector_search(
                    emb,
                    limit=settings.rag_vector_candidates,
                    source_ids=parsed_source_ids,
                    use_summary=False,
                )
            )

        # Vector search on summary_embedding column per query
        for emb in query_embeddings:
            search_tasks.append(
                self._chunks.vector_search(
                    emb,
                    limit=settings.rag_vector_candidates,
                    source_ids=parsed_source_ids,
                    use_summary=True,
                )
            )

        # HyDE vector search
        if hyde_embedding:
            search_tasks.append(
                self._chunks.vector_search(
                    hyde_embedding,
                    limit=settings.rag_vector_candidates,
                    source_ids=parsed_source_ids,
                )
            )

        # BM25 per query
        for q in all_queries:
            search_tasks.append(
                self._chunks.bm25_search(
                    q,
                    limit=settings.rag_bm25_candidates,
                    source_ids=parsed_source_ids,
                )
            )

        # Symbol search — scoped by source_ids
        all_symbols = list(set(_IDENTIFIER_PATTERN.findall(query)))
        for sym in all_symbols[:3]:
            search_tasks.append(
                self._chunks.symbol_search(
                    sym, source_ids=parsed_source_ids
                )
            )

        # Domain tag search — scoped by source_ids
        domain_words = [
            w.lower()
            for w in query.split()
            if len(w) > 3 and w.isalpha()
        ]
        if domain_words:
            search_tasks.append(
                self._chunks.domain_tag_search(
                    domain_words[:5], source_ids=parsed_source_ids
                )
            )

        # Execute all searches in parallel
        all_results = await asyncio.gather(*search_tasks)

        total_searched = sum(len(r) for r in all_results)

        # Step 4: RRF fusion
        fused = reciprocal_rank_fusion(
            list(all_results),
            k=60,
            top_n=settings.rag_top_k_after_fusion,
        )

        # Step 5: Graph expansion — scoped by source_ids
        if use_graph and fused:
            top_file_paths = list(
                {c["file_path"] for c in fused[:5] if c.get("source_type") == "code"}
            )
            if top_file_paths:
                neighbors = await self._chunks.get_graph_neighbors(
                    top_file_paths, source_ids=parsed_source_ids
                )
                if neighbors:
                    fused = reciprocal_rank_fusion(
                        [fused, neighbors],
                        k=60,
                        top_n=settings.rag_top_k_after_fusion,
                    )

        # Step 6a: Trigram-based deduplication — remove near-identical chunks
        fused = deduplicate_by_trigram(fused)

        # Step 6b: Filter by include_wiki / include_code
        if not include_wiki:
            fused = [c for c in fused if c.get("source_type") not in ("wiki", "generic")]
        if not include_code:
            fused = [c for c in fused if c.get("source_type") != "code"]

        # Step 7: Cross-encoder rerank
        reranked = await self._reranker.rerank(
            query, fused, top_k=top_k
        )

        # Step 8: Build result with source_name populated
        chunks = [
            self._dict_to_chunk(c, source_name_map) for c in reranked
        ]

        top_score = chunks[0].score if chunks else 0.0
        scope_confidence = min(1.0, top_score / settings.rag_scope_threshold) if top_score > 0 else 0.0

        logger.info(
            "Retrieved %d chunks (scope=%.2f, searched=%d)",
            len(chunks),
            scope_confidence,
            total_searched,
        )

        return chunks, scope_confidence, total_searched, top_score

    async def _embed_with_cache(self, texts: list[str]) -> list[list[float]]:
        """Embed texts with Redis cache. Misses fall through to the embedder."""
        model = self._embedder._model

        if not self._embed_cache:
            return await asyncio.gather(
                *[self._embedder.embed_text(t) for t in texts]
            )

        # Batch lookup
        cached = await self._embed_cache.get_many(texts, model)
        results: list[list[float] | None] = list(cached)
        miss_indices = [i for i, v in enumerate(results) if v is None]

        if miss_indices:
            miss_texts = [texts[i] for i in miss_indices]
            fresh = await asyncio.gather(
                *[self._embedder.embed_text(t) for t in miss_texts]
            )
            for idx, emb in zip(miss_indices, fresh):
                results[idx] = emb
            # Write misses back to cache (fire-and-forget)
            await self._embed_cache.set_many(miss_texts, model, list(fresh))

            logger.info(
                "Embedding cache: %d hits, %d misses",
                len(texts) - len(miss_indices),
                len(miss_indices),
            )

        return results  # type: ignore[return-value]

    async def _build_source_name_map(
        self, source_ids: list[UUID] | None
    ) -> dict[str, str]:
        """Build a mapping of source_id -> source_name for populating chunk metadata."""
        if not self._sources_repo:
            return {}
        try:
            sources = await self._sources_repo.list_active_sources()
            return {str(s["id"]): s["name"] for s in sources}
        except Exception:
            logger.warning("Failed to build source name map", exc_info=True)
            return {}

    def _dict_to_chunk(
        self, d: dict, source_name_map: dict[str, str]
    ) -> RetrievedChunk:
        source_id = str(d.get("source_id", ""))
        return RetrievedChunk(
            id=str(d.get("id", "")),
            source_id=source_id,
            source_type=d.get("source_type", "code"),
            file_path=d.get("file_path", ""),
            repo_root=d.get("repo_root", ""),
            language=d.get("language", ""),
            chunk_type=d.get("chunk_type", ""),
            name=d.get("name", ""),
            qualified_name=d.get("qualified_name", ""),
            content=d.get("content", ""),
            content_with_context=d.get("content_with_context"),
            start_line=d.get("start_line", 0),
            end_line=d.get("end_line", 0),
            summary=d.get("summary"),
            purpose=d.get("purpose"),
            signature=d.get("signature"),
            reuse_signal=d.get("reuse_signal"),
            domain_tags=d.get("domain_tags", []),
            complexity=d.get("complexity"),
            imports_used=d.get("imports_used", []),
            metadata=d.get("metadata", {}),
            score=d.get("score", 0.0),
            source_name=source_name_map.get(source_id, ""),
        )
