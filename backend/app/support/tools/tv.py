"""TV service tool stub. Phase 0.5: returns mock data."""
from __future__ import annotations


async def tv_get_details(customer_id: str) -> dict:
    """Get TV service details for a customer."""
    # TODO: Wire to real MCP TV backend
    return {
        "customer_id": customer_id,
        "service": "tv",
        "status": "active",
        "plan": "Ultimate Entertainment",
        "channels_count": 250,
        "monthly_cost": 89.99,
        "dvr_enabled": True,
        "streaming_included": True,
    }
