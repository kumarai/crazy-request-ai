from __future__ import annotations

from pydantic_ai import Agent

from app.agents.models import FaithfulnessResult

# Default model; overridden at .run() time via llm_client.agent_model("followup")
faithfulness_agent = Agent(
    model="openai:gpt-4o-mini",
    output_type=FaithfulnessResult,
    system_prompt="""\
Verify that a generated answer is faithful to its source context.

FAIL if the answer:
- Contains information not present in the provided context
- Makes implementation assumptions beyond what context shows
- Invents types, methods, base classes, or packages not in context

PASS only if every technical claim traces directly to the context.
Be strict. This is an internal tool where accuracy is critical.
""",
)
