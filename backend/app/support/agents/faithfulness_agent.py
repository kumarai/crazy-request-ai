"""Support-specific faithfulness checker.

Separate from the code-assistant faithfulness agent. Checks against
retrieved chunks, tool outputs, AND conversation history so cross-turn
claims are verifiable.
"""
from __future__ import annotations

from pydantic_ai import Agent

from app.support.agents.prompts import SUPPORT_FAITHFULNESS_PROMPT

support_faithfulness_agent = Agent(
    model="openai:gpt-4o-mini",  # overridden at .run() via agent_model
    output_type=str,
    system_prompt=SUPPORT_FAITHFULNESS_PROMPT,
)


def build_faithfulness_context(
    answer: str,
    retrieved_chunks: str,
    tool_outputs: list[dict],
    history_summary: str | None,
    recent_turns: list[dict],
) -> str:
    """Build the user message for faithfulness checking."""
    parts = []

    if retrieved_chunks:
        parts.append(f"Retrieved knowledge base chunks:\n{retrieved_chunks}")

    if tool_outputs:
        tool_text = "\n".join(
            f"- {t['tool']}: {t['output']}" for t in tool_outputs
        )
        parts.append(f"Tool outputs this turn:\n{tool_text}")

    if history_summary:
        parts.append(f"Conversation summary:\n{history_summary}")

    if recent_turns:
        turns_text = "\n".join(
            f"{t.get('role', 'unknown')}: {t.get('content', '')}"
            for t in recent_turns
        )
        parts.append(f"Recent conversation turns:\n{turns_text}")

    parts.append(f"Assistant's response to check:\n{answer}")

    return "\n\n".join(parts)
