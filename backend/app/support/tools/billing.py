"""Billing tool shims — call the MCP server when live, fall back to static stubs."""
from __future__ import annotations

import logging

from app.support.tools._mcp_bridge import get_mcp, is_live

logger = logging.getLogger("[support]")

_STATIC_INVOICE = {
    "invoice_id": "inv_demo",
    "date": "2026-04-01",
    "due_date": "2026-04-15",
    "total": 254.97,
    "status": "unpaid",
    "line_items": [],
}

_STATIC_BALANCE = {
    "current_balance": 264.97,
    "past_due": 10.00,
    "next_bill_date": "2026-05-01",
    "autopay_enabled": False,
}


async def billing_get_invoice(customer_id: str, invoice_id: str) -> dict:
    if is_live():
        try:
            return await get_mcp().call_tool(
                "billing_get_invoice",
                {"customer_id": customer_id, "invoice_id": invoice_id},
            )
        except Exception as e:
            logger.warning("MCP billing_get_invoice failed, using stub: %s", e)
    return {"customer_id": customer_id, **_STATIC_INVOICE, "invoice_id": invoice_id}


async def billing_list_charges(customer_id: str) -> dict:
    if is_live():
        try:
            result = await get_mcp().call_tool(
                "billing_list_charges", {"customer_id": customer_id}
            )
            # MCP returns list-shaped tools wrapped as {"items": [...]}
            return {"customer_id": customer_id, "charges": result.get("items", [])}
        except Exception as e:
            logger.warning("MCP billing_list_charges failed, using stub: %s", e)
    return {
        "customer_id": customer_id,
        "charges": [
            {"date": "2026-04-01", "description": "Monthly service", "amount": 254.97},
            {"date": "2026-03-15", "description": "Late fee", "amount": 10.00},
            {"date": "2026-03-01", "description": "Monthly service", "amount": 254.97},
        ],
    }


async def billing_get_balance(customer_id: str) -> dict:
    if is_live():
        try:
            return await get_mcp().call_tool(
                "billing_get_balance", {"customer_id": customer_id}
            )
        except Exception as e:
            logger.warning("MCP billing_get_balance failed, using stub: %s", e)
    return {"customer_id": customer_id, **_STATIC_BALANCE}
