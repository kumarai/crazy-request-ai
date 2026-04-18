"""Bill Pay specialist agent — payments, autopay, payment methods.

Mirrors the order agent's pattern: read tools registered here; write
tools (make_payment, enroll_autopay, payment_method_add,
payment_method_set_default) live on the action endpoint so they run
only after an explicit customer click.
"""
from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.support.agents.prompts.bill_pay import BILL_PAY_SYSTEM_PROMPT
from app.support.agents.proposal_tools import record_proposal
from app.support.agents.support_agent import SupportAgentDeps
from app.support.tools._mcp_bridge import get_mcp, is_live

bill_pay_agent = Agent(
    model="openai:gpt-4o",  # overridden via agent_model("bill_pay")
    output_type=str,
    system_prompt=BILL_PAY_SYSTEM_PROMPT,
    deps_type=SupportAgentDeps,
)


async def _call_mcp(name: str, args: dict) -> dict:
    if not is_live():
        return {"error": "mcp_unavailable"}
    try:
        return await get_mcp().call_tool(name, args)
    except Exception as e:
        return {"error": str(e)}


@bill_pay_agent.tool
async def get_balance(ctx: RunContext[SupportAgentDeps], customer_id: str) -> dict:
    """Customer's current balance + past-due."""
    result = await _call_mcp("billing_get_balance", {"customer_id": customer_id})
    ctx.deps.tool_outputs.append({"tool": "billing_get_balance", "output": result})
    return result


@bill_pay_agent.tool
async def list_payment_methods(
    ctx: RunContext[SupportAgentDeps], customer_id: str
) -> dict:
    """Saved payment methods on file."""
    result = await _call_mcp("payment_method_list", {"customer_id": customer_id})
    ctx.deps.tool_outputs.append({"tool": "payment_method_list", "output": result})
    return result


@bill_pay_agent.tool
async def propose_payment(
    ctx: RunContext[SupportAgentDeps],
    amount: float,
    payment_method_id: str,
    payment_method_label: str,
) -> dict:
    """Propose a one-time payment. Does NOT charge the card. The
    orchestrator turns this into a confirmation button; only a
    customer click on that button triggers the actual payment.

    Use this after you've confirmed the amount and the payment method
    with the customer in text.
    """
    label = f"Pay ${amount:.2f} with {payment_method_label}"
    return record_proposal(
        ctx.deps,
        kind="pay",
        label=label,
        confirm_text=f"Pay ${amount:.2f} now?",
        payload={"amount": amount, "payment_method_id": payment_method_id},
    )


@bill_pay_agent.tool
async def propose_autopay(
    ctx: RunContext[SupportAgentDeps],
    payment_method_id: str,
    payment_method_label: str,
) -> dict:
    """Propose enrolling in autopay. Does NOT enroll until the
    customer clicks the confirmation button.
    """
    return record_proposal(
        ctx.deps,
        kind="enroll_autopay",
        label=f"Enroll in autopay with {payment_method_label}",
        confirm_text=f"Enroll autopay with {payment_method_label}?",
        payload={"payment_method_id": payment_method_id},
    )
