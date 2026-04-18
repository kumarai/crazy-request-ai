"""Repository for customer-support conversations, messages, and tool calls."""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Conversation, Message, ToolCallRecord

logger = logging.getLogger("[db]")


class ConversationsRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------
    async def create_conversation(
        self, customer_id: str, channel: str = "web"
    ) -> dict:
        async with self._sf() as session:
            conv = Conversation(customer_id=customer_id, channel=channel)
            session.add(conv)
            await session.commit()
            await session.refresh(conv)
            return conv.to_dict()

    async def get_conversation(self, conversation_id: UUID) -> dict | None:
        async with self._sf() as session:
            conv = await session.get(Conversation, conversation_id)
            return conv.to_dict() if conv else None

    async def list_for_customer(
        self, customer_id: str, limit: int = 50
    ) -> list[dict]:
        """Return conversations for a customer, newest first.

        Includes a preview snippet (first user message) + message count so
        the UI can render a switcher without a second round-trip per row.
        """
        async with self._sf() as session:
            preview_sq = (
                select(Message.content)
                .where(
                    Message.conversation_id == Conversation.id,
                    Message.role == "user",
                )
                .order_by(Message.created_at.asc())
                .limit(1)
                .correlate(Conversation)
                .scalar_subquery()
            )
            count_sq = (
                select(func.count(Message.id))
                .where(Message.conversation_id == Conversation.id)
                .correlate(Conversation)
                .scalar_subquery()
            )
            cost_sq = (
                select(func.coalesce(func.sum(Message.cost_usd), 0))
                .where(Message.conversation_id == Conversation.id)
                .correlate(Conversation)
                .scalar_subquery()
            )
            stmt = (
                select(
                    Conversation.id,
                    Conversation.customer_id,
                    Conversation.status,
                    Conversation.created_at,
                    Conversation.updated_at,
                    Conversation.last_specialist,
                    Conversation.title,
                    preview_sq.label("preview"),
                    count_sq.label("message_count"),
                    cost_sq.label("cost_usd"),
                )
                .where(Conversation.customer_id == customer_id)
                .order_by(Conversation.updated_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [dict(row._mapping) for row in result.all()]

    async def validate_ownership(
        self, conversation_id: UUID, customer_id: str
    ) -> bool:
        async with self._sf() as session:
            stmt = select(Conversation.customer_id).where(
                Conversation.id == conversation_id
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return row == customer_id

    async def get_pending_intent(self, conversation_id: UUID) -> dict | None:
        """Return the stashed pending intent, or ``None`` if not set.

        Set by the orchestrator when a guest hits an auth-gated
        specialist; read after the customer signs in to decide whether
        to offer a resume. Value shape:
        ``{"specialist": str, "query": str, "ts": float}``.
        """
        async with self._sf() as session:
            stmt = select(Conversation.pending_intent_json).where(
                Conversation.id == conversation_id
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def rebind_customer_id(
        self, conversation_id: UUID, new_customer_id: str
    ) -> None:
        """Transfer a conversation from one customer_id to another.

        Used when a guest logs in mid-conversation and we want to keep
        the transcript. Callers must pre-verify that the current owner
        is a guest OR matches ``new_customer_id``.
        """
        async with self._sf() as session:
            stmt = (
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(customer_id=new_customer_id)
            )
            await session.execute(stmt)
            await session.commit()

    async def update_conversation(
        self, conversation_id: UUID, **fields: Any
    ) -> None:
        allowed = {
            "last_specialist",
            "last_handoff_json",
            "rolling_summary",
            "unresolved_facts_json",
            "metadata_json",
            "pending_intent_json",
            "status",
            "title",
        }
        filtered = {k: v for k, v in fields.items() if k in allowed}
        if not filtered:
            return
        async with self._sf() as session:
            stmt = (
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(**filtered)
            )
            await session.execute(stmt)
            await session.commit()

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------
    async def add_message(
        self,
        conversation_id: UUID,
        role: str,
        content: str,
        specialist_used: str | None = None,
        citations_json: dict | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
    ) -> dict:
        async with self._sf() as session:
            msg = Message(
                conversation_id=conversation_id,
                role=role,
                content=content,
                specialist_used=specialist_used,
                citations_json=citations_json,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
            )
            session.add(msg)
            await session.commit()
            await session.refresh(msg)
            return msg.to_dict()

    async def update_message_citations(
        self, message_id: UUID, citations_json: dict
    ) -> None:
        """Attach structured rehydration data to an assistant message row.

        Called after the main flow emits followups/actions/scope_warn —
        those events are computed after the message is first inserted,
        so we patch the row in place so reloads can replay them.
        """
        async with self._sf() as session:
            stmt = (
                update(Message)
                .where(Message.id == message_id)
                .values(citations_json=citations_json)
            )
            await session.execute(stmt)
            await session.commit()

    async def get_conversation_totals(self, conversation_id: UUID) -> dict:
        """Sum tokens + USD cost + message count across a conversation.

        Returns zeros (not nulls) when no assistant message has usage —
        lets the UI render ``0 tokens · $0.00`` without branching.
        """
        async with self._sf() as session:
            stmt = select(
                func.coalesce(func.sum(Message.input_tokens), 0),
                func.coalesce(func.sum(Message.output_tokens), 0),
                func.coalesce(func.sum(Message.cost_usd), 0),
                func.count(Message.id),
            ).where(Message.conversation_id == conversation_id)
            result = await session.execute(stmt)
            row = result.one()
            input_tokens, output_tokens, cost_usd, message_count = row
            return {
                "conversation_id": str(conversation_id),
                "input_tokens": int(input_tokens),
                "output_tokens": int(output_tokens),
                "total_tokens": int(input_tokens) + int(output_tokens),
                "cost_usd": float(cost_usd),
                "message_count": int(message_count),
            }

    async def get_recent_messages(
        self, conversation_id: UUID, limit: int = 20
    ) -> list[dict]:
        async with self._sf() as session:
            stmt = (
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            # Return in chronological order
            return [r.to_dict() for r in reversed(rows)]

    # ------------------------------------------------------------------
    # Tool calls
    # ------------------------------------------------------------------
    async def add_tool_call(
        self,
        conversation_id: UUID,
        tool_name: str,
        input_json: dict | None = None,
        output_json: dict | None = None,
        message_id: UUID | None = None,
    ) -> dict:
        async with self._sf() as session:
            tc = ToolCallRecord(
                conversation_id=conversation_id,
                message_id=message_id,
                tool_name=tool_name,
                input_json=input_json,
                output_json=output_json,
            )
            session.add(tc)
            await session.commit()
            await session.refresh(tc)
            return tc.to_dict()

    async def get_tool_calls_for_conversation(
        self, conversation_id: UUID, limit: int = 50
    ) -> list[dict]:
        async with self._sf() as session:
            stmt = (
                select(ToolCallRecord)
                .where(ToolCallRecord.conversation_id == conversation_id)
                .order_by(ToolCallRecord.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [r.to_dict() for r in reversed(rows)]
