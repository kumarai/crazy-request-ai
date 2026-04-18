from __future__ import annotations

import json
import logging
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.llm.client import LLMClient

logger = logging.getLogger("[rag]")

# How aggressively to penalise repeated files.
# Each additional chunk from the same file gets multiplied by
# (1 - PENALTY * occurrence_index), clamped to a floor of 0.3.
_FILE_DIVERSITY_PENALTY = 0.15
_FILE_DIVERSITY_FLOOR = 0.3


class Reranker:
    def __init__(self, llm: LLMClient, model: str) -> None:
        self._llm = llm
        self._model = model

    async def rerank(
        self,
        query: str,
        chunks: list[dict],
        top_k: int = 8,
    ) -> list[dict]:
        if not chunks:
            return []

        formatted = []
        for i, c in enumerate(chunks):
            source_type = c.get("source_type", "code")
            qname = c.get("qualified_name", c.get("name", ""))
            purpose = c.get("purpose", "")
            reuse = c.get("reuse_signal", "")
            raw_content = c.get("content_with_context") or c.get("content") or ""
            snippet = raw_content[:600].replace("\n", " ")
            formatted.append(
                f"{i}: ({source_type}) {qname} | {purpose} | {reuse} | {snippet}"
            )

        doc_list = "\n".join(formatted)

        raw = await self._llm.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a relevance scorer. Score each document 0-10 for "
                        "relevance to the query. Return ONLY a JSON array of integers, "
                        "one score per document, in order. Example: [8, 3, 9, 1, 5]\n"
                        "No explanation, no markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Query: {query}\n\nDocuments:\n{doc_list}",
                },
            ],
            model=self._model,
            temperature=0,
            max_tokens=200,
        )

        raw = raw.strip()
        try:
            scores = json.loads(raw)
            if not isinstance(scores, list):
                raise ValueError("Expected JSON array")
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Rerank parse error: %s — raw: %s", e, raw[:200])
            return self._apply_diversity(chunks, top_k)

        if len(scores) != len(chunks):
            logger.warning(
                "Rerank score count mismatch: got %d scores for %d chunks — using RRF only",
                len(scores),
                len(chunks),
            )
            return self._apply_diversity(chunks, top_k)

        # Combine LLM score with existing RRF score
        for i, chunk in enumerate(chunks):
            llm_score = min(max(scores[i], 0), 10) / 10.0
            rrf_score = chunk.get("rrf_score", chunk.get("score", 0.0))
            chunk["final_score"] = llm_score * 0.7 + rrf_score * 0.3
            chunk["score"] = chunk["final_score"]

        chunks.sort(key=lambda c: c.get("final_score", 0), reverse=True)
        return self._apply_diversity(chunks, top_k)

    @staticmethod
    def _apply_diversity(chunks: list[dict], top_k: int) -> list[dict]:
        """Apply a file-level diversity penalty so results span more files.

        Chunks are already sorted by score.  For each file, the 1st chunk
        keeps its score, the 2nd is penalised by ``_FILE_DIVERSITY_PENALTY``,
        the 3rd by ``2 * _FILE_DIVERSITY_PENALTY``, etc.  Then re-sort and
        take top_k.
        """
        file_count: Counter[str] = Counter()
        for chunk in chunks:
            fp = chunk.get("file_path", "")
            occurrence = file_count[fp]
            file_count[fp] += 1

            if occurrence > 0:
                penalty = max(
                    _FILE_DIVERSITY_FLOOR,
                    1.0 - _FILE_DIVERSITY_PENALTY * occurrence,
                )
                chunk["score"] = chunk.get("score", 0) * penalty

        chunks.sort(key=lambda c: c.get("score", 0), reverse=True)
        return chunks[:top_k]
