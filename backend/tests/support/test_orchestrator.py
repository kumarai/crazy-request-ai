"""Tests for the support orchestrator.

Tests handoff detection, specialist registry lookups, and tool registry.
"""
import pytest

from app.support.agents.registry import (
    SPECIALIST_REGISTRY,
    get_specialist,
    list_specialists,
)
from app.support.agents.validator_agent import is_verifiable_response
from app.support.orchestrator import (
    SupportOrchestrator,
    _build_knowledge_base_refusal,
    _passes_support_scope_gate,
)
from app.support.tools.registry import TOOL_REGISTRY, get_tools_for_domain


class TestSpecialistRegistry:
    def test_technical_exists(self):
        spec = get_specialist("technical")
        assert spec.model_slot == "technical"
        assert spec.domain == "technical"
        assert spec.faithfulness_model_slot == "followup"

    def test_billing_exists(self):
        spec = get_specialist("billing")
        assert spec.model_slot == "billing"
        assert spec.domain == "billing"
        assert spec.faithfulness_model_slot == "generation"

    def test_unknown_specialist_raises(self):
        with pytest.raises(KeyError):
            get_specialist("nonexistent")

    def test_list_specialists(self):
        names = list_specialists()
        assert "technical" in names
        assert "billing" in names


class TestToolRegistry:
    def test_all_tools_registered(self):
        expected = [
            "voice_get_details",
            "mobile_get_details",
            "internet_get_details",
            "tv_get_details",
            "list_devices",
            "get_device",
            "get_outage_for_customer",
            "get_recent_tickets",
            "billing_get_invoice",
            "billing_list_charges",
            "billing_get_balance",
            "get_escalation_contact",
        ]
        for name in expected:
            assert name in TOOL_REGISTRY, f"Tool {name} not registered"

    def test_technical_tools(self):
        tools = get_tools_for_domain("technical")
        assert "voice_get_details" in tools
        assert "internet_get_details" in tools
        assert "get_escalation_contact" in tools  # shared
        assert "billing_get_invoice" not in tools

    def test_billing_tools(self):
        tools = get_tools_for_domain("billing")
        assert "billing_get_invoice" in tools
        assert "billing_get_balance" in tools
        assert "get_escalation_contact" in tools  # shared
        assert "voice_get_details" not in tools

    def test_all_tools_are_callable(self):
        for name, entry in TOOL_REGISTRY.items():
            assert callable(entry.func), f"Tool {name} is not callable"


class TestHandoffDetection:
    """Test the orchestrator's handoff detection logic."""

    def test_detect_billing_handoff_from_technical(self):
        # We test the method directly on an uninitialized instance
        orch = SupportOrchestrator.__new__(SupportOrchestrator)
        result = orch._detect_handoff(
            "This is a billing issue. The billing specialist would be more appropriate.",
            "technical",
        )
        assert result == "billing"

    def test_detect_technical_handoff_from_billing(self):
        orch = SupportOrchestrator.__new__(SupportOrchestrator)
        result = orch._detect_handoff(
            "This seems like a connectivity issue. The technical team should handle this.",
            "billing",
        )
        assert result == "technical"

    def test_no_handoff_when_same_specialist(self):
        orch = SupportOrchestrator.__new__(SupportOrchestrator)
        result = orch._detect_handoff(
            "Let me check your billing details. The billing specialist is on it.",
            "billing",
        )
        assert result is None

    def test_no_handoff_for_normal_response(self):
        orch = SupportOrchestrator.__new__(SupportOrchestrator)
        result = orch._detect_handoff(
            "Your internet speed looks good at 950 Mbps.",
            "technical",
        )
        assert result is None


class TestSupportGroundingHeuristic:
    def test_scope_gate_allows_strong_single_hit(self):
        assert _passes_support_scope_gate(
            top_score=0.93,
            coverage=1,
            scope_threshold=0.55,
            min_coverage_chunks=2,
        ) is True

    def test_scope_gate_rejects_borderline_single_hit(self):
        assert _passes_support_scope_gate(
            top_score=0.61,
            coverage=1,
            scope_threshold=0.55,
            min_coverage_chunks=2,
        ) is False

    def test_scope_gate_allows_multi_chunk_coverage(self):
        assert _passes_support_scope_gate(
            top_score=0.58,
            coverage=2,
            scope_threshold=0.55,
            min_coverage_chunks=2,
        ) is True

    def test_short_troubleshooting_steps_are_still_verifiable(self):
        response = (
            "Restart the gateway, confirm the coax cable is tight, and wait "
            "two minutes for the online light to stabilize."
        )
        assert is_verifiable_response(response) is True

    def test_canned_kb_refusal_is_not_verifiable(self):
        reply = _build_knowledge_base_refusal(
            "en",
            {
                "value": "1-800-TECH-HELP",
                "hours": "24/7",
                "url": "https://support.example.com/technical",
            },
        )
        assert is_verifiable_response(reply) is False
        assert "knowledge base" in reply
        assert "1-800-TECH-HELP" in reply

    def test_spanish_kb_refusal_is_not_verifiable(self):
        reply = _build_knowledge_base_refusal(
            "es",
            {
                "value": "1-800-TECH-HELP",
                "hours": "24/7",
                "url": "https://support.example.com/technical",
            },
        )
        assert is_verifiable_response(reply) is False
        assert "base de conocimientos" in reply
