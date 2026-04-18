"""Technical Support specialist agent.

Handles internet, TV, voice, mobile, device, and connectivity issues.
Tools: service details, devices, outages, tickets, escalation.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import Agent, RunContext

from app.support.agents.prompts.technical import TECHNICAL_SYSTEM_PROMPT
from app.support.agents.support_agent import SupportAgentDeps
from app.support.tools.registry import TOOL_REGISTRY

technical_agent = Agent(
    model="openai:gpt-4o",  # overridden at .run() via agent_model("technical")
    output_type=str,
    system_prompt=TECHNICAL_SYSTEM_PROMPT,
    deps_type=SupportAgentDeps,
)


# Register technical-domain + shared tools
@technical_agent.tool
async def voice_get_details(ctx: RunContext[SupportAgentDeps], customer_id: str) -> dict:
    """Get voice service details for the customer."""
    result = await TOOL_REGISTRY["voice_get_details"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "voice_get_details", "output": result})
    return result


@technical_agent.tool
async def mobile_get_details(ctx: RunContext[SupportAgentDeps], customer_id: str) -> dict:
    """Get mobile service details for the customer."""
    result = await TOOL_REGISTRY["mobile_get_details"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "mobile_get_details", "output": result})
    return result


@technical_agent.tool
async def internet_get_details(ctx: RunContext[SupportAgentDeps], customer_id: str) -> dict:
    """Get internet service details for the customer."""
    result = await TOOL_REGISTRY["internet_get_details"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "internet_get_details", "output": result})
    return result


@technical_agent.tool
async def tv_get_details(ctx: RunContext[SupportAgentDeps], customer_id: str) -> dict:
    """Get TV service details for the customer."""
    result = await TOOL_REGISTRY["tv_get_details"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "tv_get_details", "output": result})
    return result


@technical_agent.tool
async def list_devices(ctx: RunContext[SupportAgentDeps], customer_id: str) -> dict:
    """List all devices on the customer account."""
    result = await TOOL_REGISTRY["list_devices"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "list_devices", "output": result})
    return result


@technical_agent.tool
async def get_device(ctx: RunContext[SupportAgentDeps], device_id: str) -> dict:
    """Get detailed info for a specific device."""
    result = await TOOL_REGISTRY["get_device"].func(device_id)
    ctx.deps.tool_outputs.append({"tool": "get_device", "output": result})
    return result


@technical_agent.tool
async def get_outage_for_customer(ctx: RunContext[SupportAgentDeps], customer_id: str) -> dict:
    """Check for active outages affecting the customer."""
    result = await TOOL_REGISTRY["get_outage_for_customer"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "get_outage_for_customer", "output": result})
    return result


@technical_agent.tool
async def get_recent_tickets(ctx: RunContext[SupportAgentDeps], customer_id: str) -> dict:
    """Get recent support tickets for the customer."""
    result = await TOOL_REGISTRY["get_recent_tickets"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "get_recent_tickets", "output": result})
    return result


# NOTE: ``get_escalation_contact`` is intentionally NOT registered as a
# tool here. The orchestrator injects the relevant escalation contact
# directly into the agent's user message context (see
# ``build_user_message``). This avoids a wasted tool round-trip (~1.5-2K
# tokens) on every turn the agent decides to mention escalation, and
# stops the agent from fishing for an excuse to call the tool on routine
# how-to questions.
