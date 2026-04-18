"""Tests for the intent classifier hard rules.

LLM-fallback path is exercised in integration tests; here we verify the
deterministic regex catches obvious smalltalk and leaves real support
queries alone.
"""
import pytest

from app.agents.intent_agent import apply_hard_rules


class TestSmalltalkHardRules:
    @pytest.mark.parametrize(
        "msg",
        [
            "hi",
            "Hi!",
            "hello",
            "Hello.",
            "hey",
            "hey!",
            "yo",
            "howdy",
            "good morning",
            "Good evening!",
            "thanks",
            "Thank you",
            "thx",
            "ty",
            "appreciate it",
            "bye",
            "goodbye",
            "see ya",
            "later",
            "ok",
            "okay",
            "got it",
            "sounds good",
            "perfect",
            "how are you",
            "what's up",
        ],
    )
    def test_obvious_smalltalk_matches(self, msg: str):
        result = apply_hard_rules(msg)
        assert result is not None, f"expected smalltalk for {msg!r}"
        assert result.intent == "smalltalk"
        assert result.source == "hard_rule"
        # Hard-rule patterns are English-only; Spanish smalltalk falls
        # through to the LLM classifier.
        assert result.language == "en"

    def test_empty_message_is_smalltalk(self):
        result = apply_hard_rules("   ")
        assert result is not None
        assert result.intent == "smalltalk"
        assert result.language == "en"


class TestSupportQueriesNotMatched:
    @pytest.mark.parametrize(
        "msg",
        [
            "my internet is down",
            "wifi keeps dropping",
            "I need to pay my bill",
            "router won't connect",
            "is there an outage",
            "hi, can you help with my bill?",  # mixed -> falls through to LLM
            "thanks but I still need help with the modem",
        ],
    )
    def test_real_queries_not_hard_matched(self, msg: str):
        # Hard rule should return None so the LLM classifier runs.
        result = apply_hard_rules(msg)
        assert result is None, f"unexpected hard match for {msg!r}"

    def test_long_message_skipped(self):
        # Long messages are never smalltalk even if they begin with "hi".
        msg = "hi " + "x" * 100
        assert apply_hard_rules(msg) is None


class TestOffTopicNotHardMatched:
    """Off-topic detection lives in the LLM classifier, never the regex.

    The hard rule must NOT short-circuit off-topic questions to smalltalk
    — otherwise we'd warmly greet the user instead of redirecting them.
    """

    @pytest.mark.parametrize(
        "msg",
        [
            "what's the weather today",
            "tell me a joke",
            "who won the game last night",
            "what's a good pasta recipe",
            "what's the capital of France",
            "stock price of AAPL",
        ],
    )
    def test_off_topic_falls_through_to_llm(self, msg: str):
        result = apply_hard_rules(msg)
        assert result is None, (
            f"off-topic message {msg!r} should fall through to LLM, "
            f"not be hard-matched as smalltalk"
        )
