"""Tool registry mapping tool names to implementations and domain tags."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Coroutine

from app.support.tools.billing import (
    billing_get_balance,
    billing_get_invoice,
    billing_list_charges,
)
from app.support.tools.devices import get_device, list_devices
from app.support.tools.escalation import get_escalation_contact
from app.support.tools.internet import internet_get_details
from app.support.tools.mobile import mobile_get_details
from app.support.tools.outage import get_outage_for_customer
from app.support.tools.tickets import get_recent_tickets
from app.support.tools.tv import tv_get_details
from app.support.tools.voice import voice_get_details

ToolFunc = Callable[..., Coroutine[Any, Any, dict]]


@dataclass
class ToolEntry:
    func: ToolFunc
    domain: str
    description: str


# Master registry of all support tools
TOOL_REGISTRY: dict[str, ToolEntry] = {
    "voice_get_details": ToolEntry(
        func=voice_get_details,
        domain="technical",
        description="Get voice service details for a customer",
    ),
    "mobile_get_details": ToolEntry(
        func=mobile_get_details,
        domain="technical",
        description="Get mobile service details for a customer",
    ),
    "internet_get_details": ToolEntry(
        func=internet_get_details,
        domain="technical",
        description="Get internet service details for a customer",
    ),
    "tv_get_details": ToolEntry(
        func=tv_get_details,
        domain="technical",
        description="Get TV service details for a customer",
    ),
    "list_devices": ToolEntry(
        func=list_devices,
        domain="technical",
        description="List all devices on the customer account",
    ),
    "get_device": ToolEntry(
        func=get_device,
        domain="technical",
        description="Get detailed info for a specific device",
    ),
    "get_outage_for_customer": ToolEntry(
        func=get_outage_for_customer,
        domain="technical",
        description="Check for active outages affecting the customer",
    ),
    "get_recent_tickets": ToolEntry(
        func=get_recent_tickets,
        domain="technical",
        description="Get recent support tickets for a customer",
    ),
    "billing_get_invoice": ToolEntry(
        func=billing_get_invoice,
        domain="billing",
        description="Get a specific invoice for a customer",
    ),
    "billing_list_charges": ToolEntry(
        func=billing_list_charges,
        domain="billing",
        description="List recent charges for a customer",
    ),
    "billing_get_balance": ToolEntry(
        func=billing_get_balance,
        domain="billing",
        description="Get current account balance",
    ),
    "get_escalation_contact": ToolEntry(
        func=get_escalation_contact,
        domain="shared",
        description="Get escalation contact info for a topic",
    ),
}


def get_tools_for_domain(domain: str) -> dict[str, ToolEntry]:
    """Return tools for a specific domain + shared tools."""
    return {
        name: entry
        for name, entry in TOOL_REGISTRY.items()
        if entry.domain == domain or entry.domain == "shared"
    }
