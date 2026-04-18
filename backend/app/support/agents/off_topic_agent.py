"""Cheap redirect agent for out-of-scope questions.

Used when the intent classifier flags a message as ``off_topic`` (weather,
sports, jokes, recipes, general knowledge). Skips retrieval entirely —
the goal is a brief, friendly decline that points the user back at what
the assistant can actually help with.
"""
from __future__ import annotations

from pydantic_ai import Agent

_OFF_TOPIC_SYSTEM_PROMPT = """\
You are a customer-support assistant. The user just asked something \
outside your scope (e.g. weather, sports, jokes, general knowledge, \
cooking, world news). You should NOT try to answer it.

Reply in 1-2 short sentences:
1. Politely acknowledge you can't help with that topic.
2. Briefly remind them what you CAN help with: their internet, TV, \
voice, mobile service, devices, billing, or account questions.

Be warm, not robotic. Don't apologise excessively. Don't invent facts \
about the off-topic question. Don't promise to look it up.
"""

support_off_topic_agent = Agent(
    model="openai:gpt-4o-mini",  # overridden at .run() via agent_model("smalltalk")
    output_type=str,
    system_prompt=_OFF_TOPIC_SYSTEM_PROMPT,
)
