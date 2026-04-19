"""Intent + language classifier shared by every orchestrator.

The same LLM call returns BOTH the intent and the customer's language
(English / Spanish / unsupported). This replaces the earlier separate
``detect_language`` step that used ``langdetect`` — modern small models
are dramatically more accurate at language ID, especially for typo'd
English ("Help m pay bil paymen") or short Spanish ("Hola amigo")
that langdetect's character n-gram model misclassified routinely.

Two-stage cascade:
1. Hard-rule regex catches obvious English smalltalk ("hi", "thanks",
   "bye") and trivial acks. Free, deterministic, runs on every turn.
   Hard-rule matches always tag ``language="en"`` — the patterns are
   English-only. Spanish smalltalk ("hola", "gracias") falls through
   to the LLM, which classifies both fields correctly.
2. Small-LLM fallback for everything else: returns
   ``{intent, language, confidence}`` in one JSON object.

Intents (handled per-domain by the calling orchestrator):
- ``smalltalk``: greeting, gratitude, farewell, ack, polite chitchat.
  Caller streams a warm short reply via ``smalltalk_agent``.
- ``off_topic``: real question but outside the assistant's scope (weather,
  sports, jokes, general knowledge, recipes). Caller streams a gentle
  "I can't help with that, but I can help with X" via
  ``support_off_topic_agent``.
- ``capabilities``: meta question about what the assistant can do
  ("what can you help with?", "what are your capabilities?"). Caller
  streams a static capabilities summary — no LLM, no retrieval — so
  the user gets a fast, accurate list of what's supported instead of
  the agent trying to invent one from a tool call.
- ``summarize``: meta request for a recap of the current conversation
  ("summarize our chat", "recap what we discussed"). Caller loads the
  chat history and streams a summary via ``summarize_agent`` — no
  retrieval, the history itself is the grounding.
- ``support``: in-scope question. Caller runs the full RAG pipeline.

Languages:
- ``en``: English (default).
- ``es``: Spanish.
- ``unsupported``: any other language. The support orchestrator
  short-circuits with a bilingual rejection.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.usage import RunUsage

logger = logging.getLogger("[intent]")

Intent = str  # "smalltalk" | "off_topic" | "capabilities" | "summarize" | "support"
_VALID_INTENTS = {"smalltalk", "off_topic", "capabilities", "summarize", "support"}

Language = str  # "en" | "es" | "unsupported"
_VALID_LANGUAGES = {"en", "es", "unsupported"}

# Confidence floor for short-circuiting the full RAG pipeline.
#
# Misclassifying a real support question as smalltalk/off_topic makes the
# user see a generic "I can't help" reply instead of a real answer — a
# painful failure mode. Misclassifying smalltalk as support just runs the
# full pipeline and burns a few extra tokens on a cheap message — minor.
#
# So we only honour LLM-classified non-support intents above this floor;
# below it, fall back to "support" and run the pipeline. The hard-rule
# regex bypasses this floor (it returns confidence=1.0).
_NON_SUPPORT_CONFIDENCE_FLOOR = 0.75

# Short, self-contained smalltalk. Keep tight to avoid swallowing
# greetings that are followed by a real question.
_SMALLTALK_PATTERNS = [
    r"^\s*(hi|hello|hey|hiya|yo|howdy|sup)[\s!.?]*$",
    r"^\s*good\s+(morning|afternoon|evening|night)[\s!.?]*$",
    r"^\s*(thanks|thank\s*you|thx|ty|cheers|appreciate(d)?( it)?)[\s!.?]*$",
    r"^\s*(bye|goodbye|see\s*ya|see\s*you|later|cya)[\s!.?]*$",
    r"^\s*(ok|okay|cool|got it|sounds good|great|nice|awesome|perfect)[\s!.?]*$",
    r"^\s*(how\s+are\s+you|how's\s+it\s+going|what's\s+up|whats\s+up)[\s!.?]*$",
]
_SMALLTALK_RE = re.compile("|".join(_SMALLTALK_PATTERNS), re.IGNORECASE)

# Anything beyond this length is almost certainly substantive — skip the
# regex check entirely (saves a few cycles, and avoids false positives on
# long messages that happen to start with "hi").
_MAX_SMALLTALK_LEN = 40


class IntentDecision(BaseModel):
    intent: Intent
    language: Language
    confidence: float
    source: str  # "hard_rule" | "llm" | "fallback"


@dataclass
class IntentDeps:
    pass


_INTENT_SYSTEM_PROMPT = """\
You are an intent + language classifier for a customer-support / \
knowledge-base chat. Classify the user's message into exactly one \
intent category AND identify the language.

