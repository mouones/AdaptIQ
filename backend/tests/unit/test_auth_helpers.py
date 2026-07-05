"""Regression tests for test auth helpers behavior."""

import time
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from jose import jwt

from config import JWT_ALGORITHM, JWT_SECRET_KEY
from routers.auth import (
    _clear_expired_ban,
    _create_access_token,
    _hash_password,
    _is_user_banned_now,
    _db_utc_now,
    _verify_password,
    get_current_user,
)


def test_password_hash_and_verify_roundtrip() -> None:
    password = "TestPass123!"
    password_hash = _hash_password(password)

    assert password_hash != password
    assert _verify_password(password, password_hash) is True
    assert _verify_password("wrong-password", password_hash) is False


def test_create_access_token_contains_expected_claims() -> None:
    user_id = str(uuid.uuid4())
    token = _create_access_token(user_id)

    payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])

    assert payload.get("sub") == user_id
    assert isinstance(payload.get("iat"), int)
    assert isinstance(payload.get("exp"), int)
    assert payload["exp"] > payload["iat"]

    jti = payload.get("jti")
    assert isinstance(jti, str)
    uuid.UUID(jti)


def test_is_user_banned_now_true_for_future_expiry() -> None:
    now = _db_utc_now()
    user = SimpleNamespace(ban_until=now + timedelta(minutes=5), ban_reason="temp ban")

    assert _is_user_banned_now(user, now=now) is True


def test_clear_expired_ban_resets_ban_fields() -> None:
    now = _db_utc_now()
    user = SimpleNamespace(ban_until=now - timedelta(minutes=1), ban_reason="expired")

    changed = _clear_expired_ban(user, now=now)

    assert changed is True
    assert user.ban_until is None
    assert user.ban_reason is None


@pytest.mark.asyncio
async def test_get_current_user_rejects_missing_bearer_token() -> None:
    class _Db:
        async def get(self, *_args, **_kwargs):
            raise AssertionError("db.get must not be called")

    with pytest.raises(HTTPException) as exc:
        await get_current_user(request=None, authorization=None, db=_Db())

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_rejects_non_uuid_sub() -> None:
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "not-a-uuid",
            "iat": now,
            "exp": now + 60,
            "jti": str(uuid.uuid4()),
        },
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )

    class _Db:
        async def get(self, *_args, **_kwargs):
            raise AssertionError("db.get must not be called")

    with pytest.raises(HTTPException) as exc:
        await get_current_user(request=None, authorization=f"Bearer {token}", db=_Db())

    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token payload"


@pytest.mark.asyncio
async def test_cookie_auth_requires_csrf_for_unsafe_methods() -> None:
    user_id = uuid.uuid4()
    token = _create_access_token(str(user_id))

    class _Db:
        async def get(self, *_args, **_kwargs):
            return SimpleNamespace(id=user_id, is_active=True)

    request = SimpleNamespace(method="POST")
    with pytest.raises(HTTPException) as exc:
        await get_current_user(
            request=request,
            authorization=None,
            adaptiq_access=token,
            adaptiq_csrf="csrf-token",
            x_csrf_token=None,
            db=_Db(),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_cookie_auth_accepts_matching_csrf_token() -> None:
    user_id = uuid.uuid4()
    token = _create_access_token(str(user_id))

    class _Db:
        async def get(self, *_args, **_kwargs):
            return SimpleNamespace(id=user_id, is_active=True)

    current, _issued_at = await get_current_user(
        request=SimpleNamespace(method="POST"),
        authorization=None,
        adaptiq_access=token,
        adaptiq_csrf="csrf-token",
        x_csrf_token="csrf-token",
        db=_Db(),
    )

    assert current.id == user_id


@pytest.mark.asyncio
async def test_bearer_auth_does_not_require_csrf_for_unsafe_methods() -> None:
    user_id = uuid.uuid4()
    token = _create_access_token(str(user_id))

    class _Db:
        async def get(self, *_args, **_kwargs):
            return SimpleNamespace(id=user_id, is_active=True)

    current, _issued_at = await get_current_user(
        request=SimpleNamespace(method="POST"),
        authorization=f"Bearer {token}",
        db=_Db(),
    )

    assert current.id == user_id
