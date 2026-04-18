"""Order specialist agent — catalog browse, status lookup, order placement.

Write operations (``order_place``, ``order_cancel``) are NOT registered
as tools here. The agent recommends an action in text; the orchestrator
emits an INTERACTIVE_ACTIONS button the customer clicks to commit.
This keeps the LLM from firing a write without an explicit user click.
"""
from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.support.agents.prompts.order import ORDER_SYSTEM_PROMPT
from app.support.agents.proposal_tools import record_proposal
from app.support.agents.support_agent import SupportAgentDeps
from app.support.tools._mcp_bridge import get_mcp, is_live

order_agent = Agent(
    model="openai:gpt-4o-mini",  # overridden via agent_model("order")
    output_type=str,
    system_prompt=ORDER_SYSTEM_PROMPT,
    deps_type=SupportAgentDeps,
)


async def _call_mcp(name: str, args: dict) -> dict:
    if not is_live():
        return {"error": "mcp_unavailable"}
    try:
        return await get_mcp().call_tool(name, args)
    except Exception as e:
        return {"error": str(e)}


@order_agent.tool
async def list_catalog(
    ctx: RunContext[SupportAgentDeps], category: str | None = None
) -> dict:
    """Browse the product catalog. Optional category filter."""
    args = {"category": category} if category else {}
    result = await _call_mcp("order_list_catalog", args)
    ctx.deps.tool_outputs.append({"tool": "order_list_catalog", "output": result})
    return result


@order_agent.tool
async def quote(
    ctx: RunContext[SupportAgentDeps], customer_id: str, sku_ids: list[str]
) -> dict:
    """Price a cart before committing."""
    result = await _call_mcp(
        "order_quote", {"customer_id": customer_id, "sku_ids": sku_ids}
    )
    ctx.deps.tool_outputs.append({"tool": "order_quote", "output": result})
    return result


@order_agent.tool
async def list_orders(ctx: RunContext[SupportAgentDeps], customer_id: str) -> dict:
    """List all orders for the customer."""
    result = await _call_mcp("order_list", {"customer_id": customer_id})
    ctx.deps.tool_outputs.append({"tool": "order_list", "output": result})
    return result


@order_agent.tool
async def order_status(
    ctx: RunContext[SupportAgentDeps], customer_id: str, order_id: str
) -> dict:
    """Get an order's status + items."""
    result = await _call_mcp(
        "order_get", {"customer_id": customer_id, "order_id": order_id}
    )
    ctx.deps.tool_outputs.append({"tool": "order_get", "output": result})
    return result


@order_agent.tool
async def shipment_status(
    ctx: RunContext[SupportAgentDeps], customer_id: str, order_id: str
) -> dict:
    """Get shipment tracking + ETA."""
    result = await _call_mcp(
        "order_shipment_status",
        {"customer_id": customer_id, "order_id": order_id},
    )
    ctx.deps.tool_outputs.append(
        {"tool": "order_shipment_status", "output": result}
    )
    return result


@order_agent.tool
async def list_payment_methods(
    ctx: RunContext[SupportAgentDeps], customer_id: str
) -> dict:
    """Shared tool — list the customer's saved payment methods."""
    result = await _call_mcp("payment_method_list", {"customer_id": customer_id})
    ctx.deps.tool_outputs.append({"tool": "payment_method_list", "output": result})
    return result


@order_agent.tool
async def propose_place_order(
    ctx: RunContext[SupportAgentDeps],
    sku_ids: list[str],
    total: float,
    payment_method_id: str,
    payment_method_label: str,
    summary: str,
) -> dict:
    """Propose placing an order. Does NOT commit — surfaces a confirmation
    button to the customer. ``summary`` should be a short one-liner for
    the button label, e.g. "iPhone 15 Pro — $999".
    """
    label = f"Place order: {summary} — ${total:.2f}"
    return record_proposal(
        ctx.deps,
        kind="place_order",
        label=label,
        confirm_text=f"Place this order for ${total:.2f}?",
        payload={
            "sku_ids": sku_ids,
            "payment_method_id": payment_method_id,
            # expose for the action-endpoint response renderer
            "summary": summary,
            "total": total,
            "payment_method_label": payment_method_label,
        },
    )


@order_agent.tool
async def propose_cancel_order(
    ctx: RunContext[SupportAgentDeps], order_id: str, summary: str
) -> dict:
    """Propose cancelling an order. Customer confirms via button."""
    return record_proposal(
        ctx.deps,
        kind="cancel_order",
        label=f"Cancel order {order_id[:10]} ({summary})",
        confirm_text=f"Cancel order {order_id}? This cannot be undone.",
        payload={"order_id": order_id},
    )


@order_agent.tool
async def propose_discard_order_draft(
    ctx: RunContext[SupportAgentDeps], summary: str
) -> dict:
    """Propose dismissing the pending order draft (customer changes their mind).

    Pairs with ``propose_place_order`` so the customer sees both
    "Place order" and "Discard" buttons side-by-side. Clicking discard
    makes no MCP call — it just acks the dismissal so the transcript
    reflects the choice. ``summary`` is a short description of what's
    being dropped (e.g. "iPhone 15 Pro — $1086.41") used as the
    button label and in the confirmation banner.
    """
    return record_proposal(
        ctx.deps,
        kind="discard_order_draft",
        label=f"Discard: {summary}",
        confirm_text=None,
        payload={"summary": summary},
    )
