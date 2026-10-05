"""Output guardrail -- on by default for every turn.

Redacts credential-shaped substrings from the generated response: a
deterministic pattern match, the same category as validate_knowledge_
access's DB-backed authorization check. Cheap, best-effort, defense-in-
depth on top of retrieval-time isolation, not a replacement for it, and
not a general-purpose secret scanner -- see check_output's own docstring
for the known streaming limitation.

There used to be an input guardrail here too (a local pattern match for
prompt-injection/instruction-override phrasings). It was removed: real
protection against a query that would misuse retrieval or generation
already comes from validate_knowledge_access's scope-checked
authorization and from the system prompt's own rules (see
rag_graph._BASE_SYSTEM_PROMPT), and the input guardrail added a second,
weaker, pattern-matched layer of the same thing without actually gating
anything those don't already gate.
"""

import re

# Credential-shaped substrings this app itself issues, sees in BYOK
# credentials, or could plausibly see echoed back in a generated response
# -- catches the class of bug "the model repeated a secret it somehow saw
# in context", not a general-purpose secret scanner. Each pattern is
# deliberately narrow (a real provider's documented key shape) to keep
# false positives low; a near-miss string just short of the real shape is
# expected to pass through unredacted (see test_guardrail_service.py).
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),  # OpenAI / Anthropic
    re.compile(r"AIza[A-Za-z0-9_-]{30,}"),  # Google
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),  # Groq
    re.compile(r"gh[pos]_[A-Za-z0-9]{30,}"),  # GitHub (personal/OAuth/server tokens)
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),  # GitHub (fine-grained PAT)
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack
    # PEM private key block, any key type ("RSA PRIVATE KEY", "EC PRIVATE
    # KEY", "PRIVATE KEY", ...) -- DOTALL so "." spans the embedded
    # newlines of the key body between the BEGIN/END lines.
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    # A URL with a plaintext password embedded in its userinfo component
    # (scheme://user:password@host), e.g. a leaked DB connection string.
    re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:@/]+:[^\s@]+@[^\s/]+", re.IGNORECASE),
)


class GuardrailService:
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
