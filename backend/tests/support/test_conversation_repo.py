"""Tests for the conversation repository.

These tests validate the repository interface. Integration tests
requiring a real database should be run with the test database fixture.
"""
import pytest

from app.support.customer_context import CustomerContext


class TestCustomerContext:
    def test_default_context(self):
        ctx = CustomerContext(customer_id="cust-123")
        assert ctx.customer_id == "cust-123"
        assert ctx.plan == "unknown"
        assert ctx.services == []
        assert ctx.flags == {}
        assert ctx.allowed_source_ids == []

    def test_full_context(self):
        ctx = CustomerContext(
            customer_id="cust-456",
            plan="premium",
            services=["internet", "tv"],
            flags={"active": True},
            allowed_source_ids=["src-1", "src-2"],
        )
        assert ctx.plan == "premium"
        assert len(ctx.services) == 2
        assert ctx.flags["active"] is True
        assert len(ctx.allowed_source_ids) == 2
