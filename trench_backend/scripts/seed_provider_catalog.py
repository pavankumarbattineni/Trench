"""Seeds the LLM catalog (`providers` + `provider_models`) to exactly the set
below. Idempotent: safe to run any number of times -- rows are upserted by
(provider name) and (provider, model_name), never duplicated.

Models in the database that are NOT listed below are marked
is_active=False (not deleted), so they disappear from the model selector
without breaking any user whose saved preference still points at them.

Provider `name` values must match what LLMClientService dispatches on
("openai", "anthropic", "google", "groq"). The platform default must be a
Groq model: it runs on the platform's own Groq key (TRENCH_CONFIG.GROQ),
while every other provider needs the user's/tenant's BYOK credential.

Model IDs were taken from each provider's official model docs (Nov 2026).
OpenAI "-pro" models are deliberately excluded: they are Responses-API-only
and LLMClientService calls OpenAI via Chat Completions.

Usage (from trench_backend/):
    uv run python scripts/seed_provider_catalog.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update  # noqa: E402

from app.database.models import Provider, ProviderModel  # noqa: E402
from app.database.session import async_session_factory  # noqa: E402

DEFAULT_MODEL = ("groq", "openai/gpt-oss-120b")

# provider name -> (provider display name, [(model_name, display_name, is_visible)])
CATALOG: dict[str, tuple[str, list[tuple[str, str, bool]]]] = {
    "groq": (
        "Groq",
        [
            ("openai/gpt-oss-120b", "GPT-OSS 120B", True),
            ("qwen/qwen3.8-27b", "Qwen3.8 27B", True),
        ],
    ),
    "openai": (
        "OpenAI",
        [
            ("gpt-6-astra", "GPT-6 Astra", True),
            ("gpt-6.1-sol", "GPT-6.1 Sol", True),
            ("gpt-6.1-terra", "GPT-6.1 Terra", True),
            ("gpt-6-sol", "GPT-6 Sol", True),
            ("gpt-6-luna", "GPT-6 Luna", True),
            ("gpt-5.6-sol", "GPT-5.6 Sol", True),
            ("gpt-5.6-terra", "GPT-5.6 Terra", True),
            ("gpt-5.6-luna", "GPT-5.6 Luna", True),
            ("gpt-5.5", "GPT-5.5", True),
            ("gpt-5.4", "GPT-5.4", True),
            ("gpt-5.4-mini", "GPT-5.4 Mini", True),
            ("gpt-5.2", "GPT-5.2", True),
        ],
    ),
    "anthropic": (
        "Anthropic",
        [
            ("claude-fable-5-1", "Claude Fable 5.1", True),
            ("claude-opus-5-5", "Claude Opus 5.5", True),
            ("claude-sonnet-5-5", "Claude Sonnet 5.5", True),
            ("claude-haiku-5-5", "Claude Haiku 5.5", True),
            ("claude-fable-5", "Claude Fable 5", True),
            ("claude-opus-5", "Claude Opus 5", True),
            ("claude-sonnet-5", "Claude Sonnet 5", True),
            ("claude-opus-4-8", "Claude Opus 4.8", True),
            ("claude-opus-4-7", "Claude Opus 4.7", True),
            ("claude-opus-4-6", "Claude Opus 4.6", True),
            ("claude-sonnet-4-6", "Claude Sonnet 4.6", True),
            ("claude-opus-4-5-20251101", "Claude Opus 4.5", True),
            ("claude-haiku-4-5-20251001", "Claude Haiku 4.5", True),
        ],
    ),
    "google": (
        "Google Gemini",
        [
            ("gemini-4-argon", "Gemini 4 Argon", True),
            ("gemini-3.8-flash", "Gemini 3.8 Flash", True),
            ("gemini-3.6-flash", "Gemini 3.6 Flash", True),
            ("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite", True),
            ("gemini-3.1-pro-preview", "Gemini 3.1 Pro (Preview)", True),
            ("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite", True),
            ("gemini-3-flash-preview", "Gemini 3 Flash (Preview)", True),
        ],
    ),
}


async def seed() -> None:
    async with async_session_factory() as db:
        wanted: set[tuple[str, str]] = set()
        default_row: ProviderModel | None = None

        for pname, (pdisplay, models) in CATALOG.items():
            provider = (
                await db.execute(select(Provider).where(Provider.name == pname))
            ).scalar_one_or_none()
            if provider is None:
                provider = Provider(name=pname, display_name=pdisplay)
                db.add(provider)
                await db.flush()
            provider.display_name = pdisplay
            provider.is_active = True

            for model_name, display, visible in models:
                wanted.add((pname, model_name))
                row = (
                    await db.execute(
                        select(ProviderModel).where(
                            ProviderModel.provider_id == provider.id,
                            ProviderModel.model_name == model_name,
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    row = ProviderModel(
                        provider_id=provider.id,
                        model_name=model_name,
                        display_name=display,
                        is_platform_default=False,
                    )
                    db.add(row)
                row.display_name = display
                row.is_active = True
                row.is_visible = visible
                if (pname, model_name) == DEFAULT_MODEL:
                    default_row = row

        await db.flush()
        if default_row is None:
            raise SystemExit(f"default model {DEFAULT_MODEL} is not in CATALOG")

        # Exactly one platform default (partial unique index allows at most one).
        await db.execute(
            update(ProviderModel)
            .where(ProviderModel.id != default_row.id)
            .values(is_platform_default=False)
        )
        await db.flush()
        default_row.is_platform_default = True

        # Deactivate anything in the DB that isn't in CATALOG.
        rows = (
            await db.execute(
                select(Provider.name, ProviderModel).join(
                    Provider, Provider.id == ProviderModel.provider_id
                )
            )
        ).all()
        for pname, row in rows:
            if (pname, row.model_name) not in wanted and row.is_active:
                row.is_active = False
                print(f"deactivated {pname}/{row.model_name}")
        for provider in (await db.execute(select(Provider))).scalars():
            if provider.name not in CATALOG:
                provider.is_active = False

        await db.commit()

        rows = (
            await db.execute(
                select(Provider.name, ProviderModel)
                .join(Provider, Provider.id == ProviderModel.provider_id)
                .order_by(Provider.name, ProviderModel.model_name)
            )
        ).all()
        print(
            f"{'provider':<10} {'model_name':<28} {'active':<7} {'visible':<8} default"
        )
        for pname, r in rows:
            print(
                f"{pname:<10} {r.model_name:<28} {str(r.is_active):<7} "
                f"{str(r.is_visible):<8} {'*' if r.is_platform_default else ''}"
            )


if __name__ == "__main__":
    asyncio.run(seed())
