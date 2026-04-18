"""Customer support query endpoint with SSE streaming."""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from fastapi import Response

from app.db.repositories.chunks import ChunksRepository
from app.db.repositories.conversations import ConversationsRepository
from app.db.repositories.sources import SourcesRepository
from app.support.action_catalog import get_action
from app.support.auth import (
    is_guest_id,
    issue_session,
    make_guest_id,
    read_session,
)
from app.support.customer_context import (
    resolve_conversation,
    resolve_customer_context,
)
from app.support.orchestrator import SupportOrchestrator
from app.support.session_cache import SessionCache

logger = logging.getLogger("[api]")

router = APIRouter()


class SupportQueryRequest(BaseModel):
    query: str
    source_ids: list[str] | None = None
    provider: str | None = None


class ActionClickRequest(BaseModel):
    topic: str


@router.post("/support/query")
async def support_query_endpoint(
    body: SupportQueryRequest,
    request: Request,
    x_conversation_id: str | None = Header(None, alias="X-Conversation-Id"),
    x_new_conversation: bool = Header(False, alias="X-New-Conversation"),
    x_customer_id: str | None = Header(None, alias="X-Customer-Id"),
):
    """Support turn entrypoint.

    Identity resolution order:
      1. Signed session cookie (``support_session``) — the normal path.
      2. ``X-Customer-Id`` header — back-compat for API callers.
      3. Mint a guest id and set the cookie so the browser keeps a
         stable guest session.

    When a prior guest conversation exists and the caller is now
    authenticated with a matching (or previously-guest) conversation,
    we rebind ``conversation.customer_id`` so the transcript follows
    the user across login. Cross-customer rebinds are rejected.
    """
    sf = request.app.state.session_factory
    conv_repo = ConversationsRepository(sf)

    # 1) Identity from cookie first
    ident = read_session(request)
    cookie_needs_set = False
    if ident is not None:
        current_customer_id = ident.customer_id
        is_guest = ident.is_guest
    elif x_customer_id:
        # 2) Header fallback (service-to-service clients)
        current_customer_id = x_customer_id
        is_guest = is_guest_id(x_customer_id)
    else:
        # 3) Mint guest id
        current_customer_id = make_guest_id()
        is_guest = True
        cookie_needs_set = True

    customer = await resolve_customer_context(current_customer_id, is_guest=is_guest)

    # 2) Conversation resolution + guest→authed rebind
    conversation_id = None
    is_new = False
    rebind_succeeded = False

    if x_conversation_id and not x_new_conversation:
        from uuid import UUID
        try:
            candidate = UUID(x_conversation_id)
        except ValueError:
            raise HTTPException(400, "invalid_conversation_id")

        existing = await conv_repo.get_conversation(candidate)
        if existing is None:
            raise HTTPException(404, "conversation_not_found")

        prior_owner = existing["customer_id"]
        if prior_owner == current_customer_id:
            conversation_id = candidate
        elif is_guest_id(prior_owner) and not is_guest:
            # Guest-to-authed upgrade: rebind so the transcript follows
            # the user into their logged-in account.
            await conv_repo.rebind_customer_id(candidate, current_customer_id)
            conversation_id = candidate
            rebind_succeeded = True
            logger.info(
                "Rebound conversation %s from guest %s -> %s",
                candidate, prior_owner, current_customer_id,
            )
        else:
            raise HTTPException(
                status_code=403,
                detail="Conversation does not belong to this customer",
            )

    if conversation_id is None:
        try:
            conversation_id, is_new = await resolve_conversation(
                None, current_customer_id, True, conv_repo
            )
        except PermissionError:
            raise HTTPException(403, "Conversation does not belong to this customer")

    chunks_repo = ChunksRepository(sf)
    sources_repo = SourcesRepository(sf)
    session_cache = SessionCache(request.app.state.redis)

    orchestrator = SupportOrchestrator(
        conversations_repo=conv_repo,
        chunks_repo=chunks_repo,
        sources_repo=sources_repo,
        session_cache=session_cache,
        llm_client=request.app.state.llm_client,
        redis=request.app.state.redis,
    )

    response = EventSourceResponse(
        orchestrator.stream(
            query=body.query,
            customer=customer,
            conversation_id=conversation_id,
            source_ids=body.source_ids,
            provider=body.provider,
        ),
        media_type="text/event-stream",
    )

    if cookie_needs_set:
        issue_session(response, current_customer_id)
    response.headers["X-Conversation-Id"] = str(conversation_id)
    response.headers["X-Is-Guest"] = "true" if is_guest else "false"
    if rebind_succeeded:
        response.headers["X-Conversation-Rebound"] = "true"
    return response


