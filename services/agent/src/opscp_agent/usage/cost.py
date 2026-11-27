"""An estimate of what a run's model calls cost. It is an estimate, never a bill."""

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from opscp_agent.llm.base import Usage

_PAISE = Decimal("0.01")


class _Prices(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input: Decimal = Field(ge=0)
    cached_input: Decimal | None = Field(default=None, ge=0)
    output: Decimal | None = Field(default=None, ge=0)


class Pricing(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    per_tokens: int = Field(gt=0)
    models: dict[str, _Prices]


def load_pricing(path: Path) -> Pricing:
    with path.open(encoding="utf-8") as handle:
        return Pricing.model_validate(yaml.safe_load(handle))


class CostEstimate:
    def __init__(self, pricing: Pricing, usd_inr_rate: Decimal) -> None:
        self._pricing = pricing
        self._rate = usd_inr_rate

    def inr(self, model: str, usage: Usage) -> Decimal | None:
        """None for a model with no listed price: an unknown cost is not shown as zero."""
        prices = self._pricing.models.get(model)
        if prices is None or prices.output is None:
            return None
        cached = prices.input if prices.cached_input is None else prices.cached_input
        fresh_in = max(usage.tokens_in - usage.tokens_cached, 0)
        usd = (
            fresh_in * prices.input
            + usage.tokens_cached * cached
            + usage.tokens_out * prices.output
        ) / self._pricing.per_tokens
        return (usd * self._rate).quantize(_PAISE, rounding=ROUND_HALF_UP)
