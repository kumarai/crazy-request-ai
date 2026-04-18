"""Redis-backed registry for server-issued interactive actions.

Each ``InteractiveAction`` the orchestrator emits is written here with
a 10-minute TTL. Clicking the button POSTs the ``action_id``; the
endpoint looks it up, re-checks customer authz, and dispatches to the
right MCP write tool. One-shot: the entry is deleted on success so
double-clicks can't replay the action outside the idempotency window.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("[support]")

ACTION_TTL_SECONDS = 10 * 60  # 10 minutes


@dataclass
class PendingAction:
    action_id: str
    kind: str                # "pay" | "place_order" | "book_appointment" | "enroll_autopay" | ...
    customer_id: str         # must match the caller on click
    conversation_id: str     # used to derive idempotency
    payload: dict[str, Any]  # tool-specific args (e.g. amount, sku_ids, slot_id)
    expires_at: str

    @classmethod
    def new(
        cls,
        kind: str,
        customer_id: str,
        conversation_id: str,
        payload: dict[str, Any],
    ) -> "PendingAction":
        expires = datetime.now(timezone.utc) + timedelta(seconds=ACTION_TTL_SECONDS)
        return cls(
            action_id=uuid.uuid4().hex,
            kind=kind,
            customer_id=customer_id,
            conversation_id=conversation_id,
            payload=payload,
            expires_at=expires.isoformat(),
        )


def _key(action_id: str) -> str:
    return f"support:action:{action_id}"


class ActionRegistry:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def create(
        self,
        kind: str,
        customer_id: str,
        conversation_id: str,
        payload: dict[str, Any],
    ) -> PendingAction:
        action = PendingAction.new(kind, customer_id, conversation_id, payload)
        await self._redis.set(
            _key(action.action_id),
            json.dumps(asdict(action)),
            ex=ACTION_TTL_SECONDS,
        )
        return action

    async def claim(self, action_id: str) -> PendingAction | None:
        """Look up + delete in one round-trip.

        Uses ``GETDEL`` (Redis 6.2+) for the single-shot guarantee. A
        ``None`` return means: action doesn't exist, expired, or was
        already claimed by another request.
        """
        key = _key(action_id)
        try:
            raw = await self._redis.getdel(key)
        except AttributeError:
            # Pre-6.2 Redis — fall back to GET then DEL. A concurrent
            # double-click can observe the action twice in this path;
            # the MCP idempotency key still prevents double-mutation.
            raw = await self._redis.get(key)
            if raw is not None:
                await self._redis.delete(key)
        if raw is None:
            return None
        data = json.loads(raw)
        return PendingAction(**data)
