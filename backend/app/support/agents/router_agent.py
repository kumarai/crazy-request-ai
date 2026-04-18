"""Router agent: classifies customer intent into 7 specialist domains.

Hard-rule pre-filters cover the clear-cut cases (billing keywords,
pay-intent verbs, appointment verbs, etc.), then LLM classification
picks between technical / general / outage / order. Low-confidence
classifications default to ``general`` (not technical) so that
service-operation questions the router is unsure about fall into a
safe catch-all instead of being misdiagnosed as connectivity issues.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.usage import RunUsage

logger = logging.getLogger("[support]")

# Hard-rule regexes — each maps to a single specialist. Order matters:
# more specific rules come first (bill_pay before generic billing).
_BILL_PAY_KEYWORDS = re.compile(
    r"\b(pay my bill|make a payment|pay (?:the )?balance|autopay|"
    r"enroll in autopay|set up autopay|add (?:a )?(?:credit )?card|"
    r"pay online|pay now)\b",
    re.IGNORECASE,
)

_BILLING_KEYWORDS = re.compile(
    r"\b(refund(?:s|ed|ing)?|invoice(?:s|d)?|bill(?:s|ed|ing)?|"
    r"charge(?:s|d)?|payment(?:s)?|balance(?:s)?|credit(?:s|ed)?|"
    r"overcharge(?:s|d)?|late fee(?:s)?|past due|statement(?:s)?)\b",
    re.IGNORECASE,
)

_APPOINTMENT_KEYWORDS = re.compile(
    r"\b(appointment|schedule(?:d)?|reschedul(?:e|ing)|book(?: a)?|"
    r"tech(?:nician)? visit|install(?:ation)? (?:date|appointment))\b",
    re.IGNORECASE,
)

_ORDER_KEYWORDS = re.compile(
    r"\b(order(?:s|ed|ing)?(?: status)?|shipment|tracking|"
    r"place(?: an)? order|buy|purchas(?:e|ing)|catalog|"
    r"new phone|new device|upgrade my phone|"
    r"where('?s)? my (?:phone|device|router))\b",
    re.IGNORECASE,
)

_OUTAGE_KEYWORDS = re.compile(
    r"\b(outage(?:s)?|service (?:down|out)|internet down|"
    r"everyone('?s)? (?:without|down|out)|is (?:the )?network down|"
    r"area (?:is )?down|no (?:service|signal) (?:in|around|near))\b",
    re.IGNORECASE,
)

# Explicit session-management phrases. These are unambiguous enough
# that even mid-conversation we'd rather snap to ``account`` than let
# the prior specialist try to handle them (bill_pay has no answer for
# "am I signed in", order has no answer for "log me out"). Unlike the
# other hard rules this one IS checked even when ``_has_active_context``
# is true — see ``route()`` below.
_ACCOUNT_KEYWORDS = re.compile(
    r"\b(log(?: me)? (?:in|out)|logged (?:in|out)|"
    r"sign(?: me)? (?:in|out)|signed (?:in|out)|"
    r"am i (?:signed|logged) in|who am i|"
    r"switch account|switch user|change user|"
    r"i(?:'m| am)? (?:already )?(?:signed|logged) in)\b",
    re.IGNORECASE,
)

# Confidence floor. Below this, default to ``general`` (catch-all),
# not technical. Keeps low-confidence classifications from driving
# users into the wrong specialist.
_CONFIDENCE_THRESHOLD = 0.75


class RouterDecision(BaseModel):
    specialist: str  # one of SPECIALIST_REGISTRY keys
    confidence: float
    handoff_payload: dict | None = None


@dataclass
class RouterDeps:
    customer_plan: str
    customer_services: list[str]


_ROUTER_SYSTEM_PROMPT = """\
You are an intent classifier for customer support. Given a customer's \
message and their account context, decide which specialist handles it.

Available specialists:

