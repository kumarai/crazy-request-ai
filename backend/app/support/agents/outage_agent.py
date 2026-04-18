"""Outage specialist agent with structured handoff output.

Unlike other specialists whose output is free text, this agent
returns a typed ``OutageOutput``: ``{reply, handoff_to, reason}``.
The orchestrator reads ``handoff_to`` directly to decide whether to
transfer to the technical specialist — no text-regex detection.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from app.support.agents.prompts.outage import OUTAGE_SYSTEM_PROMPT
from app.support.agents.support_agent import SupportAgentDeps
from app.support.tools.outage import (
    get_outage_by_zip,
    get_outage_for_customer,
    get_scheduled_maintenance,
)


class OutageOutput(BaseModel):
    reply: str
    handoff_to: Literal["technical"] | None = None
    reason: str | None = None


outage_agent = Agent(
    model="openai:gpt-4o-mini",  # overridden via agent_model("outage")
    output_type=OutageOutput,
    system_prompt=OUTAGE_SYSTEM_PROMPT,
    deps_type=SupportAgentDeps,
)


@outage_agent.tool
async def area_status_for_customer(
    ctx: RunContext[SupportAgentDeps], customer_id: str
) -> dict:
    """Look up outages affecting the authenticated customer."""
    result = await get_outage_for_customer(customer_id)
    ctx.deps.tool_outputs.append({"tool": "outage_area_status", "output": result})
    return result


@outage_agent.tool
async def area_status_by_zip(
    ctx: RunContext[SupportAgentDeps], zip_code: str
) -> dict:
    """Look up outages by zip code (used for guest customers)."""
    result = await get_outage_by_zip(zip_code)
    ctx.deps.tool_outputs.append(
        {"tool": "outage_area_status_zip", "output": result}
    )
    return result


@outage_agent.tool
async def scheduled_maintenance(
    ctx: RunContext[SupportAgentDeps], zip_code: str | None = None
) -> dict:
    """Upcoming scheduled maintenance in the area."""
    result = await get_scheduled_maintenance(zip_code)
    ctx.deps.tool_outputs.append(
        {"tool": "outage_scheduled_maintenance", "output": result}
    )
    return result
