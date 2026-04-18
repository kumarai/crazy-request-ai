from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import tiktoken

from app.indexing.parsers.base import ParsedChunk

if TYPE_CHECKING:
    from app.llm.client import LLMClient

logger = logging.getLogger("[indexing]")

_BATCH_SIZE = 32
_MAX_TOKENS = 8191  # text-embedding-3-small limit


class Embedder:
    def __init__(self, llm: LLMClient, model: str = "text-embedding-3-small") -> None:
        self._llm = llm
        self._model = model
        try:
            self._encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            self._encoding = tiktoken.get_encoding("cl100k_base")

    async def embed_chunks(self, chunks: list[ParsedChunk]) -> list[ParsedChunk]:
        for i in range(0, len(chunks), _BATCH_SIZE):
            batch = chunks[i : i + _BATCH_SIZE]

            content_texts = [self._build_content_text(c) for c in batch]
            summary_texts = [self._build_summary_text(c) for c in batch]

            content_truncated = [self._truncate_to_tokens(t) for t in content_texts]
            summary_truncated = [self._truncate_to_tokens(t) for t in summary_texts]

            content_embeds = await self._llm.embed(content_truncated, self._model)
            summary_embeds = await self._llm.embed(summary_truncated, self._model)

            for j, chunk in enumerate(batch):
                chunk.embedding = content_embeds[j]
                chunk.summary_embedding = summary_embeds[j]

            logger.info(
                "Embedded batch %d-%d of %d chunks",
                i,
                min(i + _BATCH_SIZE, len(chunks)),
                len(chunks),
            )

        return chunks

    async def embed_text(self, text: str) -> list[float]:
        result = await self._llm.embed(
            [self._truncate_to_tokens(text)], self._model
        )
        return result[0]

    def _truncate_to_tokens(self, text: str) -> str:
        tokens = self._encoding.encode(text)
        if len(tokens) <= _MAX_TOKENS:
            return text
        return self._encoding.decode(tokens[:_MAX_TOKENS])

    def _build_content_text(self, chunk: ParsedChunk) -> str:
        parts = [chunk.content_with_context or chunk.content]
        if chunk.purpose:
            parts.append(f"\nPURPOSE: {chunk.purpose}")
        if chunk.summary:
            parts.append(f"SUMMARY: {chunk.summary}")
        if chunk.reuse_signal:
            parts.append(f"USE WHEN: {chunk.reuse_signal}")
        if chunk.domain_tags:
            parts.append(f"DOMAIN: {' '.join(chunk.domain_tags)}")
        return "\n".join(parts)

    def _build_summary_text(self, chunk: ParsedChunk) -> str:
        parts = []
        if chunk.purpose:
            parts.append(chunk.purpose)
        if chunk.summary:
            parts.append(chunk.summary)
        if chunk.reuse_signal:
            parts.append(chunk.reuse_signal)
        if chunk.domain_tags:
            parts.append(" ".join(chunk.domain_tags))
        return "\n".join(parts) if parts else chunk.content[:500]