- "technical": Service issues (internet/TV/voice/mobile not working, \
slow, dropping), device problems (router, modem, phone, SIM), service \
operations (porting, activation, plan switching, unlock, cancel).
- "billing": Invoices, charges, balances, refunds, late fees, "why \
am I charged", billing-policy questions, COST questions about service \
operations.
- "bill_pay": INTENT TO PAY — "pay my bill", "autopay", "add card".
- "order": Order status, shipment tracking, buying a new device/plan, \
browsing the catalog.
- "appointment": Scheduling, rescheduling, or cancelling an install or \
tech visit.
- "outage": "is there an outage", "service down in my area", mass \
disruption questions.
- "account": Session-management questions — "am I signed in", "log me \
in / out", "I'm already logged in", "switch account", or any message \
where the customer is claiming / questioning their own login state.
- "general": Capability questions, general how-to, coverage areas, \
hours, anything that doesn't clearly fit the above.

KEY DISTINCTIONS:
- "billing" is INFO. "bill_pay" is ACTION. "How much do I owe?" → \
billing. "I want to pay my bill" → bill_pay.
- "technical" is diagnosis of an INDIVIDUAL service. "outage" is \
area-wide. "My wifi is slow" → technical. "Is there an outage in \
94107?" → outage.

CONVERSATION CONTINUITY (VERY IMPORTANT):
- You will often see a "Previous specialist" field. If the current \
message is a short reply, a piece of data the prior specialist just \
asked for (zip code, slot number, "yes", "no", "go ahead", a SKU, an \
amount, a date), or otherwise makes sense only as a continuation, \
ROUTE TO THE PREVIOUS SPECIALIST with high confidence (0.9+).
- Examples of continuation follow-ups that should stick with the \
previous specialist:
  • prior=appointment, msg="80015" → appointment (0.95)
  • prior=appointment, msg="yes, continue" → appointment (0.95)
  • prior=bill_pay, msg="use the Visa" → bill_pay (0.95)
  • prior=order, msg="the 256GB one" → order (0.95)
  • prior=order, msg="Selected payment method id pm_001. Continue." \
→ order (0.95)  [picking a payment method inside an order flow]
  • prior=order, msg="use the Visa" → order (0.95)  [the word "Visa" \
does NOT flip the specialist mid-flow; only a clear topic switch does]
  • prior=bill_pay, msg="Selected payment method id pm_001. Continue." \
→ bill_pay (0.95)
  • prior=appointment, msg="Selected slot id slot_42. Continue." → \
appointment (0.95)
- Only override the previous specialist when the customer clearly \
switched topics ("actually, can you check my bill?").
- If there is no conversation context and the query is genuinely \
ambiguous, lean toward "general" with lower confidence.

