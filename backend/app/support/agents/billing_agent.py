"""Billing specialist agent.

Handles invoices, charges, balances, payments, and billing inquiries.
Tools: billing tools + escalation. Faithfulness uses generation model slot.
"""
from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.support.agents.prompts.billing import BILLING_SYSTEM_PROMPT
from app.support.agents.support_agent import SupportAgentDeps
from app.support.tools.registry import TOOL_REGISTRY

billing_agent = Agent(
    model="openai:gpt-4o",  # overridden at .run() via agent_model("billing")
    output_type=str,
    system_prompt=BILLING_SYSTEM_PROMPT,
    deps_type=SupportAgentDeps,
)


# Register billing-domain + shared tools
@billing_agent.tool
async def billing_get_invoice(
    ctx: RunContext[SupportAgentDeps], customer_id: str, invoice_id: str
) -> dict:
    """Get a specific invoice for the customer."""
    result = await TOOL_REGISTRY["billing_get_invoice"].func(customer_id, invoice_id)
    ctx.deps.tool_outputs.append({"tool": "billing_get_invoice", "output": result})
    return result


@billing_agent.tool
async def billing_list_charges(ctx: RunContext[SupportAgentDeps], customer_id: str) -> dict:
    """List recent charges for the customer."""
    result = await TOOL_REGISTRY["billing_list_charges"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "billing_list_charges", "output": result})
    return result


@billing_agent.tool
async def billing_get_balance(ctx: RunContext[SupportAgentDeps], customer_id: str) -> dict:
    """Get current account balance for the customer."""
    result = await TOOL_REGISTRY["billing_get_balance"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "billing_get_balance", "output": result})
    return result


# NOTE: ``get_escalation_contact`` is intentionally NOT registered as a
# tool here — see the matching note in ``technical_agent.py`` for
# rationale. The orchestrator injects the contact info into the user
# message before each agent call.
