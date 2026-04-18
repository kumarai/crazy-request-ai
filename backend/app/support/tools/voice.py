"""Voice service tool stub. Phase 0.5: returns mock data."""
from __future__ import annotations


async def voice_get_details(customer_id: str) -> dict:
    """Get voice service details for a customer."""
    # TODO: Wire to real MCP voice backend
    return {
        "customer_id": customer_id,
        "service": "voice",
        "status": "active",
        "plan": "Unlimited Talk",
        "monthly_cost": 29.99,
        "features": ["voicemail", "call_forwarding", "caller_id"],
        "phone_number": "555-0100",
    }
