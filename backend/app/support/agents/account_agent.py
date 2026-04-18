"""Account specialist — session status, sign-in, sign-out, post-login resume.

Guests hitting any auth-gated specialist (billing / bill_pay /
appointment) are handed off here so the agent can explain the gate,
surface a Sign-in button, and — after login — offer to resume the
original intent. Every reply is grounded in ``check_session_status``
(the server's signed cookie), not the customer's claim in chat. Sign
in / sign out propagate through ``/v1/support/action`` exactly like
any other interactive action, so the backend retains a single control
plane for session mutations.

Never registered on write tools beyond the two session proposals —
the account agent must not place orders, pay bills, or otherwise act
on behalf of the customer.
"""
from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.support.agents.prompts.account import ACCOUNT_SYSTEM_PROMPT
from app.support.agents.proposal_tools import record_proposal
from app.support.agents.support_agent import SupportAgentDeps

account_agent = Agent(
    model="openai:gpt-4o-mini",  # overridden via agent_model("account")
    output_type=str,
    system_prompt=ACCOUNT_SYSTEM_PROMPT,
    deps_type=SupportAgentDeps,
)


@account_agent.tool
async def check_session_status(ctx: RunContext[SupportAgentDeps]) -> dict:
    """Return the server-resolved session state for this turn.

    Reads from ``deps.customer`` (populated from the signed session
    cookie by the API layer) and from the conversation's stashed
    ``pending_intent`` (set by the orchestrator on the gated turn).
    This is the ONLY source of truth the agent should rely on for
    "are they signed in?" questions.
    """
    customer = ctx.deps.customer
    pending = getattr(ctx.deps, "pending_intent", None)
    output = {
        "is_guest": bool(customer.is_guest),
        "customer_id": customer.customer_id,
        "last_specialist": ctx.deps.history.last_specialist,
        "pending_intent": pending,
    }
    ctx.deps.tool_outputs.append(
        {"tool": "check_session_status", "output": output}
    )
    return output


@account_agent.tool
async def propose_sign_in(
    ctx: RunContext[SupportAgentDeps],
    reason: str | None = None,
) -> dict:
    """Surface a Sign-in button to the customer.

    ``reason`` is a short phrase the agent wants the sign-in modal to
    display (e.g. "to place your order"). Pure UI hint — no side
    effects at mint time; the frontend opens the existing LoginDialog
    when the action resolves. No auth required (guests can click this).
    """
    label = "Sign in"
    if reason:
        label = f"Sign in {reason}" if reason.startswith("to ") else f"Sign in — {reason}"
    return record_proposal(
        ctx.deps,
        kind="sign_in",
        label=label,
        confirm_text=None,
        payload={"reason": reason or ""},
    )


@account_agent.tool
async def propose_sign_out(ctx: RunContext[SupportAgentDeps]) -> dict:
    """Surface a Sign-out button to the customer.

    Clicking clears the signed session cookie server-side and wipes
    the conversation's ``last_specialist`` + ``pending_intent`` so the
    next turn starts from a clean slate.
    """
    return record_proposal(
        ctx.deps,
        kind="sign_out",
        label="Sign out",
        confirm_text="Sign out of this session?",
        payload={},
    )
