"""Thin wrapper around the Firebase Admin SDK -- no Trench business logic here."""

from functools import lru_cache

import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials

from app.config import get_settings


@lru_cache
def get_firebase_app() -> firebase_admin.App:
    settings = get_settings()
    cred = credentials.Certificate(settings.TRENCH_CONFIG.FIREBASE.credentials_path)
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
