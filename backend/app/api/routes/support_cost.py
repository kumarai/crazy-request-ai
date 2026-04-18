"""Cost dashboard endpoints for the support chat.

Aggregates per-specialist token + USD totals so the operator can see
where money goes. Pure read; no mutations. Respects the signed
session cookie — admins pass ``X-API-Key`` for admin-scoped reporting.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from app.db.models import Conversation, Message
from app.support.auth import read_session

logger = logging.getLogger("[api]")

router = APIRouter()


class SpecialistCostRow(BaseModel):
    specialist: str
    message_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float


class CostSummaryResponse(BaseModel):
    window_days: int
    scope: str  # "all" | "customer:<cid>"
    generated_at: str
    totals: SpecialistCostRow  # aggregate across all specialists
    by_specialist: list[SpecialistCostRow]


@router.get("/support/cost_summary", response_model=CostSummaryResponse)
async def support_cost_summary_endpoint(
    request: Request,
    days: int = Query(7, ge=1, le=365),
    customer_id: str | None = Query(None),
) -> CostSummaryResponse:
    """Per-specialist cost + token totals over the last ``days`` days.

    If ``customer_id`` is passed, the aggregate is scoped to that
    customer — useful for the cost badge on a single account. Without
    it, returns global totals (admin view); the endpoint does not try
    to enforce admin-only access — that's the job of the existing
    ``APIKeyMiddleware`` which already runs ahead of this route.
    """
    # Lightweight authz: if a non-admin caller passes a customer_id,
    # it must match the session identity. This prevents a customer from
    # reading another customer's spend by guessing the id. Admins (API
    # key path) bypass this — they never have a session cookie.
    ident = read_session(request)
    if customer_id and ident is not None and not ident.is_guest:
        if ident.customer_id != customer_id:
            raise HTTPException(
                status_code=403, detail="not_your_customer_id"
            )

    sf = request.app.state.session_factory
    since = datetime.now(timezone.utc) - timedelta(days=days)

    async with sf() as session:
        stmt = (
            select(
                Message.specialist_used,
                func.count(Message.id).label("message_count"),
                func.coalesce(func.sum(Message.input_tokens), 0).label("in_toks"),
                func.coalesce(func.sum(Message.output_tokens), 0).label("out_toks"),
                func.coalesce(func.sum(Message.cost_usd), 0.0).label("cost"),
            )
            .where(Message.role == "assistant")
            .where(Message.created_at >= since)
            .group_by(Message.specialist_used)
        )
        if customer_id:
            # Constrain to the customer's conversations.
            stmt = stmt.join(
                Conversation, Conversation.id == Message.conversation_id
            ).where(Conversation.customer_id == customer_id)

        result = await session.execute(stmt)
        rows = result.all()

    by_specialist: list[SpecialistCostRow] = []
    total_count = 0
    total_in = 0
    total_out = 0
    total_cost = 0.0
    for row in rows:
        specialist = row.specialist_used or "unknown"
        mc = int(row.message_count or 0)
        in_t = int(row.in_toks or 0)
        out_t = int(row.out_toks or 0)
        cost = float(row.cost or 0.0)
        total_count += mc
        total_in += in_t
        total_out += out_t
        total_cost += cost
        by_specialist.append(
            SpecialistCostRow(
                specialist=specialist,
                message_count=mc,
                input_tokens=in_t,
                output_tokens=out_t,
                total_tokens=in_t + out_t,
                cost_usd=round(cost, 6),
            )
        )

    by_specialist.sort(key=lambda r: r.cost_usd, reverse=True)

    return CostSummaryResponse(
        window_days=days,
        scope=f"customer:{customer_id}" if customer_id else "all",
        generated_at=datetime.now(timezone.utc).isoformat(),
        totals=SpecialistCostRow(
            specialist="__total__",
            message_count=total_count,
            input_tokens=total_in,
            output_tokens=total_out,
            total_tokens=total_in + total_out,
            cost_usd=round(total_cost, 6),
        ),
        by_specialist=by_specialist,
    )
