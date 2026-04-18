"""Shared ``propose_*`` tool helpers used by write-capable specialists.

A ``propose_*`` tool does NOT mutate anything. It records a
structured proposal on ``deps.tool_outputs``; the orchestrator picks
that up after the specialist run finishes, mints an entry in the
Redis ``ActionRegistry`` with a short TTL, and emits an
``INTERACTIVE_ACTIONS`` event so the frontend can render a
confirmation button. Only when the customer clicks does the
``/v1/support/action`` endpoint actually call the MCP write tool.

Why this pattern:
  - Writes never run silently from the LLM's tool_calls.
  - Idempotency keys + authz checks are enforced in one place.
  - Specialists stay single-responsibility: "pick a reasonable
    action, describe it in text, propose it."
"""
from __future__ import annotations

from typing import Any

from app.support.agents.support_agent import SupportAgentDeps

PROPOSAL_KEY = "_proposal"

# Proposal kinds that require a non-guest session. Orchestrator drops
# any proposal in this set when ``customer.is_guest`` is True; guests
# are expected to hit the auth gate (which handoffs to ``account``)
# first, but we belt-and-suspender at the action-mint layer too.
# ``sign_in`` / ``sign_out`` are intentionally NOT in this set — the
# account specialist must be able to offer Sign-in to guests, and
# authed users need Sign-out to be surfaceable.
AUTH_REQUIRED_KINDS: frozenset[str] = frozenset({
    "pay",
    "enroll_autopay",
    "place_order",
    "cancel_order",
    "book_appointment",
    "cancel_appointment",
    "reschedule_appointment",
    "add_payment_method",
    "set_default_payment_method",
})


def record_proposal(
    deps: SupportAgentDeps,
    *,
    kind: str,
    label: str,
    payload: dict[str, Any],
    confirm_text: str | None = None,
) -> dict[str, Any]:
    """Append a structured proposal to ``deps.tool_outputs``.

    Returns the proposal dict so the tool function can echo it back
    to the LLM — most agents don't need the echo, but pydantic-ai
    requires a non-empty return value from tool functions.
    """
    proposal = {
        "kind": kind,
        "label": label,
        "confirm_text": confirm_text,
        "payload": payload,
    }
    deps.tool_outputs.append(
        {"tool": f"propose_{kind}", "output": {PROPOSAL_KEY: proposal}}
    )
    return proposal


def extract_proposals(tool_outputs: list[dict]) -> list[dict[str, Any]]:
    """Pull every proposal dict out of the captured tool outputs.

    Returns a list of ``{kind, label, confirm_text, payload}`` in
    call order. Duplicate proposals (same kind + same payload) are
    deduped so the LLM spamming ``propose_*`` doesn't result in
    multiple buttons.
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for entry in tool_outputs:
        output = entry.get("output") or {}
        proposal = output.get(PROPOSAL_KEY)
        if not isinstance(proposal, dict):
            continue
        key = (proposal.get("kind", ""), repr(sorted((proposal.get("payload") or {}).items())))
        if key in seen:
            continue
        seen.add(key)
        out.append(proposal)
    return out
