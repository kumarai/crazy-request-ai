"""Customer context resolution for support conversations.

Resolves identity, plan, services, and authz from request headers.
Phase 0.5: returns mock data; wired to real MCP backends later.
"""
from __future__ import annotations

import logging
from uuid import UUID

from pydantic import BaseModel

from app.db.repositories.conversations import ConversationsRepository

logger = logging.getLogger("[support]")


class CustomerContext(BaseModel):
    customer_id: str
    plan: str = "unknown"
    services: list[str] = []
    flags: dict = {}
    allowed_source_ids: list[str] = []
    is_guest: bool = False
    zip_code: str | None = None


async def resolve_customer_context(
    customer_id: str,
    is_guest: bool = False,
) -> CustomerContext:
    """Resolve customer context.

    For guests we return a minimal context with no plan/services so the
    orchestrator treats them accordingly (scope gate blocks billing /
    bill_pay / order placement / appointment booking; technical and
    general still work).
    """
    if is_guest:
        return CustomerContext(
            customer_id=customer_id,
            plan="guest",
            services=[],
            flags={"active": False},
            allowed_source_ids=[],
            is_guest=True,
        )

    # TODO: Wire to real MCP customer header endpoint
    return CustomerContext(
        customer_id=customer_id,
        plan="premium",
        services=["internet", "tv", "voice", "mobile"],
        flags={"active": True},
        allowed_source_ids=[],
    )


async def resolve_conversation(
    conversation_id: str | None,
    customer_id: str,
    new_conversation: bool,
    conversations_repo: ConversationsRepository,
) -> tuple[UUID, bool]:
    """Resolve or create a conversation, enforcing ownership.

    Returns (conversation_id, is_new).
    Raises PermissionError on ownership mismatch.
    """
    if new_conversation or conversation_id is None:
        conv = await conversations_repo.create_conversation(customer_id)
        return conv["id"], True

    conv_uuid = UUID(conversation_id)
    is_owner = await conversations_repo.validate_ownership(conv_uuid, customer_id)
    if not is_owner:
        raise PermissionError(
            f"Conversation {conversation_id} does not belong to customer {customer_id}"
        )
    return conv_uuid, False