INTENT (choose exactly one):

DEFAULT BIAS: when in doubt about intent, choose "support". The cost of \
misclassifying a real question as smalltalk/off_topic (the user gets a \
generic "I can't help" reply) is much higher than the cost of running \
the full RAG pipeline on a borderline message.

- "smalltalk": ONLY greetings, gratitude, farewells, acknowledgements, \
or polite chitchat directed at the assistant. The user is being social, \
not asking for information.
  Examples: "hi", "hello", "thanks!", "bye", "ok got it", "how are you", \
"what's your name", "you're awesome", "hola", "gracias", "adiós".

- "off_topic": a real question or request that is CLEARLY outside the \
assistant's scope (customer-service for telecom: internet, TV, voice, \
mobile, devices, billing, payments, account; OR the indexed knowledge \
base). Use this ONLY when the topic is unambiguously unrelated.
  Examples: "what's the weather today", "tell me a joke", "who won the \
game last night", "what's a good pasta recipe", "what's the capital of \
France", "write me a poem", "stock price of AAPL".

- "capabilities": the user is asking META about what THIS assistant can \
do — not asking for a specific action, but for a summary of capabilities.
  Examples: "what can you do", "what can you help with", "is there \
anything you can help me with", "how can you help me", "what are you \
for", "tell me what you can do", "what kinds of things can you help \
with", "what's your role", "how does this work", "what are your \
capabilities".
  Note: a bare "Can you help me?" alone is too vague — classify as \
"smalltalk" if standalone, or "support" if followed by a real question \
(e.g. "Can you help me with my bill?" → support).

- "summarize": the user is asking for a recap of the current \
CONVERSATION — what we've discussed so far in this chat. The target is \
the chat history itself, not telecom knowledge.
  Examples: "summarize our chat", "can you summarize this conversation", \
"recap what we talked about", "give me a summary of what we've \
discussed", "tl;dr of this chat", "resume nuestra conversación", \
"resume lo que hemos hablado".
  Do NOT use for "summarize my bill" or "summarize the outage" — those \
are "support" (asking to summarize a domain fact, not the chat).

- "support": ANY question or request that could plausibly be about the \
user's telecom service or account. Phrasing varies — both "I need to X" \
and "How can I X", "How do I X", "What's the process for X", "Can you \
help me X" all count as support if X is in-scope. Even short or \
abstract-sounding asks, AND messages with typos, count when the topic \
is in-scope.
  Examples: "wifi is down", "refund?", "why was I charged extra", \
