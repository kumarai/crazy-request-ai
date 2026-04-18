"""Observability logging for customer-support requests.

Structured JSON logging for dashboards, replay, and debugging.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field

logger = logging.getLogger("[support:obs]")


@dataclass
class SupportRequestLog:
    conversation_id: str
    customer_id: str
    specialist_used: str
    router_confidence: float
    tools_called: list[str] = field(default_factory=list)
    tool_latencies: dict[str, int] = field(default_factory=dict)
    retrieval_scope: list[str] = field(default_factory=list)
    faithfulness_passed: bool = False
    handoff_occurred: bool = False
    handoff_from: str | None = None
    handoff_to: str | None = None
    total_latency_ms: int = 0
    retrieval_ms: int = 0
    generation_ms: int = 0
    validation_ms: int = 0


def log_support_request(log: SupportRequestLog) -> None:
    """Emit a structured JSON log entry for a support request."""
    logger.info(
        "support_request %s",
        json.dumps(asdict(log), default=str),
    )
