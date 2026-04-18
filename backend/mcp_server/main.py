"""MCP server entrypoint.

Starts a FastMCP app with streamable-HTTP transport. Seeds SQLite on
first run so the dev stack works out of the box without a migration
step.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from mcp_server.adapters.factory import Repos, build_repos, close_repos
from mcp_server.config import load_settings
from mcp_server.server import build_mcp

logging.basicConfig(
    level=os.environ.get("MCP_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("[mcp]")

_SCHEMA_PATH = Path(__file__).parent / "db" / "schema.sql"
_SEED_PATH = Path(__file__).parent / "db" / "seed.sql"


async def _ensure_seed(repos: Repos, seed_on_start: bool) -> None:
    """Apply schema + seed on the SQLite store. No-op for http backend."""
    store = repos._sqlite_store
    if store is None:
        return
    schema_sql = _SCHEMA_PATH.read_text()
    await store.apply_schema(schema_sql)
    if seed_on_start:
        seed_sql = _SEED_PATH.read_text()
        await store.seed(seed_sql)
        logger.info("SQLite seeded at %s", store._path)


def main() -> None:
    settings = load_settings()
    repos = asyncio.get_event_loop().run_until_complete(build_repos(settings))
    asyncio.get_event_loop().run_until_complete(
        _ensure_seed(repos, settings.seed_on_start)
    )
    mcp = build_mcp(repos)
    # FastMCP.run() handles its own event loop. Streamable HTTP transport
    # (spec-compliant MCP over HTTP + optional SSE for server→client).
    logger.info(
        "Starting MCP server on %s:%d (backend=%s)",
        settings.host, settings.port, settings.backend,
    )
    try:
        mcp.settings.host = settings.host
        mcp.settings.port = settings.port
        mcp.run(transport="streamable-http")
    finally:
        asyncio.get_event_loop().run_until_complete(close_repos(repos))


if __name__ == "__main__":
    main()
