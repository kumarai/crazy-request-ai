"""Appointment specialist agent — schedule, reschedule, cancel tech visits.

Read tools only here; bookings + cancellations commit through the
action endpoint after an explicit customer click.
"""
from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.support.agents.prompts.appointment import APPOINTMENT_SYSTEM_PROMPT
from app.support.agents.proposal_tools import record_proposal
from app.support.agents.support_agent import SupportAgentDeps
from app.support.tools._mcp_bridge import get_mcp, is_live

appointment_agent = Agent(
    model="openai:gpt-4o-mini",  # overridden via agent_model("appointment")
    output_type=str,
    system_prompt=APPOINTMENT_SYSTEM_PROMPT,
    deps_type=SupportAgentDeps,
)


async def _call_mcp(name: str, args: dict) -> dict:
    if not is_live():
        return {"error": "mcp_unavailable"}
    try:
        return await get_mcp().call_tool(name, args)
    except Exception as e:
        return {"error": str(e)}


@appointment_agent.tool
async def list_slots(
    ctx: RunContext[SupportAgentDeps],
    customer_id: str,
    topic: str,
    zip_code: str | None = None,
) -> dict:
    """Open appointment slots. ``topic`` ∈ install | tech_visit | tv_setup."""
    args: dict = {"customer_id": customer_id, "topic": topic}
    if zip_code:
        args["zip_code"] = zip_code
    result = await _call_mcp("appointment_list_slots", args)
    ctx.deps.tool_outputs.append(
        {"tool": "appointment_list_slots", "output": result}
    )
    return result


@appointment_agent.tool
async def list_appointments(
    ctx: RunContext[SupportAgentDeps],
    customer_id: str,
    include_past: bool = False,
) -> dict:
    """Return the customer's existing appointments.

    Default is upcoming + currently-booked only. Call with
    ``include_past=True`` when the customer asks about history.
    REQUIRED before proposing cancel or reschedule — the agent needs
    a real ``appointment_id`` and cannot invent one.
    """
    result = await _call_mcp(
        "appointment_list",
        {"customer_id": customer_id, "include_past": include_past},
    )
    ctx.deps.tool_outputs.append(
        {"tool": "appointment_list", "output": result}
    )
    return result


@appointment_agent.tool
async def propose_book_appointment(
    ctx: RunContext[SupportAgentDeps],
    slot_id: str,
    topic: str,
    slot_start: str,
    tech_name: str | None = None,
) -> dict:
    """Propose booking a specific slot. Customer confirms via button."""
    tech = f" (Tech: {tech_name})" if tech_name else ""
    label = f"Book {topic.replace('_', ' ')} — {slot_start}{tech}"
    return record_proposal(
        ctx.deps,
        kind="book_appointment",
        label=label,
        confirm_text=f"Book this {topic.replace('_', ' ')} slot?",
        payload={
            "slot_id": slot_id,
            "topic": topic,
            "slot_start": slot_start,
        },
    )


@appointment_agent.tool
async def propose_cancel_appointment(
    ctx: RunContext[SupportAgentDeps], appointment_id: str, slot_start: str
) -> dict:
    """Propose cancelling an existing appointment."""
    return record_proposal(
        ctx.deps,
        kind="cancel_appointment",
        label=f"Cancel appointment on {slot_start}",
        confirm_text=f"Cancel the appointment scheduled for {slot_start}?",
        payload={"appointment_id": appointment_id},
    )


@appointment_agent.tool
async def propose_reschedule_appointment(
    ctx: RunContext[SupportAgentDeps],
    appointment_id: str,
    new_slot_id: str,
    new_slot_start: str,
) -> dict:
    """Propose rescheduling an appointment to a new slot."""
    return record_proposal(
        ctx.deps,
        kind="reschedule_appointment",
        label=f"Reschedule to {new_slot_start}",
        confirm_text=f"Move the appointment to {new_slot_start}?",
        payload={
            "appointment_id": appointment_id,
            "new_slot_id": new_slot_id,
        },
    )
