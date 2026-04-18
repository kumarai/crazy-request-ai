"""Cheap conversational agent for smalltalk turns.

Used when the intent classifier flags a message as smalltalk (greeting,
thanks, goodbye, ack, chitchat). Skips retrieval and any validation —
the goal is a short, warm reply.

Domain-neutral: every orchestrator (devchat, support) shares this same
agent. The off-topic agents stay per-domain because their redirect copy
is scope-specific, but a friendly "hi back" reads the same everywhere.
"""
from __future__ import annotations

from pydantic_ai import Agent

_SMALLTALK_SYSTEM_PROMPT = """\
You are a friendly assistant. The user just sent a short conversational \
message — a greeting, thanks, goodbye, or small chitchat — not a real \
question.

Reply in 1-2 short sentences:
- Greetings: warmly greet back and offer to help.
- Thanks: acknowledge briefly and offer further help.
- Goodbye: warm sign-off, invite them back anytime.
- Chitchat ("how are you"): brief friendly reply, then ask what you can \
help with.

Never invent facts, account details, or knowledge-base content. Keep it \
human and short.
"""

smalltalk_agent = Agent(
    model="openai:gpt-4o-mini",  # overridden at .run() via agent_model("smalltalk")
    output_type=str,
    system_prompt=_SMALLTALK_SYSTEM_PROMPT,
)
