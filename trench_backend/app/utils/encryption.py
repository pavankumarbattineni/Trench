"""Encrypts BYOK credentials at rest using TRENCH_CONFIG.MCP.encryption_key.

One root secret is combined with a purpose string via PBKDF2 to derive a
distinct Fernet key per purpose, so a compromise of one purpose's derived
key doesn't expose others derived from the same root secret.
"""

import base64
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.config import get_settings


class DecryptionError(Exception):
    """Raised when a stored credential can't be decrypted (corrupted or key rotated)."""


@lru_cache
def _fernet_for(purpose: str) -> Fernet:
    root_secret = get_settings().TRENCH_CONFIG.MCP.encryption_key
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=f"trench_{purpose}_v1".encode(),
        iterations=100_000,
    )
    derived_key = base64.urlsafe_b64encode(kdf.derive(root_secret.encode()))
    return Fernet(derived_key)


def encrypt_secret(plaintext: str, *, purpose: str = "byok") -> str:
    return _fernet_for(purpose).encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str, *, purpose: str = "byok") -> str:
    try:
        return _fernet_for(purpose).decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionError("Stored credential could not be decrypted") from exc
