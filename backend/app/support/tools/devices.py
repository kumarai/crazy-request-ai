"""Device management tool stubs. Phase 0.5: returns mock data."""
from __future__ import annotations


async def list_devices(customer_id: str) -> dict:
    """List all devices associated with a customer account."""
    # TODO: Wire to real MCP device backend
    return {
        "customer_id": customer_id,
        "devices": [
            {
                "id": "dev-001",
                "type": "modem",
                "model": "XB8",
                "status": "online",
                "mac": "AA:BB:CC:DD:EE:01",
            },
            {
                "id": "dev-002",
                "type": "router",
                "model": "XFi Gateway",
                "status": "online",
                "mac": "AA:BB:CC:DD:EE:02",
            },
            {
                "id": "dev-003",
                "type": "set_top_box",
                "model": "Xi6",
                "status": "offline",
                "mac": "AA:BB:CC:DD:EE:03",
            },
        ],
    }


async def get_device(device_id: str) -> dict:
    """Get detailed info for a specific device."""
    # TODO: Wire to real MCP device backend
    return {
        "id": device_id,
        "type": "modem",
        "model": "XB8",
        "status": "online",
        "firmware": "v3.2.1",
        "uptime_hours": 720,
        "signal_strength": "good",
        "last_restart": "2026-04-10T08:00:00Z",
    }
