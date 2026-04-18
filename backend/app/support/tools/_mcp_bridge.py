"""Module-level holder for the MCP client.

Tool shims call ``get_mcp()`` to access the live client. ``app.main``
calls ``set_mcp()`` at startup. Keeping this out-of-band avoids
threading the client through every tool-registration call site.

If MCP is not configured (``NullMcpClient``) or unreachable, shims
fall back to their static stub responses so the chat UI keeps
working offline.
"""
from __future__ import annotations

from typing import Any

_client: Any = None


def set_mcp(client: Any) -> None:
    global _client
    _client = client


def get_mcp() -> Any:
    return _client


def is_live() -> bool:
    return _client is not None and getattr(_client, "connected", False)
