"""Regression tests for verified profile email-change behavior."""

from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routers import auth as auth_router


class _EmailChangeDb:
    def __init__(self, duplicate=None):
        self.duplicate = duplicate
        self.commit_calls = 0
        self.refresh_calls = 0

    async def scalar(self, *_args, **_kwargs):
        return self.duplicate

    async def commit(self):
        self.commit_calls += 1

    async def refresh(self, _user):
        self.refresh_calls += 1


def _make_user(**overrides):
    base = {
        "id": uuid.uuid4(),
        "email": "learner@adaptiq.dev",
        "username": "learner",
        "password_hash": "hash",
        "points": 0,
        "level": "Novice",
        "is_active": True,
        "is_admin": False,
        "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


_request_impl = inspect.unwrap(auth_router.request_profile_email_change)
_confirm_impl = inspect.unwrap(auth_router.confirm_profile_email_change)
_signup_impl = inspect.unwrap(auth_router.signup)


@pytest.mark.asyncio
async def test_email_change_request_stores_user_bound_code_and_sends(monkeypatch):
    auth_router._otp_store.clear()
    user = _make_user()
    sent: list[tuple[str, str, str]] = []

    async def _fake_send(recipient: str, otp_code: str, purpose: str = "password reset"):
        sent.append((recipient, otp_code, purpose))
        return True

    monkeypatch.setattr(auth_router, "send_otp_email", _fake_send)

    result = await _request_impl(
        request=SimpleNamespace(),
        payload=auth_router.ProfileEmailChangeRequest(new_email="New.User@adaptiq.dev"),
        current=(user, None),
        db=_EmailChangeDb(),
        redis_client=None,
    )

    key_id = auth_router._email_change_key(user.id, "new.user@adaptiq.dev")
    stored = await auth_router._read_otp_for_purpose(None, key_id, purpose="email_change")

    assert result.message == "Verification code sent to the new email address"
    assert stored is not None
    assert stored["user_id"] == str(user.id)
    assert stored["new_email"] == "new.user@adaptiq.dev"
    assert sent == [("new.user@adaptiq.dev", stored["code"], "email change")]


@pytest.mark.asyncio
async def test_email_change_confirm_updates_email_and_clears_code():
    auth_router._otp_store.clear()
    user = _make_user()
    key_id = auth_router._email_change_key(user.id, "new.user@adaptiq.dev")
    await auth_router._save_otp_for_purpose(
        None,
        key_id,
        "123456",
        purpose="email_change",
        extra={"user_id": str(user.id), "new_email": "new.user@adaptiq.dev"},
    )
    db = _EmailChangeDb()

    result = await _confirm_impl(
        request=SimpleNamespace(),
        payload=auth_router.ProfileEmailChangeConfirmRequest(
            new_email="new.user@adaptiq.dev",
            code="123456",
        ),
        current=(user, None),
        db=db,
        redis_client=None,
    )

    assert result.email == "new.user@adaptiq.dev"
    assert user.email == "new.user@adaptiq.dev"
    assert db.commit_calls == 1
    assert db.refresh_calls == 1
    assert await auth_router._read_otp_for_purpose(None, key_id, purpose="email_change") is None


@pytest.mark.asyncio
async def test_email_change_request_rejects_blocked_domain(monkeypatch):
    user = _make_user()

    async def _fail_send(*_args, **_kwargs):
        raise AssertionError("blocked domain should not send")

    monkeypatch.setattr(auth_router, "send_otp_email", _fail_send)

    with pytest.raises(HTTPException) as exc:
        await _request_impl(
            request=SimpleNamespace(),
            payload=auth_router.ProfileEmailChangeRequest(new_email="learner@example.com"),
            current=(user, None),
            db=_EmailChangeDb(),
            redis_client=None,
        )

    assert exc.value.status_code == 400
    assert "cannot receive verification codes" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_email_change_request_rejects_duplicate_email():
    user = _make_user()

    with pytest.raises(HTTPException) as exc:
        await _request_impl(
            request=SimpleNamespace(),
            payload=auth_router.ProfileEmailChangeRequest(new_email="taken@adaptiq.dev"),
            current=(user, None),
            db=_EmailChangeDb(duplicate=object()),
            redis_client=None,
        )

    assert exc.value.status_code == 400
    assert "Email already registered" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_email_change_confirm_rejects_wrong_code_and_tracks_attempt():
    auth_router._otp_store.clear()
    user = _make_user()
    key_id = auth_router._email_change_key(user.id, "new.user@adaptiq.dev")
    await auth_router._save_otp_for_purpose(
        None,
        key_id,
        "123456",
        purpose="email_change",
        extra={"user_id": str(user.id), "new_email": "new.user@adaptiq.dev"},
    )

    with pytest.raises(HTTPException) as exc:
        await _confirm_impl(
            request=SimpleNamespace(),
            payload=auth_router.ProfileEmailChangeConfirmRequest(
                new_email="new.user@adaptiq.dev",
                code="000000",
            ),
            current=(user, None),
            db=_EmailChangeDb(),
            redis_client=None,
        )

    stored = await auth_router._read_otp_for_purpose(None, key_id, purpose="email_change")
    assert exc.value.status_code == 400
    assert "Invalid verification code" in str(exc.value.detail)
    assert stored is not None
    assert stored["attempts"] == 1
    assert user.email == "learner@adaptiq.dev"


@pytest.mark.asyncio
async def test_signup_remains_immediate_and_does_not_send_verification(monkeypatch):
    class _SignupDb:
        def __init__(self):
            self.scalar_results = [None, None]
            self.commit_calls = 0
            self.refreshed = False

        async def scalar(self, *_args, **_kwargs):
            return self.scalar_results.pop(0)

        def add(self, _user):
            pass

        async def commit(self):
            self.commit_calls += 1

        async def refresh(self, _user):
            self.refreshed = True

    async def _fail_send(*_args, **_kwargs):
        raise AssertionError("signup should not send an email verification code")

    monkeypatch.setattr(auth_router, "send_otp_email", _fail_send)
    monkeypatch.setattr(auth_router, "_hash_password", lambda raw: f"hashed::{raw}")
    monkeypatch.setattr(auth_router, "_create_access_token", lambda user_id: f"token::{user_id}")

    db = _SignupDb()
    result = await _signup_impl(
        response=SimpleNamespace(set_cookie=lambda *args, **kwargs: None),
        request=SimpleNamespace(),
        payload=auth_router.SignupRequest(
            email="signup@adaptiq.dev",
            username="signup_user",
            password="StrongPass123!",
        ),
        db=db,
    )

    assert result.access_token.startswith("token::")
    assert result.user.email == "signup@adaptiq.dev"
    assert db.commit_calls == 1
    assert db.refreshed is True
