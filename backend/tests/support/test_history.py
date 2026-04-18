"""Tests for the history assembler and compaction logic."""
import pytest

from app.support.history import HistoryContext, _estimate_tokens


class TestTokenEstimation:
    def test_empty_string(self):
        assert _estimate_tokens("") == 0

    def test_short_string(self):
        # "hello" = 5 chars / 4 = 1 token
        assert _estimate_tokens("hello") == 1

    def test_longer_string(self):
        text = "a" * 400  # 400 chars / 4 = 100 tokens
        assert _estimate_tokens(text) == 100


class TestHistoryContext:
    def test_empty_context(self):
        ctx = HistoryContext()
        assert ctx.recent_turns == []
        assert ctx.rolling_summary is None
        assert ctx.unresolved_facts == []
        assert ctx.last_specialist is None

    def test_context_with_data(self):
        ctx = HistoryContext(
            recent_turns=[{"role": "user", "content": "hello"}],
            rolling_summary="Customer asked about billing",
            unresolved_facts=["balance dispute pending"],
            last_specialist="billing",
        )
        assert len(ctx.recent_turns) == 1
        assert ctx.rolling_summary == "Customer asked about billing"
        assert len(ctx.unresolved_facts) == 1
        assert ctx.last_specialist == "billing"
