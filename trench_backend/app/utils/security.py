"""Stateless JWT helpers for Trench's own access/refresh tokens.

Both token types are self-contained JWTs signed with TRENCH_CONFIG.JWT.secret_key
-- there is no server-side session store. A refresh token remains valid until
it expires; the frontend is responsible for discarding it on logout.
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.config import get_settings


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired, or the wrong type."""


def _encode(user_id: uuid.UUID, token_type: str, expires_delta: timedelta) -> str:
    jwt_config = get_settings().TRENCH_CONFIG.JWT
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        # Ensures two tokens minted in the same second still differ, and
        # gives each token a stable identifier for logging/debugging.
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, jwt_config.secret_key, algorithm=jwt_config.algorithm)


def create_access_token(user_id: uuid.UUID) -> str:
    jwt_config = get_settings().TRENCH_CONFIG.JWT
    return _encode(
        user_id, "access", timedelta(minutes=jwt_config.access_token_expire_minutes)
    )


def create_refresh_token(user_id: uuid.UUID) -> str:
    jwt_config = get_settings().TRENCH_CONFIG.JWT
    return _encode(
        user_id, "refresh", timedelta(days=jwt_config.refresh_token_expire_days)
    )


def _decode(token: str, expected_type: str) -> uuid.UUID:
    jwt_config = get_settings().TRENCH_CONFIG.JWT
    try:
        payload = jwt.decode(
            token, jwt_config.secret_key, algorithms=[jwt_config.algorithm]
        )
    except jwt.PyJWTError as exc:
        raise TokenError("Invalid or expired token") from exc

    if payload.get("type") != expected_type:
        raise TokenError(f"Expected a {expected_type} token")

    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise TokenError("Token missing a valid subject") from exc


def decode_access_token(token: str) -> uuid.UUID:
    return _decode(token, "access")


def decode_refresh_token(token: str) -> uuid.UUID:
    return _decode(token, "refresh")
