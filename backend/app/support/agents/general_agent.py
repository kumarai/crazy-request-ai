"""General Support specialist agent — catch-all for unclassified queries.

Read-only. Uses a narrow tool set: escalation contact only. The
orchestrator injects the customer header + KB context into the user
message, so the agent rarely needs to call tools to answer capability
or account-meta questions.
"""
from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.support.agents.prompts.general import GENERAL_SYSTEM_PROMPT
from app.support.agents.support_agent import SupportAgentDeps
from app.support.tools.registry import TOOL_REGISTRY

general_agent = Agent(
    model="openai:gpt-4o-mini",  # overridden via agent_model("general")
    output_type=str,
    system_prompt=GENERAL_SYSTEM_PROMPT,
    deps_type=SupportAgentDeps,
)


@general_agent.tool
async def get_recent_tickets(
    ctx: RunContext[SupportAgentDeps], customer_id: str
) -> dict:
    """Get recent support tickets for the customer."""
    result = await TOOL_REGISTRY["get_recent_tickets"].func(customer_id)
    ctx.deps.tool_outputs.append({"tool": "get_recent_tickets", "output": result})
    return result
