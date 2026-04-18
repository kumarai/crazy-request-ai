"""Pydantic models for support agent inputs/outputs."""
from __future__ import annotations

from pydantic import BaseModel


class SupportResult(BaseModel):
    """Structured output from the support agent."""
    answer: str
    citations: list[str] = []
    escalation: dict | None = None  # {topic, reason} if escalation needed
    handoff_to: str | None = None   # specialist name if handoff needed
    handoff_reason: str | None = None


class SupportFollowupQuestion(BaseModel):
    question: str
    category: str  # clarify | next_step | related_issue