"how do I reset my router", "is there an outage in my area", \
"how can I port my number", "I want to port my number to your service", \
"how do I cancel my plan", "what plans do you offer", "can I upgrade", \
"how do I switch providers", "transfer my service", "activate my SIM", \
"unlock my phone", "where's my installation appointment", \
"Help m pay bil paymen" (typo'd English — still support).

Mixed messages with a real in-scope ask (e.g. "hi, can you help with my \
bill?") are "support". Mixed messages with an off-scope ask (e.g. "hi, \
what's the weather?") are "off_topic".

If the topic could reasonably be telecom-related (porting numbers, plans, \
service transfers, devices, account changes), choose "support" — even \
when the user phrases it as a polite info-request.

LANGUAGE (choose exactly one):

- "en": English. Includes typo'd, abbreviated, or text-speak English \
("how r u", "wifi not workng", "cant log in 2 my acct"). Be generous: \
short messages ("hi", "ok", "thx") are English by default.
- "es": Spanish. Includes Spanish without proper punctuation ("Como puedo \
pagar mi factura"), Spanish with English-borrowed terms ("¿Cómo activo \
mi SIM?"), and short Spanish phrases ("hola amigo", "gracias por la \
ayuda").
- "unsupported": any language other than English or Spanish (French, \
German, Portuguese, Chinese, Arabic, etc.). Be confident — only mark \
"unsupported" when you're sure the message is in a non-EN/ES language. \
When uncertain, default to "en".

Respond with JSON only:
{"intent": "smalltalk"|"off_topic"|"capabilities"|"summarize"|"support", "language": "en"|"es"|"unsupported", "confidence": 0.0-1.0}

Confidence reflects how certain you are about the INTENT. Use < 0.7 when \
the message is ambiguous; the orchestrator treats low-confidence \
non-support intents as support to be safe.
"""

intent_agent = Agent(
    model="openai:gpt-4o-mini",  # overridden at .run() via agent_model("intent")
    output_type=str,
    system_prompt=_INTENT_SYSTEM_PROMPT,
    deps_type=IntentDeps,
)


def apply_hard_rules(query: str) -> IntentDecision | None:
    """Match obvious smalltalk via regex. Returns None if ambiguous.

    The regex patterns are English-only, so all hard-rule matches tag
    ``language="en"``. Spanish smalltalk ("hola", "gracias") falls
    through to the LLM, which classifies both fields correctly.
    """
    stripped = query.strip()
    if not stripped:
        # Empty/whitespace-only — treat as smalltalk to short-circuit.
        return IntentDecision(
            intent="smalltalk", language="en", confidence=1.0, source="hard_rule"
        )
    if len(stripped) > _MAX_SMALLTALK_LEN:
        return None
    if _SMALLTALK_RE.match(stripped):
        return IntentDecision(
            intent="smalltalk", language="en", confidence=1.0, source="hard_rule"
        )
    return None


async def classify_intent(
    query: str,
    model: str,
) -> tuple[IntentDecision, RunUsage]:
    """Classify a user message as smalltalk vs. off_topic vs. support.

    Hard-rule matches return zero-usage ``RunUsage``. LLM fallback
    reports its real usage so the orchestrator can roll it into the
    turn's token/cost totals.
    """
    hard = apply_hard_rules(query)
    if hard:
        logger.info("Intent hard-rule: %s", hard.intent)
        return hard, RunUsage()

    try:
        result = await intent_agent.run(query, model=model, deps=IntentDeps())
        usage = result.usage()
        try:
            parsed = json.loads(result.output)
            intent = parsed.get("intent", "support")
            language = parsed.get("language", "en")
            confidence = float(parsed.get("confidence", 0.5))
        except (json.JSONDecodeError, ValueError, TypeError):
            # Couldn't parse — be safe: run the full pipeline in English.
            return (
                IntentDecision(
                    intent="support",
                    language="en",
                    confidence=0.0,
                    source="fallback",
                ),
                usage,
            )

        if intent not in _VALID_INTENTS:
            intent = "support"
        if language not in _VALID_LANGUAGES:
            language = "en"

        # Confidence floor: a tentative non-support classification is
        # downgraded to support so borderline messages still get the full
        # pipeline. Phrasings like "How can I port my number?" trigger
        # this — the small classifier often returns smalltalk/off_topic
        # at ~0.5-0.7 when the wording sounds politely abstract. Note we
        # only downgrade INTENT — language stays as classified.
        if (
            intent in ("smalltalk", "off_topic", "capabilities", "summarize")
            and confidence < _NON_SUPPORT_CONFIDENCE_FLOOR
        ):
            logger.info(
                "Intent %s confidence %.2f < %.2f — downgrading to support",
                intent,
                confidence,
                _NON_SUPPORT_CONFIDENCE_FLOOR,
            )
            return (
                IntentDecision(
                    intent="support",
                    language=language,
                    confidence=confidence,
                    source="llm_low_conf",
                ),
                usage,
            )

        return (
            IntentDecision(
                intent=intent,
                language=language,
                confidence=confidence,
                source="llm",
            ),
            usage,
        )
    except Exception as e:
        logger.error("Intent classifier failed: %s — defaulting to support/en", e)
        return (
            IntentDecision(
                intent="support",
                language="en",
                confidence=0.0,
                source="fallback",
            ),
            RunUsage(),
        )
