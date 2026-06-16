"""Per-model pricing in USD per 1M tokens (vendored from internal tokens-core).

Snapshot as of 2026-06. Update when providers change rates:
  https://openai.com/pricing
  https://www.anthropic.com/pricing

Eventually this will be split into a community-maintained `coffer-pricing`
package with a GitHub Action that scrapes provider docs. For now, vendored
so coffer-cli is a single-package install on PyPI.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    provider: str
    model: str
    input_per_million: float
    output_per_million: float
    cached_input_per_million: float | None = None


MODEL_PRICING: dict[str, ModelPricing] = {
    # OpenAI ----------------------------------------------------------------
    "gpt-4o": ModelPricing(
        provider="openai",
        model="gpt-4o",
        input_per_million=2.50,
        output_per_million=10.00,
        cached_input_per_million=1.25,
    ),
    "gpt-4o-mini": ModelPricing(
        provider="openai",
        model="gpt-4o-mini",
        input_per_million=0.15,
        output_per_million=0.60,
        cached_input_per_million=0.075,
    ),
    # Anthropic -- expand in Week 6 ----------------------------------------
}


def compute_cost(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> float:
    """USD cost for one LLM call. Unknown models return 0.0."""
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return 0.0

    fresh_input_tokens = max(input_tokens - cached_input_tokens, 0)
    cached_rate = pricing.cached_input_per_million or pricing.input_per_million

    input_cost = fresh_input_tokens / 1_000_000 * pricing.input_per_million
    cached_cost = cached_input_tokens / 1_000_000 * cached_rate
    output_cost = output_tokens / 1_000_000 * pricing.output_per_million

    return round(input_cost + cached_cost + output_cost, 8)
