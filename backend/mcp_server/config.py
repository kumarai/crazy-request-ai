"""Environment configuration for the MCP server.

Values are read from env; defaults target the Docker Compose dev stack.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class McpSettings:
    backend: str  # "sqlite" | "http"
    sqlite_path: str
    downstream_billing_url: str | None
    downstream_orders_url: str | None
    downstream_appointments_url: str | None
    downstream_outage_url: str | None
    host: str
    port: int
    seed_on_start: bool
    # Signing secret used to verify action-ids issued by the backend. Writes
    # are rejected unless the authz header matches. Dev default is weak on
    # purpose — rotate for real deployments.
    mcp_shared_secret: str


def load_settings() -> McpSettings:
    return McpSettings(
        backend=os.environ.get("MCP_BACKEND", "sqlite"),
        sqlite_path=os.environ.get(
            "MCP_SQLITE_PATH", "/data/mcp/mcp_data.db"
        ),
        downstream_billing_url=os.environ.get("MCP_DOWNSTREAM_BILLING_URL"),
        downstream_orders_url=os.environ.get("MCP_DOWNSTREAM_ORDERS_URL"),
        downstream_appointments_url=os.environ.get(
            "MCP_DOWNSTREAM_APPOINTMENTS_URL"
        ),
        downstream_outage_url=os.environ.get("MCP_DOWNSTREAM_OUTAGE_URL"),
        host=os.environ.get("MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("MCP_PORT", "8765")),
        seed_on_start=os.environ.get("MCP_SEED", "true").lower() == "true",
        mcp_shared_secret=os.environ.get(
            "MCP_SHARED_SECRET", "dev-insecure-mcp-secret"
        ),
    )
