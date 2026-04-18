"""Outage lookup — MCP-backed when live, stub fallback otherwise."""
from __future__ import annotations

import logging

from app.support.tools._mcp_bridge import get_mcp, is_live

logger = logging.getLogger("[support]")


async def get_outage_for_customer(customer_id: str) -> dict:
    """Check if there is an active outage affecting the customer."""
    if is_live():
        try:
            return await get_mcp().call_tool(
                "outage_area_status", {"customer_id": customer_id}
            )
        except Exception as e:
            logger.warning("MCP outage_area_status failed, using stub: %s", e)
    return {
        "customer_id": customer_id,
        "outage_active": False,
        "area_status": "normal",
        "last_outage": {
            "date": "2026-04-01",
            "duration_hours": 2.5,
            "cause": "planned_maintenance",
            "resolved": True,
        },
    }


async def get_outage_by_zip(zip_code: str) -> dict:
    """Check outage status for a zip code (used by Outage specialist for guests)."""
    if is_live():
        try:
            return await get_mcp().call_tool(
                "outage_area_status", {"zip_code": zip_code}
            )
        except Exception as e:
            logger.warning("MCP outage_area_status(zip) failed, using stub: %s", e)
    return {"zip_code": zip_code, "outage_active": False, "area_status": "normal"}


async def get_scheduled_maintenance(zip_code: str | None = None) -> dict:
    """Upcoming scheduled maintenance in the area."""
    if is_live():
        try:
            result = await get_mcp().call_tool(
                "outage_scheduled_maintenance",
                {"zip_code": zip_code} if zip_code else {},
            )
            return {"maintenance": result.get("items", [])}
        except Exception as e:
            logger.warning("MCP scheduled_maintenance failed, using stub: %s", e)
    return {"maintenance": []}
