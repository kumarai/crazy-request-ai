"""Post-response validation agents for the support orchestrator.

Two focused agents run after support-generation:

- ``support_faithfulness_agent`` — verifies the response is grounded in
  retrieved chunks + tool outputs + history. Heavy context.
- ``support_followups_actions_agent`` — generates 2-3 follow-up
  questions and selects 0-3 action-link topics from the catalog. Light
  context (just query + answer + history).

Faithfulness is skipped only for obviously safe non-answers such as a
pure greeting or the canned "not in our knowledge base" refusal. Real
support replies, including short troubleshooting instructions, are
verified by default.
"""
from __future__ import annotations

from pydantic_ai import Agent

from app.support.action_catalog import ACTION_CATALOG


# ---------------------------------------------------------------------
# Faithfulness agent (heavy: includes retrieved chunks + tool outputs)
# ---------------------------------------------------------------------

_FAITHFULNESS_SYSTEM_PROMPT = """\
You are a strict faithfulness checker for customer-support responses. \
Verify that EVERY factual claim in the assistant's response is \
DIRECTLY traceable to a specific sentence in the provided context \
(retrieved knowledge base chunks, tool outputs, or prior conversation \
turns).

STRICT CRITERIA — apply with NO benefit of the doubt:
- Any factual claim that cannot be matched to a specific source \
sentence is UNFAITHFUL. "Sounds reasonable" or "common knowledge" is \
not a valid justification.
- Inferred conclusions, paraphrased extrapolations, or "general \
knowledge" framing are UNFAITHFUL even if the inference seems sound.
- Any specific number, dollar amount, date, ticket number, plan name, \
device model, address, phone number, or policy detail in the response \
that does NOT appear in the context is UNFAITHFUL.
- Procedural steps that don't appear (or paraphrase to) steps in the \
knowledge base are UNFAITHFUL.
- Promises or commitments to actions the agent cannot perform are \
UNFAITHFUL.

When the response makes no factual claims (a greeting, acknowledgement, \
empathy line, or polite refusal pointing to escalation), it is \
``"faithful": true``.

Respond with JSON only, no prose, no code fences:
{"faithful": true|false}
"""

support_faithfulness_agent = Agent(
    model="openai:gpt-4o-mini",  # overridden at .run() via agent_model
    output_type=str,
    system_prompt=_FAITHFULNESS_SYSTEM_PROMPT,
)


def build_faithfulness_context(
    *,
    query: str,
    answer: str,
    retrieved_chunks: str,
    tool_outputs: list[dict],
    history_summary: str | None,
    recent_turns: list[dict] | None,
) -> str:
    """Build the user message for the faithfulness check."""
    parts: list[str] = []
    parts.append(f"Customer's question: {query}")
    parts.append(f"Assistant's response to verify: {answer}")
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

    return "\n\n".join(parts)


# ---------------------------------------------------------------------
# Followups + Actions agent (light: just query + answer + history)
# ---------------------------------------------------------------------

_FOLLOWUPS_ACTIONS_SYSTEM_PROMPT = """\
You generate two things for a customer-support response:

TASK 1 — Follow-up suggestions.
2-3 natural follow-up questions the customer might want to ask next. \
Each has a category:
- "clarify": ask for more detail about something in the response.
- "next_step": a logical next action the customer might want to take.
- "related_issue": an adjacent concern they might have.
If nothing useful comes to mind, return an empty array.

TASK 2 — Action-link topics.
Select 0-3 action topics from the ALLOWED LIST below to surface as \
buttons. Pick topics that are genuinely useful given the question and \
answer; skip topics that are unrelated or redundant. ONLY return topics \
from the allowed list — any other string is invalid and will be \
dropped. Order by relevance (most relevant first).

ALLOWED ACTION TOPICS:
{action_topics_block}

Respond with JSON only, no prose, no code fences:
{{
  "followups": [
    {{"question": "...", "category": "clarify"}},
    {{"question": "...", "category": "next_step"}}
  ],
  "action_topics": ["billing.pay"]
}}
"""


def _action_topics_block() -> str:
    """Render the allowed-topics list for the system prompt."""
    return "\n".join(
        f'- "{topic}": {link.label}' for topic, link in ACTION_CATALOG.items()
    )


support_followups_actions_agent = Agent(
    model="openai:gpt-4o-mini",  # overridden at .run() via agent_model
    output_type=str,
    system_prompt=_FOLLOWUPS_ACTIONS_SYSTEM_PROMPT.format(
        action_topics_block=_action_topics_block()
    ),
)


def build_followups_actions_context(
    *,
    query: str,
    answer: str,
    history_summary: str | None,
) -> str:
    """Build the user message for the followups+actions call."""
    parts: list[str] = []
    parts.append(f"Customer's question: {query}")
    parts.append(f"Assistant's response: {answer}")

    if history_summary:
        parts.append(f"Conversation summary:\n{history_summary}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------
# Verifiability heuristic (decides whether to skip faithfulness call)
# ---------------------------------------------------------------------

_SAFE_NON_ANSWER_PREFIXES = (
    "i don't have specific information about that in our knowledge base.",
    "i can't give a grounded answer for that from our knowledge base.",
    "no tengo información específica sobre eso en nuestra base de conocimientos.",
    "no puedo darte una respuesta fundamentada sobre eso desde nuestra base de conocimientos.",
)


def _normalize_text(text: str) -> str:
    return " ".join(text.lower().split())


def is_verifiable_response(response: str) -> bool:
    """Return ``True`` when a support reply should go through faithfulness.

    Support answers are high-stakes enough that we verify almost
    everything. The only safe skips are obvious non-answers such as the
    canned KB refusal, greetings, and thanks.
    """
    stripped = response.strip()
    if not stripped:
        return False

    normalized = _normalize_text(stripped)

    if any(normalized.startswith(prefix) for prefix in _SAFE_NON_ANSWER_PREFIXES):
        return False

    if normalized in {
        "hi",
        "hello",
        "hey",
        "thanks",
        "thank you",
        "you're welcome",
        "did that help?",
        "let me know if you need anything else.",
    }:
        return False

    return True
