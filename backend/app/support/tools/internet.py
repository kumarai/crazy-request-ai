"""Internet service tool stub. Phase 0.5: returns mock data."""
from __future__ import annotations


async def internet_get_details(customer_id: str) -> dict:
    """Get internet service details for a customer."""
    # TODO: Wire to real MCP internet backend
    return {
        "customer_id": customer_id,
        "service": "internet",
        "status": "active",
        "plan": "Gigabit Pro",
        "download_speed_mbps": 1000,
        "upload_speed_mbps": 500,
        "monthly_cost": 79.99,
        "modem_model": "XB8",
        "wifi_enabled": True,
    }
