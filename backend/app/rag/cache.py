"""Redis-backed caches for query embeddings and HyDE outputs.

Avoids re-computing embeddings and HyDE LLM calls for repeated or recent queries.
All cache misses fall through transparently — the cache is optional.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger("[rag]")

_EMBED_PREFIX = "emb:"
_EMBED_TTL = 60 * 60 * 24  # 24 hours

_HYDE_CODE_PREFIX = "hyde:code:"
_HYDE_Q_PREFIX = "hyde:q:"
_HYDE_TTL = 60 * 60 * 24  # 24 hours


def _hash_key(text: str, model: str) -> str:
    """Deterministic cache key from text + model."""
    raw = f"{model}:{text}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class EmbeddingCache:
    """Thin cache layer in front of the LLM embedding call."""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def get(self, text: str, model: str) -> list[float] | None:
        try:
            key = _EMBED_PREFIX + _hash_key(text, model)
            raw = await self._redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception:
            return None

    async def set(self, text: str, model: str, embedding: list[float]) -> None:
        try:
            key = _EMBED_PREFIX + _hash_key(text, model)
            await self._redis.set(key, json.dumps(embedding), ex=_EMBED_TTL)
        except Exception:
            pass  # cache write failures are non-critical

    async def get_many(
        self, texts: list[str], model: str
    ) -> list[list[float] | None]:
        """Batch lookup. Returns a list parallel to *texts* with None for misses."""
        if not texts:
            return []
        try:
            keys = [_EMBED_PREFIX + _hash_key(t, model) for t in texts]
            raw_values = await self._redis.mget(*keys)
            return [
                json.loads(v) if v is not None else None for v in raw_values
            ]
        except Exception:
            return [None] * len(texts)

    async def set_many(
        self, texts: list[str], model: str, embeddings: list[list[float]]
    ) -> None:
        try:
            pipe = self._redis.pipeline()
            for text, emb in zip(texts, embeddings):
                key = _EMBED_PREFIX + _hash_key(text, model)
                pipe.set(key, json.dumps(emb), ex=_EMBED_TTL)
            await pipe.execute()
        except Exception:
            pass


class HyDECache:
    """Caches HyDE LLM outputs (hypothetical code + expanded queries)."""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def get_code(
        self, query: str, language: str, model: str
    ) -> str | None:
        try:
            key = _HYDE_CODE_PREFIX + _hash_key(f"{language}:{query}", model)
            raw = await self._redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception:
            return None

    async def set_code(
        self, query: str, language: str, model: str, code: str
    ) -> None:
        try:
            key = _HYDE_CODE_PREFIX + _hash_key(f"{language}:{query}", model)
            await self._redis.set(key, json.dumps(code), ex=_HYDE_TTL)
        except Exception:
            pass

    async def get_queries(
        self, query: str, count: int, model: str
    ) -> list[str] | None:
        try:
            key = _HYDE_Q_PREFIX + _hash_key(f"{count}:{query}", model)
            raw = await self._redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception:
            return None

    async def set_queries(
        self, query: str, count: int, model: str, queries: list[str]
    ) -> None:
        try:
            key = _HYDE_Q_PREFIX + _hash_key(f"{count}:{query}", model)
            await self._redis.set(key, json.dumps(queries), ex=_HYDE_TTL)
        except Exception:
            pass
