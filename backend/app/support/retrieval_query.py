"""Support-specific query rewriting for retrieval.

The customer-facing agent should answer the original question verbatim,
but retrieval benefits from stripping conversational filler and adding a
small amount of telecom-specific vocabulary. This keeps the search side
deterministic and cheap without turning on LLM query expansion, whose
current prompt is code-oriented.
"""
from __future__ import annotations

import re

_LEADING_CHATTER_RE = re.compile(
    r"^\s*(?:"
    r"(?:hi|hello|hey|hiya|yo|howdy)\b[\s,!.?-]*"
    r"|(?:please|pls)\b[\s,!.?-]*"
    r"|(?:can you|could you|would you)\s+(?:please\s+)?"
    r"|(?:i(?: am|'m))\s+(?:having|seeing|getting)\s+"
    r"|(?:my|our)\s+"
    r")+",
    re.IGNORECASE,
)
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "be",
    "been",
    "being",
    "but",
    "for",
    "from",
    "have",
    "how",
    "i",
    "im",
    "is",
    "it",
    "its",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "please",
    "pretty",
    "really",
    "so",
    "that",
    "the",
    "their",
    "there",
    "to",
    "very",
    "was",
    "we",
    "what",
    "why",
    "with",
}

_TERM_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "bill": ("billing", "invoice", "payment"),
    "billing": ("bill", "invoice", "charges", "payment"),
    "buffering": ("slow", "speed"),
    "charges": ("billing", "bill", "invoice"),
    "gateway": ("router", "modem", "wifi"),
    "internet": ("home internet", "broadband", "wifi"),
    "invoice": ("billing", "bill", "charges"),
    "lag": ("slow", "speed"),
    "message": ("text", "sms", "otp"),
    "messages": ("text", "sms", "otp"),
    "modem": ("gateway", "router", "internet"),
    "otp": ("text", "sms", "message"),
    "payment": ("billing", "bill", "invoice"),
    "router": ("gateway", "modem", "wifi"),
    "signal": ("coverage", "network"),
    "sim": ("sim card", "mobile"),
    "slow": ("speed", "performance"),
    "sms": ("text", "message", "otp"),
    "text": ("sms", "message", "otp"),
    "texts": ("sms", "message", "otp"),
    "wifi": ("wi fi", "wireless", "internet"),
}


def build_support_retrieval_query(query: str) -> str:
    """Return a retrieval-optimized query for support KB search.

    Rules:
    - Preserve the original query when we cannot extract anything useful.
    - Strip common greetings and conversational filler.
    - Keep salient terms in order, then append small deterministic telecom
      expansions and a few phrase-level boosts.
    """
    stripped = query.strip()
    if not stripped:
        return query

    normalized = _LEADING_CHATTER_RE.sub("", stripped)
    normalized = _NON_WORD_RE.sub(" ", normalized.lower()).strip()
    if not normalized:
        return stripped

    base_terms: list[str] = []
    seen_terms: set[str] = set()
    for token in normalized.split():
        if len(token) <= 1 or token in _STOPWORDS:
            continue
        if token in seen_terms:
            continue
        seen_terms.add(token)
        base_terms.append(token)

    if not base_terms:
        return stripped

    expanded_terms = list(base_terms)
    for term in base_terms:
        for expansion in _TERM_EXPANSIONS.get(term, ()):
            if expansion not in expanded_terms:
                expanded_terms.append(expansion)

    base_term_set = set(base_terms)
    if (
        {"internet", "wifi", "wireless", "broadband"} & base_term_set
        and {"slow", "lag", "buffering"} & base_term_set
    ):
        expanded_terms.extend(
            phrase
            for phrase in ("slow internet", "internet speed", "slow wifi")
            if phrase not in expanded_terms
        )

    if {"bill", "billing", "invoice", "payment", "charges"} & base_term_set:
        expanded_terms.extend(
            phrase
            for phrase in ("billing issue", "account charges")
            if phrase not in expanded_terms
        )

    return " ".join(expanded_terms)
