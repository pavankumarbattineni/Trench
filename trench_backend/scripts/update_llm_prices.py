"""Regenerates app/data/llm_prices.json from LiteLLM's model price map.

LiteLLM is the source of truth for per-token prices (input, cached input,
output). It is deliberately *not* a runtime dependency: importing it costs
~200 MB of memory, which doesn't fit the backend's 512 MB budget. Instead
this script snapshots the slice of its price map Trench needs, and
LLMCostService reads that file.

Run whenever providers change prices or new models are added:

    uv run --with litellm python scripts/update_llm_prices.py
"""

import json
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import litellm

OUTPUT = Path(__file__).resolve().parent.parent / "app" / "data" / "llm_prices.json"
# LiteLLM provider names Trench dispatches to (see LLMClientService).
PROVIDERS = {"openai", "anthropic", "gemini", "groq"}
FIELDS = (
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_read_input_token_cost",
)


def main() -> None:
    models = {}
    for key, entry in sorted(litellm.model_cost.items()):
        if entry.get("litellm_provider") not in PROVIDERS:
            continue
        if entry.get("mode") not in ("chat", "responses", None):
            continue
        if any(entry.get(f) is None for f in FIELDS[:2]):
            continue
        models[key] = {f: entry[f] for f in FIELDS if entry.get(f) is not None}

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {
                "source": "litellm model_cost",
                "litellm_version": version("litellm"),
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "unit": "USD per token",
                "models": models,
            },
            indent=1,
            sort_keys=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(models)} model prices to {OUTPUT}")


if __name__ == "__main__":
    main()
