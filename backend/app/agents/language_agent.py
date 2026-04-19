"""Language utilities for the support orchestrator.

Language *detection* lives in ``intent_agent.classify_intent`` — the
intent classifier's small-LLM call returns both the intent and the
language in one JSON object, so we don't pay for a separate detection
pass. This module just owns:

- ``language_directive(code)`` — the one-line "Respond in X." string
  the orchestrator prepends to every agent user message so replies
  match the customer's language. Specialist system prompts stay in
  English; this directive alone is enough for gpt-4o, claude-sonnet,
  and llama3.1 to flip output language.

- ``UNSUPPORTED_LANGUAGE_REPLY`` — canned bilingual rejection used
  when the intent classifier returns ``language="unsupported"``. No
  LLM call, zero tokens consumed.
"""
from __future__ import annotations

# Languages we have prompts and tooling for. Mirrors the values
# ``intent_agent.classify_intent`` may return for the ``language`` field
# (plus ``"unsupported"``, which is handled by the orchestrator's
# short-circuit path rather than by these helpers).
SUPPORTED_LANGUAGES = frozenset({"en", "es"})

# Display labels used in the directive line we append to user messages.
LANGUAGE_LABELS = {"en": "English", "es": "Spanish"}

# Bilingual reply when the intent classifier flags an unsupported
# language. Static — no LLM call, no tokens consumed.
UNSUPPORTED_LANGUAGE_REPLY = (
    "I can only help in English or Spanish.\n"
    "Solo puedo ayudar en inglés o español."
)


def language_directive(language_code: str) -> str:
    """Render the directive line we append to user messages so the
    downstream agent replies in the right language."""
    label = LANGUAGE_LABELS.get(language_code, "English")
    return f"Respond in {label}."
