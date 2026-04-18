"""Client for the telecom-support MCP server.

Sessions are opened per call. A persistent session held across
FastAPI requests breaks because anyio cancel scopes are tied to the
task that entered them — the lifespan startup task exits before the
request task tries to use the session, and anyio raises
``Attempted to exit cancel scope in a different task``.

Per-call is ~50ms slower per tool invocation than a reused session,
but correct. For Phase A / B traffic shapes (≤5 tool calls per
turn), the cost is in the noise next to the LLM round-trips.

If the MCP server is unreachable we degrade to ``NullMcpClient`` so
the backend still boots and answers non-tool turns.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger("[support]")


class McpClient:
    """Per-call streamable-HTTP client to the MCP server."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._healthy = False

    async def start(self) -> None:
        """Probe the server once so ``connected`` reflects reality."""
        try:
            # Fire a no-op session to prove the server responds.
            async with self._session() as session:
                await session.list_tools()
            self._healthy = True
            logger.info("MCP reachable at %s", self._url)
        except Exception as e:
            logger.error("MCP probe failed for %s: %s", self._url, e)
            self._healthy = False

    async def close(self) -> None:
        # No persistent state to tear down.
        return

    @property
    def connected(self) -> bool:
        return self._healthy

    def _session(self):
        """Return an async-context session factory.

        Importing inside the method keeps the module importable even
        when ``mcp`` isn't installed (the Null client takes over).
        """
        from contextlib import asynccontextmanager

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        url = self._url

        @asynccontextmanager
        async def _ctx():
            async with streamablehttp_client(url) as (read, write, _extra):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

        return _ctx()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._session() as session:
                result = await session.call_tool(name, arguments)
        except Exception as e:
            logger.error("MCP call_tool(%s) failed: %s", name, e)
            raise

        if getattr(result, "isError", False):
            raise RuntimeError(f"MCP tool {name} errored: {_extract_text(result)}")
        return _extract_json(result)


def _extract_text(result: Any) -> str:
    contents = getattr(result, "content", None) or []
    for c in contents:
        text = getattr(c, "text", None)
        if text:
            return text
    return "<no content>"


def _extract_json(result: Any) -> dict[str, Any]:
    # Prefer structuredContent (newer servers set this) — avoids a
    # JSON re-parse of the text blob.
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        # Tools returning lists get the {"result": [...]} envelope;
        # normalise for callers.
        if list(structured.keys()) == ["result"] and isinstance(
            structured["result"], list
        ):
            return {"items": structured["result"]}
        return structured

    contents = getattr(result, "content", None) or []
    for c in contents:
        text = getattr(c, "text", None)
        if text:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return {"items": parsed}
                return parsed
            except json.JSONDecodeError:
                return {"text": text}
    return {}


class NullMcpClient:
    """Fallback used when the real server is unreachable."""

    @property
    def connected(self) -> bool:
        return False

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        raise RuntimeError(
            f"MCP server unavailable; tool {name} cannot be called"
        )

    async def start(self) -> None:  # pragma: no cover
        return

    async def close(self) -> None:  # pragma: no cover
        return


async def build_mcp_client() -> McpClient | NullMcpClient:
    """Build + probe the MCP client on startup.

    Never raises — returns ``NullMcpClient`` on any failure so the app
    boots in degraded mode. Logs the reason loudly.
    """
    url = os.environ.get("MCP_SERVER_URL", "http://mcp-server:8765/mcp")
    try:
        import mcp  # noqa: F401
    except ImportError as e:
        logger.warning("mcp package missing: %s — using NullMcpClient", e)
        return NullMcpClient()

    client = McpClient(url)
    try:
        # Short timeout so a slow / misconfigured MCP doesn't stall boot.
        await asyncio.wait_for(client.start(), timeout=10.0)
    except asyncio.TimeoutError:
        logger.error("MCP probe timed out; using NullMcpClient")
        return NullMcpClient()
    except Exception as e:
        logger.error("MCP probe raised: %s — using NullMcpClient", e)
        return NullMcpClient()

    if not client.connected:
        return NullMcpClient()
    return client
