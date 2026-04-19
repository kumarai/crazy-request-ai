"""In-memory execution trace for the debug-mode parallel orchestrator.

Each node in the DAG records the input it was given, the output it
produced, retrieved sources, tool calls, timings, and status. The
frontend debug page renders the nodes + edges as a graph and the
per-node detail as an inspector panel.

Not persisted. Lives only for the duration of a single /debug/query
request.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator, Literal

from pydantic import BaseModel

from app.streaming.events import ChunkPreview


NodeKind = Literal["decomposer", "specialist", "synthesizer"]
NodeStatus = Literal["ok", "auth_skipped", "error"]


class TraceToolCall(BaseModel):
    name: str
    input: dict | None = None
    output: Any | None = None


class TraceNode(BaseModel):
    id: str
    kind: NodeKind
    specialist: str | None = None
    sub_query: str | None = None
    rationale: str | None = None
    output_text: str | None = None
    sources: list[ChunkPreview] = []
    tool_calls: list[TraceToolCall] = []
    timing_ms: int = 0
    status: NodeStatus = "ok"
    error: str | None = None


class TraceEdge(BaseModel):
    from_id: str
    to_id: str


class Trace(BaseModel):
    query: str
    nodes: list[TraceNode] = []
    edges: list[TraceEdge] = []
    final_answer: str = ""
    total_latency_ms: int = 0
    total_cost_usd: float | None = None


class TraceRecorder:
    """Mutable builder for a ``Trace``. Thread-unsafe by design — each
    request builds its own recorder."""

    def __init__(self, query: str) -> None:
        self._trace = Trace(query=query)
        self._start = time.time()

    def add_node(self, node: TraceNode) -> None:
        self._trace.nodes.append(node)

    def add_edge(self, from_id: str, to_id: str) -> None:
        self._trace.edges.append(TraceEdge(from_id=from_id, to_id=to_id))

    def set_final_answer(self, text: str) -> None:
        self._trace.final_answer = text

    def set_cost(self, cost_usd: float | None) -> None:
        self._trace.total_cost_usd = cost_usd

    @contextmanager
    def time_node(self) -> Iterator[dict[str, int]]:
        """Context manager that writes the elapsed ms into the yielded dict.

        Usage::

            with recorder.time_node() as t:
                ...
            node.timing_ms = t["ms"]
        """
        holder = {"ms": 0}
        started = time.time()
        try:
            yield holder
        finally:
            holder["ms"] = int((time.time() - started) * 1000)

    def finalize(self) -> Trace:
        self._trace.total_latency_ms = int((time.time() - self._start) * 1000)
        return self._trace
