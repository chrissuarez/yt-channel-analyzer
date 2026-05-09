"""Model-keyed pricing table for LLM cost estimation.

Prices are USD per 1M tokens, mirrored from Anthropic's published list
pricing. The Anthropic batch API discounts both directions by 50%.
Unknown models or missing token counts return None so the caller can
write NULL to ``llm_calls.cost_estimate_usd`` without special-casing.
"""
from __future__ import annotations

from typing import Optional


_BATCH_DISCOUNT = 0.5
_PER_MILLION = 1_000_000


MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-7": (15.00, 75.00),
}


def estimate_cost(
    model: Optional[str],
    tokens_in: Optional[int],
    tokens_out: Optional[int],
    *,
    is_batch: bool = False,
) -> Optional[float]:
    if model is None or tokens_in is None or tokens_out is None:
        return None
    prices = MODEL_PRICES.get(model)
    if prices is None:
        return None
    input_price, output_price = prices
    cost = (tokens_in * input_price + tokens_out * output_price) / _PER_MILLION
    if is_batch:
        cost *= _BATCH_DISCOUNT
    return cost
