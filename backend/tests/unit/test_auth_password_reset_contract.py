"""Regression tests for test auth password reset contract behavior."""

import inspect
from types import SimpleNamespace

import pytest

from routers import auth as auth_router


class _ResetDb:
    def __init__(self, user):
        self._user = user
        self.commit_calls = 0

    async def scalar(self, *_args, **_kwargs):
        return self._user

    async def commit(self):
        self.commit_calls += 1


_reset_impl = inspect.unwrap(auth_router.reset_password)


@pytest.mark.asyncio
async def test_reset_password_success_updates_hash_and_clears_otp(monkeypatch):
    user = SimpleNamespace(email="learner@adaptiq.dev", password_hash="old-hash")
    db = _ResetDb(user=user)
    deleted_emails: list[str] = []

    async def _fake_read_otp(_redis_client, email: str):
        assert email == "learner@adaptiq.dev"
        return {"code": "123456", "attempts": 0}

    async def _fake_delete_otp(_redis_client, email: str):
        deleted_emails.append(email)

    monkeypatch.setattr(auth_router, "_read_otp", _fake_read_otp)
    monkeypatch.setattr(auth_router, "_delete_otp", _fake_delete_otp)
    monkeypatch.setattr(auth_router, "_hash_password", lambda raw: f"hashed::{raw}")

    payload = auth_router.ResetPasswordRequest(
        email="  Learner@adaptiq.dev ",
        code="123456",
        new_password="NewPass123!",
    )

    result = await _reset_impl(
        request=SimpleNamespace(),
        payload=payload,
        db=db,
        redis_client=object(),
    )

    assert result.message == "Password reset successful"
    assert user.password_hash == "hashed::NewPass123!"
    assert db.commit_calls == 1
    assert deleted_emails == ["learner@adaptiq.dev"]
