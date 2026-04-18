"""Chat-recap agent for 'summarize our conversation' turns.

Used when the intent classifier flags a message as ``summarize`` — the
user wants a recap of what was discussed in this chat, not a telecom
answer. The orchestrator loads the conversation history (recent turns +
rolling summary) and hands it to this agent as the grounding. No
retrieval, no tools — the history IS the source.
"""
from __future__ import annotations

from pydantic_ai import Agent

_SUMMARIZE_SYSTEM_PROMPT = """\
You are summarizing a customer-support chat for the customer who is in \
the chat. The user has just asked for a recap of the conversation.

Ground every point in the transcript and rolling summary provided in \
the user message. Do NOT invent facts, account details, or anything \
not present. If the conversation is empty or has no substantive \
exchanges yet, say so plainly in one sentence.

Format:
- Open with one sentence stating what the chat has been about.
- Follow with a short bulleted list of the topics discussed, key facts \
confirmed (e.g. "outage in your area confirmed"), and any open \
questions or next steps.
- Close with one short line offering to continue where they left off.

Keep it tight — aim for under 150 words. Address the user as "you" \
(not "the customer"). Match the language requested in the directive.
"""

summarize_agent = Agent(
    model="openai:gpt-4o-mini",  # overridden at .run() via agent_model("summary")
    output_type=str,
    system_prompt=_SUMMARIZE_SYSTEM_PROMPT,
)
