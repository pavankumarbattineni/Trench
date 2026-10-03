"""Thin wrapper around the Firebase Admin SDK -- no Trench business logic here."""

import base64
import json
import logging
from functools import lru_cache

import firebase_admin
import httpx
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials

from app.config import get_settings

logger = logging.getLogger(__name__)

_IDENTITY_TOOLKIT_SIGN_IN_URL = (
    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
)
# The one error Google returns for an actual wrong password (or an email
# with no account) -- expected, routine, and never worth logging. Any
# other failure (bad/restricted API key, email/password sign-in disabled
# for the project, etc.) is a misconfiguration masquerading as "wrong
# password" and must be loud, or it's undiagnosable from the outside.
_GENUINE_WRONG_CREDENTIALS = "INVALID_LOGIN_CREDENTIALS"


@lru_cache
def get_firebase_app() -> firebase_admin.App:
    settings = get_settings()
    raw_json = base64.b64decode(settings.TRENCH_CONFIG.FIREBASE.credentials_json_base64)
    cert_info = json.loads(raw_json)
    cred = credentials.Certificate(cert_info)
    return firebase_admin.initialize_app(cred)


def verify_firebase_id_token(id_token: str) -> dict:
    """Returns the decoded token claims (includes uid, email, iat, ...)."""
    get_firebase_app()
    return firebase_auth.verify_id_token(id_token)


def delete_firebase_user(firebase_uid: str) -> None:
    get_firebase_app()
    firebase_auth.delete_user(firebase_uid)


def set_user_password(email: str, new_password: str) -> None:
    """Updates a Firebase user's password via the Admin SDK, looked up by
    email (Trench stores no firebase_uid -- see User's docstring)."""
    get_firebase_app()
    user_record = firebase_auth.get_user_by_email(email)
    firebase_auth.update_user(user_record.uid, password=new_password)


async def verify_user_password(email: str, password: str) -> bool:
    """Checks a plaintext password against the one on file, via Firebase's
    Identity Toolkit REST API -- the Admin SDK (used everywhere else in
    this module) has no way to verify a password, only to look up/overwrite
    one. Needs FIREBASE.web_api_key (the public Web API key from Firebase
    Project settings, not the Admin SDK's service-account credentials).

    Returns:
        True if the password matches, False otherwise (wrong password, or
        no such account -- both come back from Google as an INVALID_
        LOGIN_CREDENTIALS 400, which is treated as "doesn't match"). Any
        other failure reason (bad API key, sign-in method disabled, ...)
        is logged at ERROR before returning False too, since silently
        treating every non-200 as "wrong password" makes a config problem
        indistinguishable from a typo'd password.
    """
    settings = get_settings()
    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(
            _IDENTITY_TOOLKIT_SIGN_IN_URL,
            params={"key": settings.TRENCH_CONFIG.FIREBASE.web_api_key},
            json={"email": email, "password": password, "returnSecureToken": False},
        )
    if response.status_code == 200:
        return True

    reason = response.json().get("error", {}).get("message", "")
    if reason != _GENUINE_WRONG_CREDENTIALS:
        logger.error(
            "verify_user_password failed for a reason other than a wrong "
            "password -- likely a FIREBASE.web_api_key misconfiguration, "
            "not an actual bad password | status=%s reason=%s",
            response.status_code,
            reason or response.text,
        )
    return False
