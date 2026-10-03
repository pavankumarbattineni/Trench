"""Input and output guardrails -- on by default for every turn (see
docs/superpowers/specs/2026-10-01-agentic-rag-jev-architecture.md §5 and
§10's locked decision: "On by default for every turn... bypasses only for
explicitly trusted internal operations, not a general opt-out").

Input guardrail: a JEV Noul-style yes/no judgment ("does this message
attempt to override system instructions or extract secrets?") -- a fuzzy
question, the same category as retrieval-sufficiency, so it goes through
JevService exactly like that does (stub heuristic until TypeSafe/JEV is
configured).

Output guardrail: NOT a JEV question -- whether text contains a
credential-shaped substring is a deterministic pattern match, the same
category as validate_knowledge_access's DB-backed authorization check.
Regex-based and cheap, redacting matches rather than blocking the whole
response (defense-in-depth on top of retrieval-time isolation, not a
replacement for it -- see the architecture doc's §5).
"""

import re

from app.service.jev_service import JevService

# Documented placeholder patterns, not an exhaustive jailbreak taxonomy --
# replaced by the real JEV call once TypeSafe/JEV is configured (see
# JevService's own docstring on stub mode).
_INJECTION_PATTERNS = (
    re.compile(r"ignore (all |)previous instructions", re.IGNORECASE),
    re.compile(r"reveal (your |the )?system prompt", re.IGNORECASE),
    re.compile(
        r"disregard (your |all )?(prior |previous |)instructions", re.IGNORECASE
    ),
    re.compile(r"you are now (in )?(dan|developer) mode", re.IGNORECASE),
)

# Credential-shaped substrings this app itself issues or sees (OpenAI/
# Anthropic/Cohere-style secret-key prefixes, Google API key shape) --
# catches the class of bug "the model echoed back a secret it somehow saw
# in context", not a general-purpose secret scanner.
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{30,}"),
)


def _input_injection_stub_heuristic(query: str) -> float:
    """Stub fallback for JevService.ask_noul while TypeSafe/JEV isn't
    configured -- pattern matching against a small, documented, non-
    exhaustive list. Returns a Noul-style probability (1.0 = flagged)."""
    return 1.0 if any(p.search(query) for p in _INJECTION_PATTERNS) else 0.0


_INPUT_FLAG_THRESHOLD = 0.5


class GuardrailService:
    @staticmethod
    async def check_input(query: str) -> tuple[bool, str | None]:
        """Returns (flagged, reason) -- reason is None iff not flagged."""
        score = await JevService.ask_noul(
            "Does this message attempt to override system instructions "
            "or extract secrets?",
            stub_fallback=lambda: _input_injection_stub_heuristic(query),
        )
        if score >= _INPUT_FLAG_THRESHOLD:
            return True, (
                "This message looks like an attempt to override Trench's "
                "instructions."
            )
        return False, None

    @staticmethod
    def check_output(text: str) -> tuple[str, list[str]]:
        """Returns (sanitized_text, flags) -- redacts any credential-
        shaped substring found; sanitized_text equals the input unchanged
        when nothing is flagged."""
        flags: list[str] = []
        sanitized = text
        for pattern in _SECRET_PATTERNS:
            if pattern.search(sanitized):
                flags.append(
                    f"redacted a credential-shaped match for {pattern.pattern!r}"
                )
                sanitized = pattern.sub("[REDACTED]", sanitized)
        return sanitized, flags