Respond with JSON only: \
{"specialist": "<name>", "confidence": 0.0-1.0}
"""

router_agent = Agent(
    model="openai:gpt-4o-mini",  # overridden at .run() via agent_model("router")
    output_type=str,
    system_prompt=_ROUTER_SYSTEM_PROMPT,
    deps_type=RouterDeps,
)


def apply_hard_rules(query: str) -> RouterDecision | None:
    """Apply keyword-based hard rules before LLM classification.

    Specific verbs first (pay, schedule, order, outage) so a message
    like "I want to pay my bill" matches ``bill_pay`` and never falls
    through to the broader billing regex. ``account`` is first so the
    session phrases ("log me out", "am I signed in") snap to the
    account specialist even when a topical specialist was active on
    the prior turn — see ``route()`` for the mid-conversation path.
    """
    if _ACCOUNT_KEYWORDS.search(query):
        return RouterDecision(specialist="account", confidence=1.0)
    if _BILL_PAY_KEYWORDS.search(query):
        return RouterDecision(specialist="bill_pay", confidence=1.0)
    if _APPOINTMENT_KEYWORDS.search(query):
        return RouterDecision(specialist="appointment", confidence=1.0)
    if _OUTAGE_KEYWORDS.search(query):
        return RouterDecision(specialist="outage", confidence=1.0)
    if _ORDER_KEYWORDS.search(query):
        return RouterDecision(specialist="order", confidence=1.0)
    if _BILLING_KEYWORDS.search(query):
        return RouterDecision(specialist="billing", confidence=1.0)
    return None


def _has_active_context(
    history_summary: str | None, last_specialist: str | None
) -> bool:
    """True when the conversation has meaningful prior state.

    Gates whether we use the cheap hard-rule regex or go straight to
    LLM classification. Mid-conversation we want the model to reason
    about intent from the summary, not snap to a keyword match.
    """
    if last_specialist:
        # Skip smalltalk/off_topic/summarize/capabilities pseudo-
        # specialists — they don't carry real topical context.
        return last_specialist not in {
            "smalltalk",
            "off_topic",
            "capabilities",
            "summarize",
            "unsupported_language",
        }
    return bool(history_summary and history_summary.strip())


async def route(
    query: str,
    customer_plan: str,
    customer_services: list[str],
    history_summary: str | None,
    last_specialist: str | None,
    model: str,
) -> tuple[RouterDecision, RunUsage]:
    """Route a customer query to the appropriate specialist.

    Returns the decision plus the LLM usage consumed by the classification
    call. On a cold start (no prior context) hard-rule regex handles the
    common cases cheaply; once the conversation is active we skip the
    regex and let the LLM reason about continuity from the summary plus
    ``last_specialist``. That prevents a keyword like "payment" mid-
    appointment flow from snapping the user out of the appointment
    specialist.
    """
    active_context = _has_active_context(history_summary, last_specialist)

    if not active_context:
        hard_result = apply_hard_rules(query)
        if hard_result:
            logger.info(
                "Cold-start hard-rule routed to %s (no prior context)",
                hard_result.specialist,
            )
            return hard_result, RunUsage()
    else:
        # Session-management phrases override active-context continuity:
        # "log me out" during an order flow must snap to ``account``
        # or the prior specialist (which has no session tools) will
        # try to answer it.
        if _ACCOUNT_KEYWORDS.search(query):
            logger.info(
                "Account keyword match mid-conversation (prior=%s) — "
                "snapping to account specialist",
                last_specialist,
            )
            return (
                RouterDecision(specialist="account", confidence=1.0),
                RunUsage(),
            )
        logger.info(
            "Active conversation — skipping hard rules, using LLM with "
            "last_specialist=%s",
            last_specialist,
        )

    context_parts = [
        f"Customer plan: {customer_plan}",
        f"Services: {', '.join(customer_services)}",
    ]
    if history_summary:
        context_parts.append(f"Conversation summary: {history_summary}")
    if last_specialist:
        context_parts.append(f"Previous specialist: {last_specialist}")

    context_parts.append(f"Customer message: {query}")
    user_message = "\n".join(context_parts)

    deps = RouterDeps(
        customer_plan=customer_plan,
        customer_services=customer_services,
    )

    try:
        result = await router_agent.run(user_message, model=model, deps=deps)
        output = result.output
        usage = result.usage()

        try:
            parsed = json.loads(output)
            specialist = parsed.get("specialist", "general")
            confidence = float(parsed.get("confidence", 0.5))
        except (json.JSONDecodeError, ValueError):
            specialist = "general"
            confidence = 0.4

        # Low-confidence fallback. If we have a ``last_specialist``,
        # stick with it — a short follow-up ("80015", "yes, continue",
        # "use the Visa") reads as low-confidence in isolation but is
        # a clear continuation. Dropping to ``general`` breaks the
        # flow and forces the customer to re-explain themselves.
        if confidence < _CONFIDENCE_THRESHOLD:
            if last_specialist:
                logger.info(
                    "Low confidence %.2f for '%s' — sticking with prior "
                    "specialist %s (continuation)",
                    confidence,
                    specialist,
                    last_specialist,
                )
                specialist = last_specialist
                # Raise confidence slightly so the frontend badge
                # reflects that the choice is the continuation rule,
                # not a weak LLM guess.
                confidence = max(confidence, 0.8)
            else:
                logger.info(
                    "Low confidence %.2f for '%s' — defaulting to general",
                    confidence,
                    specialist,
                )
                specialist = "general"

        return RouterDecision(specialist=specialist, confidence=confidence), usage

    except Exception as e:
        logger.error("Router agent failed: %s", e)
        # Same continuity rule on failure — better to retry with the
        # prior specialist than to drop into the catch-all.
        if last_specialist:
            return RouterDecision(specialist=last_specialist, confidence=0.5), RunUsage()
        return RouterDecision(specialist="general", confidence=0.0), RunUsage()
