"""Tests for language utilities.

Language *detection* lives in the intent classifier now (see
``test_intent.py``); this module just owns the directive renderer and
the canned bilingual rejection text.
"""
from app.agents.language_agent import (
    LANGUAGE_LABELS,
    SUPPORTED_LANGUAGES,
    UNSUPPORTED_LANGUAGE_REPLY,
    language_directive,
)


class TestLanguageDirective:
    def test_english_directive(self):
        assert language_directive("en") == "Respond in English."

    def test_spanish_directive(self):
        assert language_directive("es") == "Respond in Spanish."

    def test_unknown_falls_back_to_english_label(self):
        # Defensive: shouldn't be called with anything other than en/es
        # in practice, but we don't want to raise.
        assert language_directive("zz") == "Respond in English."


class TestSupportedLanguagesSet:
    def test_only_en_and_es(self):
        # Mirrors the values the intent classifier may return for
        # ``language`` (excluding "unsupported", which is handled
        # separately by the orchestrator's short-circuit).
        assert SUPPORTED_LANGUAGES == frozenset({"en", "es"})

    def test_labels_cover_supported_set(self):
        for code in SUPPORTED_LANGUAGES:
            assert code in LANGUAGE_LABELS


class TestUnsupportedReplyContent:
    def test_reply_is_bilingual(self):
        # Sanity check: the canned rejection mentions both languages.
        assert "English" in UNSUPPORTED_LANGUAGE_REPLY
        assert "Spanish" in UNSUPPORTED_LANGUAGE_REPLY
        assert "inglés" in UNSUPPORTED_LANGUAGE_REPLY
        assert "español" in UNSUPPORTED_LANGUAGE_REPLY
