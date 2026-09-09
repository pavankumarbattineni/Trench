import uuid

import pytest

from app.utils.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)


def test_access_token_round_trip():
    user_id = uuid.uuid4()
    token = create_access_token(user_id)
    assert decode_access_token(token) == user_id


def test_refresh_token_round_trip():
    user_id = uuid.uuid4()
    token = create_refresh_token(user_id)
    assert decode_refresh_token(token) == user_id


def test_access_token_rejected_as_refresh_token():
    token = create_access_token(uuid.uuid4())
    with pytest.raises(TokenError):
        decode_refresh_token(token)


def test_refresh_token_rejected_as_access_token():
    token = create_refresh_token(uuid.uuid4())
    with pytest.raises(TokenError):
        decode_access_token(token)


def test_garbage_token_raises_token_error():
    with pytest.raises(TokenError):
        decode_access_token("not-a-jwt")
