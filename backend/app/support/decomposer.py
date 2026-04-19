"""LLM-driven query decomposer for the debug-mode parallel orchestrator.

Splits a compound customer query (e.g. "fix my internet after my card
got rejected and I paid late") into up to 3 focused sub-queries, each
tagged with the specialist that should handle it.

Simple queries decompose to a single sub-query so the caller can treat
the single- and multi-branch cases uniformly.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.usage import RunUsage

from app.support.agents.registry import SPECIALIST_REGISTRY

logger = logging.getLogger("[support]")

MAX_SUB_QUERIES = 3


class SubQuery(BaseModel):
    sub_query: str
    specialist: str  # must be a key in SPECIALIST_REGISTRY
    rationale: str


class Decomposition(BaseModel):
    sub_queries: list[SubQuery]


@dataclass
class DecomposerDeps:
    pass


_DECOMPOSER_SYSTEM_PROMPT = """\
You decompose a customer-support query into independent sub-queries, \
each routed to a single specialist.

AVAILABLE SPECIALISTS:
- "technical": service issues (internet/TV/voice/mobile not working), \
device troubleshooting, activation/porting/plan switches.
- "billing": invoices, charges, balances, refunds, late fees, \
"why was I charged", cost questions.
- "bill_pay": intent to PAY — "pay my bill", autopay, add card, card \
rejection / payment failure.
- "order": order status, shipment tracking, buying a new device.
- "appointment": scheduling / rescheduling an install or tech visit.
- "outage": area-wide service disruption lookups.
- "account": session management (sign in / sign out / switch user).
- "general": capability, coverage, hours, anything else.

RULES:
1. Return 1 sub-query for a simple, single-topic question.
2. Return 2-3 sub-queries ONLY when the customer's message clearly \
mixes distinct topics that belong to different specialists. Max 3.
3. Each sub-query must be a standalone question a specialist can \
answer without seeing the others. Rewrite — do not quote the original.
4. Do not invent topics the customer didn't mention.
5. Keep the order they were raised in the original message.
6. "rationale" is a 1-line explanation of why this specialist handles \
this slice.

Output JSON only:
{"sub_queries": [{"sub_query": "...", "specialist": "...", "rationale": "..."}]}
"""


decomposer_agent = Agent(
    model="openai:gpt-4o-mini",  # overridden at .run() via agent_model("router")
    output_type=str,
    system_prompt=_DECOMPOSER_SYSTEM_PROMPT,
    deps_type=DecomposerDeps,
)


def _fallback_single(query: str) -> Decomposition:
    """Fall back to routing the whole query as one general sub-query.

    Used when the LLM output is unparseable — better to run the full
    flow against the catch-all than to crash the debug request."""
    return Decomposition(
        sub_queries=[
            SubQuery(
                sub_query=query,
                specialist="general",
                rationale="decomposer_fallback",
            )
        ]
    )


async def decompose(
    query: str,
    model: str,
) -> tuple[Decomposition, RunUsage]:
    """Decompose a query. Never raises — falls back on any failure."""
    try:
        result = await decomposer_agent.run(
            query, model=model, deps=DecomposerDeps()
        )
        usage = result.usage()
    except Exception as e:
        logger.error("Decomposer call failed: %s", e, exc_info=True)
        return _fallback_single(query), RunUsage()

    try:
        parsed = json.loads(result.output)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Decomposer returned unparseable JSON: %r", result.output)
        return _fallback_single(query), usage

    raw = parsed.get("sub_queries") or []
    if not isinstance(raw, list) or not raw:
        return _fallback_single(query), usage

    sub_queries: list[SubQuery] = []
    for item in raw[:MAX_SUB_QUERIES]:
        if not isinstance(item, dict):
            continue
        sq = (item.get("sub_query") or "").strip()
        spec = (item.get("specialist") or "").strip()
        if not sq or spec not in SPECIALIST_REGISTRY:
            continue
        sub_queries.append(
            SubQuery(
                sub_query=sq,
                specialist=spec,
                rationale=(item.get("rationale") or "").strip(),
            )
        )

    if not sub_queries:
        return _fallback_single(query), usage

    return Decomposition(sub_queries=sub_queries), usage
