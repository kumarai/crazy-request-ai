from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.rag.cache import HyDECache

if TYPE_CHECKING:
    from app.llm.client import LLMClient

logger = logging.getLogger("[rag]")


class HyDE:
    def __init__(
        self, llm: LLMClient, model: str, redis: Any = None
    ) -> None:
        self._llm = llm
        self._model = model
        self._cache = HyDECache(redis) if redis else None

    async def generate_hypothetical_code(
        self, query: str, language: str = "typescript"
    ) -> str:
        if self._cache:
            cached = await self._cache.get_code(query, language, self._model)
            if cached is not None:
                return cached

        result = await self._llm.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a code generation assistant. Write a short, realistic "
                        f"code snippet in {language} that would answer this query. "
                        "Include realistic function names, types, and patterns. "
                        "Output ONLY code, no explanation."
                    ),
                },
                {"role": "user", "content": query},
            ],
            model=self._model,
            temperature=0.3,
            max_tokens=500,
        )

        if self._cache:
            await self._cache.set_code(query, language, self._model, result)
        return result

    async def generate_search_queries(
        self, query: str, count: int = 4
    ) -> list[str]:
        if self._cache:
            cached = await self._cache.get_queries(query, count, self._model)
            if cached is not None:
                return cached

        text = await self._llm.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Generate {count} alternative search queries for a code search system. "
                        "Each query should approach the topic from a different angle: "
                        "1) Technical implementation detail, "
                        "2) Use case / business domain, "
                        "3) Function/class name guess, "
                        "4) Architecture pattern.\n"
                        "Output one query per line, no numbering, no explanation."
                    ),
                },
                {"role": "user", "content": query},
            ],
            model=self._model,
            temperature=0.5,
            max_tokens=300,
        )
        queries = [q.strip() for q in text.strip().splitlines() if q.strip()][:count]

        if self._cache:
            await self._cache.set_queries(query, count, self._model, queries)
        return queries
