"""Single customer-support agent (Phase 0.5).

Handles all domains before the router + specialist split in Phase 1.
All MCP tools are registered on this agent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, RunContext

from app.support.agents.prompts import SUPPORT_SYSTEM_PROMPT
from app.support.customer_context import CustomerContext
from app.support.history import HistoryContext
from app.support.tools.registry import TOOL_REGISTRY


@dataclass
class SupportAgentDeps:
    customer: CustomerContext
    history: HistoryContext
    retrieved_context: str  # formatted retrieved chunks
    tool_outputs: list[dict]  # accumulated tool call results this turn
    # Set by the orchestrator when a guest hit an auth-gated specialist
    # on a prior turn and this turn was handed off to the account
    # specialist for sign-in. Shape: ``{"specialist": str, "query": str,
    # "ts": float}``. The account agent reads it via
    # ``check_session_status`` and offers to resume the original intent
    # after the customer signs in.
    pending_intent: dict | None = None


support_agent = Agent(
    model="openai:gpt-4o",  # overridden at .run() via agent_model("generation")
    output_type=str,
    system_prompt=SUPPORT_SYSTEM_PROMPT,
    deps_type=SupportAgentDeps,
)


# ------------------------------------------------------------------
# Register all MCP tools from the registry
# ------------------------------------------------------------------

@support_agent.tool
async def voice_get_details(
    ctx: RunContext[SupportAgentDeps], customer_id: str
) -> dict:
    """Get voice service details for the customer."""
    result = await TOOL_REGISTRY["voice_get_details"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "voice_get_details", "output": result})
    return result


@support_agent.tool
async def mobile_get_details(
    ctx: RunContext[SupportAgentDeps], customer_id: str
) -> dict:
    """Get mobile service details for the customer."""
    result = await TOOL_REGISTRY["mobile_get_details"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "mobile_get_details", "output": result})
    return result


@support_agent.tool
async def internet_get_details(
    ctx: RunContext[SupportAgentDeps], customer_id: str
) -> dict:
    """Get internet service details for the customer."""
    result = await TOOL_REGISTRY["internet_get_details"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "internet_get_details", "output": result})
    return result


@support_agent.tool
async def tv_get_details(
    ctx: RunContext[SupportAgentDeps], customer_id: str
) -> dict:
    """Get TV service details for the customer."""
    result = await TOOL_REGISTRY["tv_get_details"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "tv_get_details", "output": result})
    return result


@support_agent.tool
async def list_devices(
    ctx: RunContext[SupportAgentDeps], customer_id: str
) -> dict:
    """List all devices on the customer account."""
    result = await TOOL_REGISTRY["list_devices"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "list_devices", "output": result})
    return result


@support_agent.tool
async def get_device(
    ctx: RunContext[SupportAgentDeps], device_id: str
) -> dict:
    """Get detailed info for a specific device."""
    result = await TOOL_REGISTRY["get_device"].func(device_id)
    ctx.deps.tool_outputs.append({"tool": "get_device", "output": result})
    return result


@support_agent.tool
async def get_outage_for_customer(
    ctx: RunContext[SupportAgentDeps], customer_id: str
) -> dict:
    """Check for active outages affecting the customer."""
    result = await TOOL_REGISTRY["get_outage_for_customer"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "get_outage_for_customer", "output": result})
    return result


@support_agent.tool
async def get_recent_tickets(
    ctx: RunContext[SupportAgentDeps], customer_id: str
) -> dict:
    """Get recent support tickets for the customer."""
    result = await TOOL_REGISTRY["get_recent_tickets"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "get_recent_tickets", "output": result})
    return result


@support_agent.tool
async def billing_get_invoice(
    ctx: RunContext[SupportAgentDeps], customer_id: str, invoice_id: str
) -> dict:
    """Get a specific invoice for the customer."""
    result = await TOOL_REGISTRY["billing_get_invoice"].func(customer_id, invoice_id)
    ctx.deps.tool_outputs.append({"tool": "billing_get_invoice", "output": result})
    return result


@support_agent.tool
async def billing_list_charges(
    ctx: RunContext[SupportAgentDeps], customer_id: str
) -> dict:
    """List recent charges for the customer."""
    result = await TOOL_REGISTRY["billing_list_charges"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "billing_list_charges", "output": result})
    return result


@support_agent.tool
async def billing_get_balance(
    ctx: RunContext[SupportAgentDeps], customer_id: str
) -> dict:
    """Get current account balance for the customer."""
    result = await TOOL_REGISTRY["billing_get_balance"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "billing_get_balance", "output": result})
    return result


@support_agent.tool
async def get_escalation_contact(
    ctx: RunContext[SupportAgentDeps], topic: str
) -> dict:
    """Get the escalation contact info (phone, URL, chat) for a topic."""
    result = await TOOL_REGISTRY["get_escalation_contact"].func(topic)
    ctx.deps.tool_outputs.append({"tool": "get_escalation_contact", "output": result})
    return result


def build_user_message(
    query: str,
    customer: CustomerContext,
    history: HistoryContext,
    retrieved_context: str,
    escalation_contact: dict | None = None,
    language_directive: str | None = None,
) -> str:
    """Build the user message with all context for the agent.

    ``escalation_contact`` (when supplied by the orchestrator) is the
    pre-fetched contact info for the active specialist's domain. It's
    injected here so the agent has it ready without needing to call
    ``get_escalation_contact`` as a tool — saves a full LLM round-trip
    per turn. The prompt instructs the agent to mention it sparingly.

    ``language_directive`` (e.g. "Respond in Spanish.") flips the output
    language to match the customer's input. The specialist system
    prompts stay English; this directive alone is enough for the model
    to switch its reply.
    """
    parts = []

    # Response-language directive sits at the very top so the model sees
    # it before any other context. Cheap and effective.
    if language_directive:
        parts.append(language_directive)

    # Customer header
    parts.append(
        f"Customer: {customer.customer_id} | Plan: {customer.plan} | "
        f"Services: {', '.join(customer.services)}"
    )

    # History context
    if history.rolling_summary:
        parts.append(f"Conversation summary:\n{history.rolling_summary}")

    if history.unresolved_facts:
        parts.append(
            "Unresolved facts:\n" + "\n".join(f"- {f}" for f in history.unresolved_facts)
        )

    if history.recent_turns:
        turns_text = []
        for turn in history.recent_turns:
            role = turn.get("role", "unknown")
            content = turn.get("content", "")
            turns_text.append(f"{role}: {content}")
        parts.append("Recent conversation:\n" + "\n".join(turns_text))

    # Retrieved knowledge base context
    if retrieved_context:
        parts.append(f"Knowledge base context:\n{retrieved_context}")

    # Pre-fetched escalation contact for this specialist's domain. The
    # agent should only mention it when the customer explicitly asks for
    # a human, or when KB troubleshooting has been exhausted. See the
    # ESCALATION rules in the specialist's system prompt.
    if escalation_contact:
        contact_lines = [
            f"- {k}: {v}" for k, v in escalation_contact.items()
        ]
        parts.append(
            "Escalation contact for this domain (mention only per the "
            "ESCALATION rules in your system prompt — do not bring it up "
            "unsolicited):\n" + "\n".join(contact_lines)
        )

    # Current query
    parts.append(f"Customer question: {query}")

    return "\n\n".join(parts)
