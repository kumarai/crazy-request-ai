"""Support-specific followup question generator.

Generates customer-appropriate follow-up suggestions, not developer questions.
Categories: clarify, next_step, related_issue.
"""
from __future__ import annotations

from pydantic_ai import Agent

from app.support.agents.prompts import SUPPORT_FOLLOWUP_PROMPT

support_followup_agent = Agent(
    model="openai:gpt-4o-mini",  # overridden at .run() via agent_model
    output_type=str,
    system_prompt=SUPPORT_FOLLOWUP_PROMPT,
)


def build_followup_context(
    query: str,
    answer: str,
    history_summary: str | None,
) -> str:
    """Build the user message for followup generation."""
    parts = []

    if history_summary:
        parts.append(f"Conversation summary:\n{history_summary}")

    parts.append(f"Customer's question: {query}")
    parts.append(f"Assistant's response: {answer}")

    return "\n\n".join(parts)
