"""Offline unit tests for the decomposer fallback + debug trace recorder.

LLM-dependent paths are covered by integration smoke tests; these
tests pin the parts that don't need a provider key.
"""
from __future__ import annotations

from app.support.debug_trace import (
    Trace,
    TraceNode,
    TraceRecorder,
    TraceToolCall,
)
from app.support.decomposer import MAX_SUB_QUERIES, _fallback_single


class TestDecomposerFallback:
    def test_fallback_returns_single_general_branch(self):
        d = _fallback_single("hello world")
        assert len(d.sub_queries) == 1
        assert d.sub_queries[0].specialist == "general"
        assert d.sub_queries[0].sub_query == "hello world"

    def test_max_sub_queries_cap(self):
        # Regression pin: we don't want the debug UI to balloon past 3
        # branches. If someone raises the cap they should update tests
        # and the debug-page layout.
        assert MAX_SUB_QUERIES == 3


class TestTraceRecorder:
    def test_nodes_and_edges_accumulate(self):
        r = TraceRecorder("q")
        r.add_node(TraceNode(id="a", kind="decomposer"))
        r.add_node(
            TraceNode(
                id="b",
                kind="specialist",
                specialist="billing",
                tool_calls=[TraceToolCall(name="billing_get_balance", output={"balance": 12.0})],
            )
        )
        r.add_edge("a", "b")
        r.set_final_answer("final")
        r.set_cost(0.001)
        t = r.finalize()

        assert isinstance(t, Trace)
        assert [n.id for n in t.nodes] == ["a", "b"]
        assert len(t.edges) == 1
        assert t.edges[0].from_id == "a"
        assert t.edges[0].to_id == "b"
        assert t.final_answer == "final"
        assert t.total_cost_usd == 0.001
        assert t.total_latency_ms >= 0

    def test_time_node_context_manager_records_ms(self):
        r = TraceRecorder("q")
        with r.time_node() as h:
            pass
        # We can't assert > 0 on a zero-duration no-op, but the key
        # contract is that ``ms`` is written (not still missing).
        assert "ms" in h
        assert isinstance(h["ms"], int)

    def test_trace_serializes_to_dict(self):
        r = TraceRecorder("q")
        r.add_node(TraceNode(id="a", kind="decomposer"))
        t = r.finalize()
        dumped = t.model_dump()
        assert dumped["query"] == "q"
        assert dumped["nodes"][0]["id"] == "a"
        assert dumped["nodes"][0]["kind"] == "decomposer"
