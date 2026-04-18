"""USD pricing per million tokens, keyed by model id.

Used to estimate the dollar cost of a single turn from the token counts
already surfaced in ``RunUsage``. Numbers are captured from the providers'
public pricing pages and are intentionally approximate — token accounting
for billing must come from the provider invoice, not from this file.

Add new models by dropping a new entry into :data:`MODEL_PRICING`. Missing
models resolve to ``None`` so the caller can hide the cost badge instead
of reporting a wrong number.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic_ai.usage import RunUsage


@dataclass(frozen=True)
class ModelPrice:
    input_per_1m_usd: float    # price per 1,000,000 input tokens
    output_per_1m_usd: float   # price per 1,000,000 output tokens


# Prices as of 2026-04 — verify before relying on these for billing.
MODEL_PRICING: dict[str, ModelPrice] = {
    # ---- OpenAI ----
    "gpt-4o":             ModelPrice(2.50, 10.00),
    "gpt-4o-mini":        ModelPrice(0.15,  0.60),
    "gpt-4.1":            ModelPrice(2.00,  8.00),
    "gpt-4.1-mini":       ModelPrice(0.40,  1.60),
    # ---- Anthropic ----
    "claude-sonnet-4-20250514":   ModelPrice(3.00, 15.00),
    "claude-haiku-4-5-20251001":  ModelPrice(0.80,  4.00),
    "claude-opus-4-20250514":     ModelPrice(15.00, 75.00),
    # ---- Google ----
    "gemini-2.0-flash":   ModelPrice(0.10, 0.40),
    "gemini-1.5-pro":     ModelPrice(1.25, 5.00),
    # ---- Ollama (self-hosted, no per-token cost) ----
    "llama3.1:8b":        ModelPrice(0.00, 0.00),
}


def get_price(model: str) -> ModelPrice | None:
    """Return pricing for a model id or ``None`` if unknown.

    Accepts bare model names as well as pydantic-ai-prefixed names like
    ``openai:gpt-4o`` — the prefix is stripped.
    """
    if ":" in model:
        # e.g. "openai:gpt-4o" -> "gpt-4o"
        model = model.split(":", 1)[1]
    return MODEL_PRICING.get(model)


def estimate_cost_usd(
    input_tokens: int,
    output_tokens: int,
    model: str,
) -> float | None:
    """Estimate USD cost for a single call. Returns ``None`` for unknown models."""
    price = get_price(model)
    if price is None:
        return None
    return (
        input_tokens  * price.input_per_1m_usd  / 1_000_000
      + output_tokens * price.output_per_1m_usd / 1_000_000
    )


@dataclass
class UsageAccumulator:
    """Sums :class:`RunUsage` and USD cost across a multi-call turn.

    A turn typically involves router + specialist + faithfulness + followup +
    action suggester, and each call may run on a different model slot. Cost
    is tracked per-call because the per-slot model (and therefore the per-1M
    price) differs. ``cost_usd`` stays ``None`` until at least one call
    contributes a priced model, so unknown-model turns don't lie about
    cost being "$0.00".
    """
    usage: RunUsage = field(default_factory=RunUsage)
    cost_usd: float | None = None

    def add(self, call_usage: RunUsage, model: str) -> None:
        self.usage.incr(call_usage)
        cost = estimate_cost_usd(
            call_usage.input_tokens, call_usage.output_tokens, model
        )
        if cost is not None:
            self.cost_usd = (self.cost_usd or 0.0) + cost
