from __future__ import annotations

from pydantic_ai import Agent

from app.agents.models import FollowupResult

# Default model; overridden at .run() time via llm_client.agent_model("followup")
followup_agent = Agent(
    model="openai:gpt-4o-mini",
    output_type=FollowupResult,
    system_prompt="""\
Generate follow-up questions a developer should ask next about their codebase.

Categories:
- dig_deeper: deeper into the same topic
  e.g. "How does PaymentService handle idempotency for duplicate requests?"
- adjacent_concern: related things needed next
  e.g. "Are there existing integration tests for the Stripe webhook handler?"
- architecture: higher-level design questions
  e.g. "Should SubscriptionService extend BaseService or compose it?"

Rules:
- Questions must be answerable from the indexed codebase (not general)
- Reference specific class/file names from the retrieved context
- Phrase exactly as a developer would type them
- Always return a mix of all three categories
- Return exactly 3-5 questions total
""",
)
