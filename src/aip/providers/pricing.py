"""Estimate the cost of one LLM call from token counts.

Prices come from the setting ``llm_price_per_mtok_json``, for example
``{"my-model": {"input": 1.0, "output": 2.0}}`` in USD per million tokens.
No vendor price is built into the code. A model without a price gives ``None``.
"""

import json

from aip.providers.base import Usage

PriceTable = dict[str, dict[str, float]]


def load_prices(raw: str) -> PriceTable:
    if not raw.strip():
        return {}
    data = json.loads(raw)
    return {str(model): {k: float(v) for k, v in prices.items()} for model, prices in data.items()}


def estimate_cost_usd(model: str | None, usage: Usage, table: PriceTable) -> float | None:
    prices = table.get(model or "")
    if not prices or "input" not in prices or "output" not in prices:
        return None
    cost = (usage.input_tokens * prices["input"] + usage.output_tokens * prices["output"]) / 1_000_000
    return round(cost, 8)
