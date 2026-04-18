"""Ticket lookup tool stub. Phase 0.5: returns mock data."""
from __future__ import annotations


async def get_recent_tickets(customer_id: str) -> dict:
    """Get recent support tickets for a customer."""
    # TODO: Wire to real MCP ticketing backend
    return {
        "customer_id": customer_id,
        "tickets": [
            {
                "id": "TKT-10234",
                "subject": "Internet speed issue",
                "status": "resolved",
                "created": "2026-04-05",
                "resolved": "2026-04-06",
            },
        ],
    }
