"""Redis-backed hot session cache for customer-support conversations.

Stores rolling summary, last specialist, and unresolved facts for fast
context assembly. Postgres is always the source of truth; Redis is a
speed cache with write-through semantics.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("[support]")

_PREFIX = "support:session:"
_TTL = 60 * 60  # 1 hour


@dataclass
class SessionState:
    rolling_summary: str | None = None
    last_specialist: str | None = None
    unresolved_facts: list[str] | None = None
    last_tool_facts: dict[str, Any] | None = None


class SessionCache:
    """Thin Redis cache for per-conversation session state."""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def get(self, conversation_id: str) -> SessionState | None:
        try:
            key = _PREFIX + conversation_id
            raw = await self._redis.get(key)
            if raw is None:
                return None
            data = json.loads(raw)
            return SessionState(
                rolling_summary=data.get("rolling_summary"),
                last_specialist=data.get("last_specialist"),
                unresolved_facts=data.get("unresolved_facts"),
                last_tool_facts=data.get("last_tool_facts"),
            )
        except Exception:
            logger.debug("Session cache miss for %s", conversation_id)
            return None

    async def set(self, conversation_id: str, state: SessionState) -> None:
        try:
            key = _PREFIX + conversation_id
            data = {
                "rolling_summary": state.rolling_summary,
                "last_specialist": state.last_specialist,
                "unresolved_facts": state.unresolved_facts,
                "last_tool_facts": state.last_tool_facts,
            }
            await self._redis.set(key, json.dumps(data), ex=_TTL)
        except Exception:
            logger.warning(
                "Failed to write session cache for %s", conversation_id
            )

    async def invalidate(self, conversation_id: str) -> None:
        try:
            await self._redis.delete(_PREFIX + conversation_id)
        except Exception:
            pass
