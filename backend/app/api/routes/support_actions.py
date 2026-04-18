"""Endpoint for server-issued interactive action buttons.

Clicking a button in the support chat POSTs ``{action_id}`` here.
We look up the pending action in Redis, verify the caller owns it,
and dispatch to the matching MCP write tool. The response is the
``ActionResultEvent`` shape so the frontend can render it inline.

This is the ONE place write-tools run — specialists never call them
directly, so a hallucinated "I've placed your order" from the LLM
never actually mutates state.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.db.repositories.conversations import ConversationsRepository
from app.streaming.events import ActionResultEvent
from app.support.action_registry import ActionRegistry
from app.support.auth import clear_session, is_guest_id, read_session
from app.support.tools._mcp_bridge import get_mcp, is_live

logger = logging.getLogger("[api]")

router = APIRouter()


class ClickActionRequest(BaseModel):
    action_id: str


_MCP_TOOL_FOR_KIND = {
    "pay": "bill_pay_make_payment",
    "enroll_autopay": "bill_pay_enroll_autopay",
    "place_order": "order_place",
    "cancel_order": "order_cancel",
    "book_appointment": "appointment_book",
    "cancel_appointment": "appointment_cancel",
    "reschedule_appointment": "appointment_reschedule",
    "add_payment_method": "payment_method_add",
    "set_default_payment_method": "payment_method_set_default",
}

@router.post("/support/action", response_model=ActionResultEvent)
async def support_action_endpoint(
    body: ClickActionRequest,
    request: Request,
    response: Response,
) -> ActionResultEvent:
    ident = read_session(request)

    # ``sign_in`` is the only action kind that guests must be able to
    # click — by definition it's what moves them out of guest mode.
    # The frontend wires this to the existing LoginDialog, so the
    # server doesn't need to do anything except ack the click and let
    # the dialog drive ``/v1/login``.
    registry = ActionRegistry(request.app.state.redis)
    action = await registry.claim(body.action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="action_not_found_or_expired")

    if action.kind == "sign_in":
        if ident is not None and not ident.is_guest:
            # Already signed in — nothing to do, just ack.
            return ActionResultEvent(
                action_id=action.action_id,
                kind="sign_in",
                status="success",
                message=f"Already signed in as {ident.customer_id}.",
            )
        return ActionResultEvent(
            action_id=action.action_id,
            kind="sign_in",
            status="pending",
            message="Open the sign-in dialog to continue.",
            detail={"open_login_dialog": True},
        )

    # ``discard_order_draft`` is a no-op dismissal — pair of the
    # place_order button. No MCP call, no auth change, just ack the
    # customer's choice so the transcript shows they backed out.
    # Guests can hit this too (they could reach the order flow as a
    # browse-only user; dismissal shouldn't require auth).
    if action.kind == "discard_order_draft":
        summary = (action.payload or {}).get("summary", "")
        message = (
            f"Order draft discarded — {summary}." if summary
            else "Order draft discarded. No order was placed."
        )
        try:
            sf = request.app.state.session_factory
            conv_repo = ConversationsRepository(sf)
            conv_uuid = UUID(action.conversation_id)
            await conv_repo.add_message(
                conv_uuid,
                role="action_result",
                content=message,
                citations_json={
                    "kind": action.kind,
                    "action_id": action.action_id,
                },
            )
        except Exception as e:
            logger.error("Failed to persist discard action: %s", e)
        return ActionResultEvent(
            action_id=action.action_id,
            kind=action.kind,
            status="success",
            message=message,
            detail={"discarded": True},
        )

    # Every other kind (writes + sign_out) requires an authed session.
    if ident is None or ident.is_guest:
        raise HTTPException(
            status_code=401, detail="login_required_for_actions"
        )

    if action.customer_id != ident.customer_id:
        logger.warning(
            "Action ownership mismatch: action.customer=%s ident=%s",
            action.customer_id, ident.customer_id,
        )
        raise HTTPException(status_code=403, detail="not_your_action")

    if action.kind == "sign_out":
        # Clear the session cookie + wipe conversation state so the
        # next turn starts from a blank slate (no stale
        # ``last_specialist`` or stashed ``pending_intent``).
        clear_session(response)
        try:
            sf = request.app.state.session_factory
            conv_repo = ConversationsRepository(sf)
            conv_uuid = UUID(action.conversation_id)
            await conv_repo.update_conversation(
                conv_uuid,
                last_specialist=None,
                pending_intent_json=None,
            )
        except Exception as e:
            logger.error("Failed to clear conversation state on sign_out: %s", e)
        return ActionResultEvent(
            action_id=action.action_id,
            kind="sign_out",
            status="success",
            message=f"Signed out {ident.customer_id}.",
            detail={"cleared_session": True},
        )

    tool_name = _MCP_TOOL_FOR_KIND.get(action.kind)
    if tool_name is None:
        raise HTTPException(status_code=400, detail=f"unknown_action_kind:{action.kind}")

    # Augment with identity fields MCP tools need. payload carries the
    # tool-specific args (amount, sku_ids, slot_id, etc.). We never
    # trust client-supplied customer_id / conversation_id — the values
    # are baked into the pending-action entry at creation time.
    args = {
        **action.payload,
        "customer_id": action.customer_id,
        "conversation_id": action.conversation_id,
    }

    if not is_live():
        return ActionResultEvent(
            action_id=action.action_id,
            kind=action.kind,
            status="error",
            message="Live transaction service is unavailable.",
        )

    try:
        result = await get_mcp().call_tool(tool_name, args)
    except Exception as e:
        logger.error("Action %s (%s) failed: %s", action.action_id, tool_name, e)
        return ActionResultEvent(
            action_id=action.action_id,
            kind=action.kind,
            status="error",
            message=f"The {action.kind.replace('_', ' ')} did not complete.",
            detail={"error": str(e)},
        )

    # Persist a message row so the transcript records the action outcome
    # when the conversation is reloaded.
    try:
        sf = request.app.state.session_factory
        conv_repo = ConversationsRepository(sf)
        conv_uuid = UUID(action.conversation_id)
        await conv_repo.add_message(
            conv_uuid,
            role="action_result",
            content=_render_action_summary(action.kind, result),
            citations_json={
                "kind": action.kind,
                "action_id": action.action_id,
                "result": result,
            },
        )
        # idempotency replay is surfaced explicitly so the UX can
        # distinguish "we already did this" from a fresh success.
        if result.get("_replayed"):
            status = "success"
            message = "This action was already completed (idempotent replay)."
        else:
            status = "success"
            message = _render_action_summary(action.kind, result)
    except Exception as e:
        logger.error("Failed to persist action message: %s", e)
        status = "success"
        message = _render_action_summary(action.kind, result)

    return ActionResultEvent(
        action_id=action.action_id,
        kind=action.kind,
        status=status,
        message=message,
        detail=result,
    )


def _render_action_summary(kind: str, result: dict) -> str:
    """Humanize the MCP response into a single sentence."""
    if kind == "pay":
        amt = result.get("amount")
        return f"Payment of ${amt:.2f} succeeded." if amt else "Payment succeeded."
    if kind == "place_order":
        oid = result.get("order_id")
        eta = result.get("eta")
        return f"Order {oid} placed. ETA: {eta}." if oid else "Order placed."
    if kind == "cancel_order":
        return "Order cancelled."
    if kind == "book_appointment":
        start = result.get("slot_start")
        return f"Appointment booked for {start}." if start else "Appointment booked."
    if kind == "cancel_appointment":
        return "Appointment cancelled."
    if kind == "reschedule_appointment":
        return "Appointment rescheduled."
    if kind == "enroll_autopay":
        return "Autopay enrolled."
    if kind == "add_payment_method":
        return "Payment method saved."
    if kind == "set_default_payment_method":
        return "Default payment method updated."
    return f"{kind.replace('_', ' ').title()} completed."
