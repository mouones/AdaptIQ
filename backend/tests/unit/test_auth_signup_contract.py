"""Regression tests for test auth signup contract behavior."""

from types import SimpleNamespace
import inspect

import pytest
from fastapi import HTTPException, Response

from routers import auth as auth_router


class _ScalarSequenceDb:
    def __init__(self, scalar_results):
        self._scalar_results = list(scalar_results)
        self.commit_calls = 0

    async def scalar(self, *_args, **_kwargs):
        if not self._scalar_results:
            raise AssertionError("Unexpected scalar() call")
        return self._scalar_results.pop(0)

    async def commit(self):
        self.commit_calls += 1


_signup_impl = inspect.unwrap(auth_router.signup)


@pytest.mark.asyncio
async def test_signup_duplicate_email_returns_409():
    db = _ScalarSequenceDb(scalar_results=[object()])
    payload = auth_router.SignupRequest(
        email="duplicate@adaptiq.dev",
        username="new_user",
        password="StrongPass123!",
    )

    with pytest.raises(HTTPException) as exc:
        await _signup_impl(response=Response(), request=SimpleNamespace(), payload=payload, db=db)

    assert exc.value.status_code == 409
    assert "Email already registered" in str(exc.value.detail)
    assert db.commit_calls == 0


@pytest.mark.asyncio
async def test_signup_duplicate_username_returns_409():
    db = _ScalarSequenceDb(scalar_results=[None, object()])
    payload = auth_router.SignupRequest(
        email="new_mail@adaptiq.dev",
        username="duplicate_user",
        password="StrongPass123!",
    )

    with pytest.raises(HTTPException) as exc:
        await _signup_impl(response=Response(), request=SimpleNamespace(), payload=payload, db=db)

    assert exc.value.status_code == 409
    assert "Username already taken" in str(exc.value.detail)
    assert db.commit_calls == 0
