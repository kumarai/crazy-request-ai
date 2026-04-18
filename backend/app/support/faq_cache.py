"""Redis-backed exact-match FAQ cache for support answers.

Goal: skip router + retrieval + specialist + validation for the 20-30%
of support traffic that's a repeat of the same generic question.
A full turn costs ~6 LLM calls; a cache hit costs zero.

What's safe to cache:
  - Generic knowledge-base answers (general specialist, capabilities).
  - Outage status keyed by zip code (already cheap but further cut by
    skipping the router + retrieval).

What's NOT safe:
  - Anything tool-derived that varies per customer (balances, orders,
    payment methods, appointments). These tool outputs change between
    turns even for the same wording, so we skip caching whenever the
    specialist called any MCP tool other than `outage_area_status` or
    `outage_scheduled_maintenance`.
  - Write-proposals or interactive actions — these carry short-TTL
    action ids keyed to the live conversation.
  - Anything mentioning the customer_id in the reply.

Keys
  ``support:faq:{scope}:{sha256(normalized_query + scope_hint)}``
  scope ∈ ``general`` | ``outage:<zip>``.

TTL: 1 hour. The KB rarely changes within the hour; outage state
does, but 1h is acceptable for a "generic question" cache.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("[support]")

FAQ_TTL_SECONDS = 60 * 60  # 1 hour

# Only these specialists are cacheable. Everything else sees live data.
_CACHEABLE_SPECIALISTS = frozenset({"general", "outage"})

# Tools that don't produce customer-specific output — keeping one in
# the output is fine for cache reuse. Anything else = don't cache.
_TOOL_ALLOWLIST = frozenset({
    "outage_area_status",
    "outage_area_status_zip",
    "outage_scheduled_maintenance",
})


@dataclass
class CachedAnswer:
    """Serialised cache payload — replayed on cache hit."""
    specialist: str
    reply: str
    followups: list[dict]   # FollowupQuestion dicts
    actions: list[dict]     # ActionLink dicts
    tools_called: list[str]


def normalize_query(text: str) -> str:
    """Collapse whitespace + lowercase + strip light punctuation.

    Keeps the normalization cheap + predictable; we're not trying to
    fuzzy-match, just hit the same cache entry for minor rewordings
    ("What's my balance?" vs "what's my balance").
    """
    t = text.lower().strip()
    t = re.sub(r"[\s\u00a0]+", " ", t)
    t = re.sub(r"[\u2018\u2019']", "'", t)
    t = re.sub(r"[\u201c\u201d\"]", '"', t)
    t = t.rstrip(".?!,;:")
    return t


def _cache_key(scope: str, normalized_query: str) -> str:
    h = hashlib.sha256()
    h.update(scope.encode("utf-8"))
    h.update(b"|")
    h.update(normalized_query.encode("utf-8"))
    digest = h.hexdigest()[:32]
    return f"support:faq:{scope}:{digest}"


def is_cacheable_specialist(specialist: str) -> bool:
    return specialist in _CACHEABLE_SPECIALISTS


def is_cacheable_tool_set(tool_names: list[str]) -> bool:
    """Every tool called must be in the allowlist for the turn to be cacheable."""
    return all(t in _TOOL_ALLOWLIST for t in tool_names)


def reply_looks_customer_specific(
    reply: str, customer_id: str | None
) -> bool:
    """Cheap guard against caching a reply that leaked the customer id."""
    if not customer_id:
        return False
    return customer_id in reply


def build_scope(specialist: str, *, zip_code: str | None = None) -> str:
    """Scope string — distinguishes outage cache entries per zip."""
    if specialist == "outage" and zip_code:
        return f"outage:{zip_code}"
    return specialist


class FaqCache:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def get(
        self, specialist: str, query: str, *, zip_code: str | None = None
    ) -> CachedAnswer | None:
        if not is_cacheable_specialist(specialist):
            return None
        scope = build_scope(specialist, zip_code=zip_code)
        key = _cache_key(scope, normalize_query(query))
        try:
            raw = await self._redis.get(key)
        except Exception as e:  # noqa: BLE001
            logger.warning("FAQ cache get failed: %s", e)
            return None
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            return CachedAnswer(**data)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("FAQ cache payload unreadable: %s", e)
            return None

    async def put(
        self,
        specialist: str,
        query: str,
        answer: CachedAnswer,
        *,
        zip_code: str | None = None,
    ) -> None:
        if not is_cacheable_specialist(specialist):
            return
        scope = build_scope(specialist, zip_code=zip_code)
        key = _cache_key(scope, normalize_query(query))
        payload = json.dumps(
            {
                "specialist": answer.specialist,
                "reply": answer.reply,
                "followups": answer.followups,
                "actions": answer.actions,
                "tools_called": answer.tools_called,
            },
            separators=(",", ":"),
        )
        try:
            await self._redis.set(key, payload, ex=FAQ_TTL_SECONDS)
        except Exception as e:  # noqa: BLE001
            logger.warning("FAQ cache put failed: %s", e)
