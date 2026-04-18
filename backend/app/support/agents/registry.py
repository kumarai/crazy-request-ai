"""Specialist registry: maps specialist names to agents, model slots, and tool sets.

Adding a new specialist = new agent file + one entry here.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import Agent

from app.support.agents.account_agent import account_agent
from app.support.agents.appointment_agent import appointment_agent
from app.support.agents.bill_pay_agent import bill_pay_agent
from app.support.agents.billing_agent import billing_agent
from app.support.agents.general_agent import general_agent
from app.support.agents.order_agent import order_agent
from app.support.agents.outage_agent import outage_agent
from app.support.agents.technical_agent import technical_agent


@dataclass
class SpecialistConfig:
    agent: Agent
    model_slot: str
    domain: str
    faithfulness_model_slot: str  # billing uses "generation", others use "followup"
    # Does this specialist produce a structured output with a
    # ``handoff_to`` field? The orchestrator reads that field directly
    # instead of running the text-regex detector.
    structured_handoff: bool = False
    # Does this specialist require an authenticated (non-guest) session?
    # Guests hitting these specialists are handed off to the ``account``
    # specialist for sign-in instead of running the real flow.
    requires_auth: bool = False
    # Per-specialist retrieval size override. ``None`` falls back to
    # ``settings.rag_top_k_final``. Why this matters: outage and
    # general specialists answer from a very narrow KB slice (one
    # article per incident / one FAQ per topic); asking for 8 chunks
    # costs rerank tokens + latency for candidates the model will
    # ignore. Technical troubleshooting benefits from breadth and
    # should keep the default. Conservative tuning, easy to adjust.
    top_k: int | None = None
    # Does this specialist need retrieval to clear the scope gate to
    # answer? Tool-driven specialists (appointment booking, bill pay,
    # order placement, outage lookup) ground their replies in live
    # MCP tool output and the customer's conversation, not KB
    # articles. For them, a missing KB is expected — the scope gate
    # should NOT block them. Knowledge specialists (technical,
    # billing info, general) still need KB coverage and keep the
    # default True.
    requires_kb_grounding: bool = True


SPECIALIST_REGISTRY: dict[str, SpecialistConfig] = {
    "technical": SpecialistConfig(
        agent=technical_agent,
        model_slot="technical",
        domain="technical",
        faithfulness_model_slot="followup",
        top_k=8,  # broad — troubleshooting benefits from cross-article recall
    ),
    "billing": SpecialistConfig(
        agent=billing_agent,
        model_slot="billing",
        domain="billing",
        faithfulness_model_slot="generation",  # higher-stakes: hallucinated charges
        requires_auth=True,
        top_k=5,
    ),
    "general": SpecialistConfig(
        agent=general_agent,
        model_slot="general",
        domain="general",
        faithfulness_model_slot="followup",
        top_k=4,  # short capability/FAQ answers — no need for breadth
    ),
    "outage": SpecialistConfig(
        agent=outage_agent,
        model_slot="outage",
        domain="outage",
        faithfulness_model_slot="followup",
        structured_handoff=True,
        top_k=3,  # outage answers are grounded in tool output, not KB recall
        requires_kb_grounding=False,  # tool-driven
    ),
    "order": SpecialistConfig(
        agent=order_agent,
        model_slot="order",
        domain="order",
        faithfulness_model_slot="followup",
        # Browsing the catalog is allowed for guests — auth gate fires
        # only when the customer tries to place an order (handled in the
        # action endpoint, not the orchestrator).
        requires_auth=False,
        top_k=6,
        requires_kb_grounding=False,  # catalog + orders come from MCP
    ),
    "bill_pay": SpecialistConfig(
        agent=bill_pay_agent,
        model_slot="bill_pay",
        domain="bill_pay",
        faithfulness_model_slot="generation",  # same stakes as billing
        requires_auth=True,
        top_k=4,
        requires_kb_grounding=False,  # balance + PMs come from MCP
    ),
    "appointment": SpecialistConfig(
        agent=appointment_agent,
        model_slot="appointment",
        domain="appointment",
        faithfulness_model_slot="followup",
        requires_auth=True,
        top_k=4,
        requires_kb_grounding=False,  # slot list comes from MCP
    ),
    "account": SpecialistConfig(
        agent=account_agent,
        # No dedicated model slot for account — small-model tier is
        # enough. Falls through to ``followup`` which maps to the
        # cheap chat model in the provider's slot table.
        model_slot="followup",
        domain="account",
        faithfulness_model_slot="followup",
        # Guests must reach this specialist — it is precisely how they
        # move from guest to authed. Never gate.
        requires_auth=False,
        top_k=2,  # minimal retrieval; replies come from session state
        # No KB to ground against — the agent's only facts come from
        # ``check_session_status``.
        requires_kb_grounding=False,
    ),
}


def get_specialist(name: str) -> SpecialistConfig:
    """Get a specialist config by name. Raises KeyError if not found."""
    return SPECIALIST_REGISTRY[name]


def list_specialists() -> list[str]:
    return list(SPECIALIST_REGISTRY.keys())
