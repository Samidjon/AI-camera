from datetime import timedelta

import jwt
import pytest

from app.core.config import Settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

SETTINGS = Settings(_env_file=None, jwt_secret="unit-test-secret-0123456789-0123456789")


def test_password_hash_roundtrip():
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong", h)


def test_overlong_password_rejected():
    with pytest.raises(ValueError):
        hash_password("x" * 73)
    assert verify_password("x" * 73, hash_password("x" * 72)) is False


def test_garbage_hash_does_not_raise():
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_token_roundtrip():
    token = create_access_token("user-1", SETTINGS, extra={"role": "viewer"})
    payload = decode_access_token(token, SETTINGS)
    assert payload["sub"] == "user-1"
    assert payload["role"] == "viewer"


def test_token_with_wrong_secret_rejected():
    token = create_access_token("user-1", SETTINGS)
    other = Settings(_env_file=None, jwt_secret="another-secret-0123456789-0123456789")
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(token, other)


def test_expired_token_rejected():
    import datetime as dt

    payload = {
        "sub": "u",
        "type": "access",
        "exp": dt.datetime.now(dt.UTC) - timedelta(seconds=5),
    }
    token = jwt.encode(payload, SETTINGS.jwt_secret, algorithm="HS256")
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token, SETTINGS)


def test_non_access_token_rejected():
    token = jwt.encode({"sub": "u", "type": "refresh"}, SETTINGS.jwt_secret, algorithm="HS256")
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token, SETTINGS)
