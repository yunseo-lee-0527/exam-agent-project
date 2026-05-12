from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


def estimate_tokens(text: str) -> int:
    """Lightweight token estimate for planning and simulation.

    English text usually averages around 4 characters/token. This intentionally
    remains provider-neutral; exact billing should be read from provider usage
    metadata when available.
    """

    return max(1, len(text) // 4) if text else 0


@dataclass
class UsageRecord:
    stage: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float


class UsageTracker:
    def __init__(self, price_table: dict[str, Any] | None = None):
        self.price_table = price_table or {}
        self.records: list[UsageRecord] = []

    def record(self, stage: str, model: str, prompt: str, response: str = "") -> None:
        input_tokens = estimate_tokens(prompt)
        output_tokens = estimate_tokens(response)
        prices = self.price_table.get(model, {})
        input_price = float(prices.get("input", 0.0))
        output_price = float(prices.get("output", 0.0))
        cost = (input_tokens * input_price + output_tokens * output_price) / 1_000_000
        self.records.append(UsageRecord(stage, model, input_tokens, output_tokens, cost))

    def summary(self) -> dict[str, Any]:
        by_model: dict[str, dict[str, Any]] = {}
        for record in self.records:
            bucket = by_model.setdefault(
                record.model,
                {"calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0},
            )
            bucket["calls"] += 1
            bucket["input_tokens"] += record.input_tokens
            bucket["output_tokens"] += record.output_tokens
            bucket["estimated_cost_usd"] += record.estimated_cost_usd

        return {
            "calls": len(self.records),
            "estimated_input_tokens": sum(r.input_tokens for r in self.records),
            "estimated_output_tokens": sum(r.output_tokens for r in self.records),
            "estimated_cost_usd": round(sum(r.estimated_cost_usd for r in self.records), 6),
            "by_model": by_model,
            "records": [asdict(r) for r in self.records],
        }

