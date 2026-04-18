"""Mobile service tool stub. Phase 0.5: returns mock data."""
from __future__ import annotations


async def mobile_get_details(customer_id: str) -> dict:
    """Get mobile service details for a customer."""
    # TODO: Wire to real MCP mobile backend
    return {
        "customer_id": customer_id,
        "service": "mobile",
        "status": "active",
        "plan": "5G Unlimited",
        "monthly_cost": 55.00,
        "data_used_gb": 12.3,
        "data_limit_gb": None,
        "device": "iPhone 16 Pro",
    }
