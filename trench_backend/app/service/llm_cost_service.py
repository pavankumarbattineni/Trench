"""Prices LLM token usage with LiteLLM's model price map.

The prices live in app/data/llm_prices.json, a snapshot of LiteLLM's
`model_cost` (regenerate with scripts/update_llm_prices.py). LiteLLM itself
isn't imported at runtime -- it costs ~200 MB of memory -- but the cost
formula below is the same one LiteLLM's `cost_per_token` applies:

    (input - cached) * input price + cached * cached-input price
        + output * output price

where `input` is the *total* prompt token count, cached tokens included.
A model missing from the map is reported as unpriced (cost None), never as
a made-up zero.
"""

import json
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

_PRICES_FILE = Path(__file__).resolve().parent.parent / "data" / "llm_prices.json"

# Trench provider name -> LiteLLM's model-key prefix.
_LITELLM_PREFIX = {"groq": "groq/", "openai": "", "anthropic": "", "google": "gemini/"}


@dataclass(frozen=True)
class ModelPrice:
    input_per_token: Decimal
    output_per_token: Decimal
    cached_input_per_token: Decimal


@lru_cache
def _price_map() -> dict[str, dict]:
    return json.loads(_PRICES_FILE.read_text(encoding="utf-8"))["models"]


def price_for(provider_name: str, model_name: str) -> ModelPrice | None:
    models = _price_map()
    prefix = _LITELLM_PREFIX.get(provider_name, "")
    entry = models.get(prefix + model_name) or models.get(model_name)
    if entry is None:
        return None
    input_price = Decimal(str(entry["input_cost_per_token"]))
    cached = entry.get("cache_read_input_token_cost")
    return ModelPrice(
        input_per_token=input_price,
        output_per_token=Decimal(str(entry["output_cost_per_token"])),
        # No separate cached price published -> cached tokens cost the same
        # as regular input (LiteLLM's behaviour too).
        cached_input_per_token=Decimal(str(cached))
        if cached is not None
        else input_price,
    )


def compute_cost(
    *,
    provider_name: str,
    model_name: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
) -> Decimal | None:
    """USD cost of one call, or None when the model isn't in the price map."""
    price = price_for(provider_name, model_name)
    if price is None:
        return None
    cached = max(0, min(cached_input_tokens, input_tokens))
    return (
        (input_tokens - cached) * price.input_per_token
        + cached * price.cached_input_per_token
        + output_tokens * price.output_per_token
    )


def cache_savings(
    *, provider_name: str, model_name: str, cached_input_tokens: int
) -> Decimal | None:
    """How much cheaper the cached tokens were than regular input."""
    price = price_for(provider_name, model_name)
    if price is None:
        return None
    return cached_input_tokens * (price.input_per_token - price.cached_input_per_token)
