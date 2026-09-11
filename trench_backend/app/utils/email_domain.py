"""Extracts and validates the organization-eligible domain from an email
address.

An organization's identity is its email domain (e.g. "acme.com") -- see
Organization.domain. Public/free email providers can't anchor an
organization: anyone could claim "gmail.com" and there's no meaningful
sense in which its users share an employer, so those domains are rejected
outright rather than trusted like a real company domain.
"""

# Not exhaustive (no list of every free-mail provider is), but covers the
# large, well-known providers that would otherwise let anyone claim an
# "organization domain" that isn't actually company-specific.
_PUBLIC_EMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.co.uk",
    "yahoo.co.in",
    "ymail.com",
    "outlook.com",
    "hotmail.com",
    "hotmail.co.uk",
    "live.com",
    "msn.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "aol.com",
    "protonmail.com",
    "proton.me",
    "pm.me",
    "mail.com",
    "gmx.com",
    "gmx.net",
    "zoho.com",
    "yandex.com",
    "yandex.ru",
    "qq.com",
    "163.com",
    "126.com",
    "rediffmail.com",
    "inbox.com",
    "fastmail.com",
    "hey.com",
    "tutanota.com",
}


def extract_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower()


def is_public_email_domain(domain: str) -> bool:
    return domain.lower() in _PUBLIC_EMAIL_DOMAINS
