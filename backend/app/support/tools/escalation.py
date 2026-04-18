"""Escalation contact tool. Returns the right phone/URL/chat queue."""
from __future__ import annotations

_CONTACTS: dict[str, dict] = {
    "billing": {
        "type": "phone",
        "value": "1-800-BILL-HELP",
        "hours": "24/7",
        "url": "https://support.example.com/billing",
    },
    "technical": {
        "type": "phone",
        "value": "1-800-TECH-HELP",
        "hours": "24/7",
        "url": "https://support.example.com/technical",
    },
    "sales": {
        "type": "chat",
        "value": "https://support.example.com/sales/chat",
        "hours": "8am-10pm ET",
        "url": "https://support.example.com/sales",
    },
    "network": {
        "type": "phone",
        "value": "1-800-NET-OPS",
        "hours": "24/7",
        "url": "https://support.example.com/outages",
    },
    "general": {
        "type": "phone",
        "value": "1-800-SUPPORT",
        "hours": "24/7",
        "url": "https://support.example.com",
    },
}


async def get_escalation_contact(topic: str) -> dict:
    """Get the escalation contact info for a given topic."""
    return _CONTACTS.get(topic, _CONTACTS["general"])
