"""History assembler and compaction engine for support conversations.

Builds compact model context from recent turns + rolling summary +
unresolved facts. Compacts when token budget is exceeded.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.config import settings
from app.db.repositories.conversations import ConversationsRepository
from app.llm.client import LLMClient
from app.support.session_cache import SessionCache, SessionState

logger = logging.getLogger("[support]")

# Rough token estimate: ~4 chars per token for English text
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


@dataclass
class HistoryContext:
    recent_turns: list[dict[str, Any]] = field(default_factory=list)
    rolling_summary: str | None = None
    unresolved_facts: list[str] = field(default_factory=list)
    last_specialist: str | None = None


class HistoryAssembler:
    """Loads and compacts conversation history for specialist context."""

    def __init__(
        self,
        conversations_repo: ConversationsRepository,
        session_cache: SessionCache,
        llm_client: LLMClient,
    ) -> None:
        self._repo = conversations_repo
        self._cache = session_cache
        self._llm = llm_client

    async def load_context(self, conversation_id: UUID) -> HistoryContext:
        """Load compact history context for a conversation."""
        # Try Redis first for summary/facts
        cached = await self._cache.get(str(conversation_id))

        if cached:
            summary = cached.rolling_summary
            facts = cached.unresolved_facts or []
            last_specialist = cached.last_specialist
        else:
            # Fallback to Postgres
            conv = await self._repo.get_conversation(conversation_id)
            if conv is None:
                return HistoryContext()
            summary = conv.get("rolling_summary")
            facts = conv.get("unresolved_facts_json") or []
            last_specialist = conv.get("last_specialist")

        # Always load recent messages from Postgres
        recent = await self._repo.get_recent_messages(
            conversation_id, limit=settings.support_verbatim_tail_turns * 2
        )

        return HistoryContext(
            recent_turns=recent,
            rolling_summary=summary,
            unresolved_facts=facts,
            last_specialist=last_specialist,
        )

    async def maybe_compact(
        self, conversation_id: UUID, ctx: HistoryContext
    ) -> HistoryContext:
        """Run compaction if context exceeds token budget threshold."""
        budget = settings.support_history_budget_tokens
        trigger = int(budget * settings.support_compaction_trigger_pct)

        total_tokens = self._estimate_context_tokens(ctx)
        if total_tokens <= trigger:
            return ctx

        logger.info(
            "Compacting history for %s: %d tokens > %d trigger",
            conversation_id,
            total_tokens,
            trigger,
        )

        tail = settings.support_verbatim_tail_turns
        # Keep last N turns verbatim (each turn = user + assistant = 2 messages)
        verbatim_count = tail * 2
        if len(ctx.recent_turns) <= verbatim_count:
            return ctx  # Not enough turns to compact

        older_turns = ctx.recent_turns[:-verbatim_count]
        verbatim_turns = ctx.recent_turns[-verbatim_count:]

        # Build text to summarize: older turns + existing summary
        parts = []
        if ctx.rolling_summary:
            parts.append(f"Previous summary:\n{ctx.rolling_summary}")
        for turn in older_turns:
            role = turn.get("role", "unknown")
            content = turn.get("content", "")
            parts.append(f"{role}: {content}")

        text_to_summarize = "\n\n".join(parts)
        new_summary = await self._generate_summary(text_to_summarize)

        # Write-through: Postgres first, then Redis
        await self._repo.update_conversation(
            conversation_id, rolling_summary=new_summary
        )
        await self._cache.set(
            str(conversation_id),
            SessionState(
                rolling_summary=new_summary,
                last_specialist=ctx.last_specialist,
                unresolved_facts=ctx.unresolved_facts,
            ),
        )

        result = HistoryContext(
            recent_turns=verbatim_turns,
            rolling_summary=new_summary,
            unresolved_facts=ctx.unresolved_facts,
            last_specialist=ctx.last_specialist,
        )

        # Hard stop: if still over budget, truncate oldest verbatim turn
        if self._estimate_context_tokens(result) > budget:
            while (
                len(result.recent_turns) > 2
                and self._estimate_context_tokens(result) > budget
            ):
                result.recent_turns = result.recent_turns[2:]  # drop oldest pair

        return result

    def _estimate_context_tokens(self, ctx: HistoryContext) -> int:
        total = 0
        for turn in ctx.recent_turns:
            total += _estimate_tokens(turn.get("content", ""))
        if ctx.rolling_summary:
            total += _estimate_tokens(ctx.rolling_summary)
        for fact in ctx.unresolved_facts:
            total += _estimate_tokens(fact)
        return total

    async def _generate_summary(self, text: str) -> str:
        """Summarize conversation history using the summary model slot."""
        model = self._llm.resolve_model("summary")
        max_tokens = settings.support_summary_max_tokens

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a conversation summarizer for customer support. "
                    "Produce a concise summary preserving: prior issue context, "
                    "confirmed facts from tool calls (e.g. 'outage confirmed on 2026-04-10'), "
                    "open questions, specialist handoffs, and promises made. "
                    f"Keep under {max_tokens} tokens."
                ),
            },
            {
                "role": "user",
                "content": f"Summarize this conversation history:\n\n{text}",
            },
        ]
        return await self._llm.chat(messages, model, max_tokens=max_tokens)
