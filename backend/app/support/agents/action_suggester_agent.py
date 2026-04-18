"""Suggest action-link topics to surface as buttons after a support response.

Parallel to the followup agent. Runs on (query, answer, history) and returns
topic keys from the action catalog. Topics — not URLs — so the LLM cannot
hallucinate links. The orchestrator resolves topics → ``ActionLink`` via the
catalog before streaming the SSE event.
"""
from __future__ import annotations

from pydantic_ai import Agent

from app.support.action_catalog import ACTION_CATALOG
from app.support.agents.prompts import SUPPORT_ACTION_SUGGESTER_PROMPT

support_action_suggester_agent = Agent(
    model="openai:gpt-4o-mini",  # overridden at .run() via agent_model
    output_type=str,
    system_prompt=SUPPORT_ACTION_SUGGESTER_PROMPT,
)


def _catalog_listing() -> str:
    """Render the allowed topics block for the prompt context."""
    lines = []
    for topic, link in ACTION_CATALOG.items():
        lines.append(f'- "{topic}": {link.label}')
    return "\n".join(lines)


def build_action_suggester_context(
    query: str,
    answer: str,
    history_summary: str | None,
) -> str:
    """Build the user message passed to the suggester agent."""
    parts = []

    if history_summary:
        parts.append(f"Conversation summary:\n{history_summary}")

    parts.append(f"Customer's question: {query}")
    parts.append(f"Assistant's response: {answer}")
    parts.append("Allowed topics:\n" + _catalog_listing())

    return "\n\n".join(parts)
