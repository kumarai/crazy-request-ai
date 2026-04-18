"""Devchat-flavored off-topic redirect agent.

Used by ``DevChatOrchestrator`` when the intent classifier flags a
message as ``off_topic`` (weather, sports, jokes, recipes, general
knowledge). The reply gently declines and points the user back at the
indexed knowledge base — the only thing this assistant can answer
faithfully.

Mirrors ``app.support.agents.off_topic_agent`` but with developer-RAG
scope copy ("indexed sources / code / wiki") instead of customer-support
scope ("internet, TV, billing").
"""
from __future__ import annotations

from pydantic_ai import Agent

_OFF_TOPIC_SYSTEM_PROMPT = """\
You are a developer assistant grounded in an indexed knowledge base \
(source code, wiki, docs). The user just asked something outside that \
scope (e.g. weather, sports, jokes, general knowledge, cooking, world \
news). You should NOT try to answer it.

Reply in 1-2 short sentences:
1. Politely acknowledge you can't help with that topic.
2. Briefly remind them what you CAN help with: questions about the \
indexed source code, documentation, and wiki content.

Be warm, not robotic. Don't apologise excessively. Don't invent facts \
about the off-topic question. Don't promise to look it up.
"""

devchat_off_topic_agent = Agent(
    model="openai:gpt-4o-mini",  # overridden at .run() via agent_model("smalltalk")
    output_type=str,
    system_prompt=_OFF_TOPIC_SYSTEM_PROMPT,
)
