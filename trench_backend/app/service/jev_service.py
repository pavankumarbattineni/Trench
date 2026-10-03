"""Wraps TypeSafe/JEV's "Noul" primitive -- a cheap, deterministic 0.0-1.0
probability answering one narrow yes/no question about a given state,
used as the decision function at the two self-assessment points in the
agentic RAG graph (retrieval-sufficiency, per-chunk citation relevance).
See docs/superpowers/specs/2026-10-01-agentic-rag-jev-architecture.md.

Stub mode: until TRENCH_CONFIG.TYPESAFE.api_key is configured, every call
falls back to a caller-supplied heuristic rather than failing or blocking
-- JEV is an enhancement to decision quality, never a hard dependency a
chat turn can't survive without (see the architecture doc's §8 reliability
table). The real SDK call is intentionally not implemented yet: TypeSafe's
public docs are intro-level only, with no package name, auth flow, or
method signatures to build against -- that lands once real API access
and their actual reference docs are available.
"""

from collections.abc import Callable

from app.config import get_settings


class JevService:
    @staticmethod
    def is_configured() -> bool:
        """Whether a real TypeSafe/JEV API key is set -- False means every
        ask_noul() call uses its caller's stub_fallback instead."""
        return bool(get_settings().TRENCH_CONFIG.TYPESAFE.api_key)

    @staticmethod
    async def ask_noul(question: str, *, stub_fallback: Callable[[], float]) -> float:
        """Returns a 0.0-1.0 probability answering `question`.

        Args:
            question: The narrow, single yes/no question being asked
                (purely documentary in stub mode; passed to the real JEV
                call once implemented).
            stub_fallback: A zero-argument callable returning the
                heuristic's own 0.0-1.0 probability, used verbatim while
                JevService.is_configured() is False.

        Returns:
            The real JEV score once implemented and configured; the
            stub_fallback's result otherwise.
        """
        if not JevService.is_configured():
            return stub_fallback()
        raise NotImplementedError(
            "Real TypeSafe/JEV integration is not implemented yet -- "
            "TRENCH_CONFIG.TYPESAFE.api_key is set, but there is no SDK "
            "call to make with it until TypeSafe's actual API reference "
            "is available."
        )
