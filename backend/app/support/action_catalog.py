"""Static catalog of customer-support action links.

Maps a stable ``topic`` string to a user-facing button label and one
of two follow-up modes:

  - ``inline_query``: clicking the button sends this query back into
    the chat, letting the appropriate specialist render the answer
    inline (catalog cards, balance lookup, outage status…). Preferred
    whenever the data is reachable via an MCP tool — the customer
    never leaves the chat.
  - ``url``: clicking opens the URL in a new tab. Reserved for pages
    the chat has no structured data for (account settings, phone
    numbers, external handoff).

Contract:
    - ``topic`` is a dotted, lowercase, stable identifier (``billing.pay``).
    - Exactly one of ``inline_query`` or ``url`` is set per topic.
    - Topic renames are breaking changes for logged ``messages`` rows
      and analytics — prefer adding a new topic over renaming one.
"""
from __future__ import annotations

from pydantic import BaseModel, HttpUrl, model_validator


class ActionLink(BaseModel):
    """A single clickable action surfaced in the chat."""

    label: str                    # Button text shown to the user.
    topic: str                    # Stable identifier, matches the catalog key.
    # Exactly one of the following is set. ``inline_query`` keeps the
    # customer in the chat (renders an answer + cards inline);
    # ``url`` opens in a new browser tab (external destinations only).
    inline_query: str | None = None
    url: HttpUrl | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "ActionLink":
        if (self.inline_query is None) == (self.url is None):
            raise ValueError(
                f"ActionLink {self.topic!r} must set exactly one of "
                "inline_query or url"
            )
        return self


# Topic → ActionLink. Keep keys dotted + lowercase (e.g. ``billing.pay``).
# Prefer ``inline_query`` when an MCP tool can answer. External URLs
# are reserved for pages we don't render inline (account settings,
# external handoff).
ACTION_CATALOG: dict[str, ActionLink] = {
    # -- Billing — all inline, the billing/bill_pay agents have tools.
    "billing.pay": ActionLink(
        label="Pay my bill",
        topic="billing.pay",
        inline_query="I'd like to pay my bill.",
    ),
    "billing.view_invoice": ActionLink(
        label="View latest invoice",
        topic="billing.view_invoice",
        inline_query="Show me my latest invoice.",
    ),
    "billing.view_balance": ActionLink(
        label="Check my balance",
        topic="billing.view_balance",
        inline_query="What's my current balance?",
    ),
    "billing.payment_methods": ActionLink(
        label="Manage payment methods",
        topic="billing.payment_methods",
        inline_query="Show me my saved payment methods.",
    ),
    "billing.autopay": ActionLink(
        label="Set up autopay",
        topic="billing.autopay",
        inline_query="I'd like to set up autopay.",
    ),

    # -- Account — settings screens, still external for now.
    "account.update": ActionLink(
        label="Update account details",
        topic="account.update",
        url="https://example.com/account",
    ),
    "account.change_password": ActionLink(
        label="Change password",
        topic="account.change_password",
        url="https://example.com/account/security",
    ),

    # -- Plans & upgrades — inline via order specialist + list_catalog.
    "plans.browse": ActionLink(
        label="Browse plans",
        topic="plans.browse",
        inline_query="Show me all available plans.",
    ),
    "plans.upgrade": ActionLink(
        label="Upgrade my plan",
        topic="plans.upgrade",
        inline_query="I'd like to upgrade my plan. What are my options?",
    ),

    # -- Phones / devices — same pattern, different category. Useful
    # as a "browse again" CTA after an order draft has been proposed.
    "phones.browse": ActionLink(
        label="Browse phones",
        topic="phones.browse",
        inline_query="Show me available phones.",
    ),

    # -- Support & escalation.
    "support.chat_queue": ActionLink(
        label="Chat with an agent",
        topic="support.chat_queue",
        url="https://example.com/support/chat",
    ),
    "support.phone": ActionLink(
        label="Call support",
        topic="support.phone",
        url="https://example.com/support/contact",
    ),
    "support.outage_status": ActionLink(
        label="Check outage status",
        topic="support.outage_status",
        inline_query="Is there an outage in my area right now?",
    ),
}


def get_action(topic: str) -> ActionLink | None:
    """Look up a single action by topic. Returns ``None`` if unknown."""
    return ACTION_CATALOG.get(topic)


def resolve_actions(topics: list[str]) -> list[ActionLink]:
    """Resolve a list of topics, skipping unknown ones and preserving order.

    Duplicates are deduplicated on first occurrence so a specialist emitting
    the same topic twice produces one button, not two.
    """
    seen: set[str] = set()
    resolved: list[ActionLink] = []
    for topic in topics:
        if topic in seen:
            continue
        action = ACTION_CATALOG.get(topic)
        if action is None:
            continue
        seen.add(topic)
        resolved.append(action)
    return resolved
