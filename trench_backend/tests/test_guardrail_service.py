from app.service.guardrail_service import GuardrailService


def test_check_output_leaves_clean_text_unchanged():
    text = "The report shows Q3 revenue grew by 12%."

    sanitized, flags = GuardrailService.check_output(text)

    assert sanitized == text
    assert flags == []


# Each case is (true_positive_text, benign_near_miss_text) -- the near
# miss is deliberately just short of the real shape (too few characters,
# missing a required prefix/segment, ...) and must never be redacted,
# matching the narrow-pattern/low-false-positive design (see
# guardrail_service.py's module docstring).
_PATTERN_CASES = {
    "openai_anthropic": (
        "Key: sk-abcdefghijklmnopqrstuvwxyz0123456789ABCD end.",
        "Order code: sk-short123 (not a real key).",
    ),
    "google": (
        "Key: AIzaSyD4f6g8h0j2k4m6n8p0q2r4s6t8u0v2w4x6y8z end.",
        "Reference: AIzaTooShort end.",
    ),
    "groq": (
        "Key: gsk_abcdefghijklmnopqrstuvwxyzABCD end.",
        "Label: gsk_short end.",
    ),
    "github_classic": (
        "Token: ghp_abcdefghijklmnopqrstuvwxyz0123456789 end.",
        "Prefix only: ghp_tooShort end.",
    ),
    "github_fine_grained": (
        "Token: github_pat_abcdefghijklmnopqrstuvwxyz end.",
        "Prefix only: github_pat_short end.",
    ),
    "aws": (
        "Key id: AKIAABCDEFGHIJKLMNOP end.",
        "Not quite: AKIASHORT end.",
    ),
    "slack": (
        "Token: xoxb-1234567890-abcdefghij end.",
        "Not quite: xoxb-short end.",
    ),
    "pem_private_key": (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIBOgIBAAJBAK...\n"
        "-----END RSA PRIVATE KEY-----",
        "Mentions a -----BEGIN CERTIFICATE----- block, not a private key.",
    ),
    "url_with_password": (
        "Connection string: postgres://user:hunter2@db.example.com/app",
        "Public URL: https://example.com/path (no credentials embedded).",
    ),
}


def test_check_output_redacts_every_documented_secret_pattern():
    for name, (true_positive, _benign) in _PATTERN_CASES.items():
        sanitized, flags = GuardrailService.check_output(true_positive)
        assert sanitized != true_positive, f"{name}: expected a redaction"
        assert "[REDACTED]" in sanitized, f"{name}: expected [REDACTED] marker"
        assert flags, f"{name}: expected a flag"


def test_check_output_does_not_redact_a_near_miss_for_any_pattern():
    for name, (_true_positive, benign) in _PATTERN_CASES.items():
        sanitized, flags = GuardrailService.check_output(benign)
        assert sanitized == benign, f"{name}: benign near-miss was redacted"
        assert flags == [], f"{name}: benign near-miss raised a flag"
