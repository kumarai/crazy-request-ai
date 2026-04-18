"""SSE event-shaping helpers shared by every orchestrator.

``sse_starlette.EventSourceResponse`` consumes ``{"event": ..., "data": ...}``
dicts; this thin helper keeps the construction in one place so all
orchestrators emit a consistent shape.
"""
from __future__ import annotations


def sse_event(event_type: str, data: str) -> dict[str, str]:
    """Build the dict shape ``EventSourceResponse`` expects."""
    return {"event": event_type, "data": data}
