"""Tests for the router agent hard rules and fallback logic."""
import pytest

from app.support.agents.router_agent import (
    RouterDecision,
    apply_hard_rules,
)


class TestHardRules:
    def test_billing_keyword_refund(self):
        result = apply_hard_rules("I want a refund for my last bill")
        assert result is not None
        assert result.specialist == "billing"
        assert result.confidence == 1.0

    def test_billing_keyword_invoice(self):
        result = apply_hard_rules("Can I see my invoice?")
        assert result is not None
        assert result.specialist == "billing"

    def test_billing_keyword_charge(self):
        result = apply_hard_rules("Why was I charged $50 extra?")
        assert result is not None
        assert result.specialist == "billing"

    def test_bill_pay_keyword_make_payment(self):
        # "make a payment" is an action verb — belongs to bill_pay,
        # not the informational billing specialist.
        result = apply_hard_rules("How do I make a payment?")
        assert result is not None
        assert result.specialist == "bill_pay"

    def test_billing_keyword_balance(self):
        result = apply_hard_rules("What is my current balance?")
        assert result is not None
        assert result.specialist == "billing"

    def test_billing_keyword_late_fee(self):
        result = apply_hard_rules("I have a late fee on my account")
        assert result is not None
        assert result.specialist == "billing"

    def test_bill_pay_keyword_autopay(self):
        # "set up autopay" is an enroll action — bill_pay, not billing.
        result = apply_hard_rules("How do I set up autopay?")
        assert result is not None
        assert result.specialist == "bill_pay"

    def test_no_billing_keyword_internet(self):
        result = apply_hard_rules("My internet is really slow")
        assert result is None

    def test_no_billing_keyword_device(self):
        result = apply_hard_rules("My router keeps rebooting")
        assert result is None

    def test_outage_keyword_outage(self):
        # Outage has its own hard-rule since the specialist split.
        result = apply_hard_rules("Is there an outage in my area?")
        assert result is not None
        assert result.specialist == "outage"

    def test_case_insensitive(self):
        result = apply_hard_rules("I need a REFUND immediately")
        assert result is not None
        assert result.specialist == "billing"