@router.get("/support/conversations")
async def list_support_conversations_endpoint(
    request: Request,
    x_customer_id: str = Header(..., alias="X-Customer-Id"),
    limit: int = 50,
):
    """List conversations for the calling customer, newest first."""
    sf = request.app.state.session_factory
    conv_repo = ConversationsRepository(sf)
    rows = await conv_repo.list_for_customer(x_customer_id, limit=limit)
    # Cast UUID + datetimes to strings for JSON serialization
    return [
        {
            "id": str(r["id"]),
            "customer_id": r["customer_id"],
            "status": r["status"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            "last_specialist": r["last_specialist"],
            "title": r["title"],
            "preview": r["preview"],
            "message_count": r["message_count"],
            "cost_usd": float(r["cost_usd"]) if r["cost_usd"] is not None else 0.0,
        }
        for r in rows
    ]


@router.get("/support/conversations/{conversation_id}/messages")
async def get_support_conversation_messages_endpoint(
    conversation_id: UUID,
    request: Request,
    x_customer_id: str = Header(..., alias="X-Customer-Id"),
    limit: int = 200,
):
    """Return the transcript for a conversation. Enforces ownership."""
    sf = request.app.state.session_factory
    conv_repo = ConversationsRepository(sf)

    is_owner = await conv_repo.validate_ownership(conversation_id, x_customer_id)
    if not is_owner:
        raise HTTPException(
            status_code=403,
            detail="Conversation does not belong to this customer",
        )

    msgs = await conv_repo.get_recent_messages(conversation_id, limit=limit)
    return [
        {
            "id": str(m["id"]),
            "role": m["role"],
            "content": m["content"],
            "specialist_used": m.get("specialist_used"),
            "citations_json": m.get("citations_json"),
            "created_at": m["created_at"].isoformat() if m.get("created_at") else None,
        }
        for m in msgs
    ]


@router.get("/support/conversations/{conversation_id}/totals")
async def support_conversation_totals_endpoint(
    conversation_id: UUID,
    request: Request,
    x_customer_id: str = Header(..., alias="X-Customer-Id"),
):
    """Return running token + USD totals for an entire conversation.

    Enforces ownership — matches the rule on ``/support/query``. The UI
    calls this on conversation load and again after each ``done`` event
    to refresh the header badge.
    """
    sf = request.app.state.session_factory
    conv_repo = ConversationsRepository(sf)

    is_owner = await conv_repo.validate_ownership(conversation_id, x_customer_id)
    if not is_owner:
        raise HTTPException(
            status_code=403,
            detail="Conversation does not belong to this customer",
        )

    return await conv_repo.get_conversation_totals(conversation_id)


@router.post("/support/conversations/{conversation_id}/action_click")
async def support_action_click_endpoint(
    conversation_id: UUID,
    body: ActionClickRequest,
    request: Request,
    x_customer_id: str = Header(..., alias="X-Customer-Id"),
):
    """Log an action-link click as a ``role="action"`` message in the transcript.

    No LLM turn is triggered. The client opens the URL in a new tab
    separately; this endpoint only records the click for the conversation
    history and analytics.
    """
    sf = request.app.state.session_factory
    conv_repo = ConversationsRepository(sf)

    # Enforce ownership — same rule as /support/query.
    is_owner = await conv_repo.validate_ownership(conversation_id, x_customer_id)
    if not is_owner:
        raise HTTPException(
            status_code=403,
            detail="Conversation does not belong to this customer",
        )

    # Server-side lookup prevents clients from logging arbitrary URLs.
    action = get_action(body.topic)
    if action is None:
        raise HTTPException(status_code=404, detail="Unknown action topic")

    msg = await conv_repo.add_message(
        conversation_id=conversation_id,
        role="action",
        content=f"Clicked: {action.label}",
        citations_json={
            "topic": action.topic,
            "url": str(action.url),
            "label": action.label,
        },
    )
    return {
        "message_id": msg.get("id"),
        "topic": action.topic,
        "url": str(action.url),
        "label": action.label,
    }
