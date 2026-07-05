"""Regression tests for test authz guards behavior."""

import uuid

import pytest
from fastapi import HTTPException

from routers.challenge import _ensure_user_match as challenge_ensure_user_match
from routers.custom import _ensure_user_match as custom_ensure_user_match
from routers.onboarding import _ensure_user_match as onboarding_ensure_user_match


def test_challenge_guard_allows_same_uuid() -> None:
    user_id = str(uuid.uuid4())
    challenge_ensure_user_match(user_id, user_id)


def test_challenge_guard_rejects_different_uuid() -> None:
    with pytest.raises(HTTPException) as exc:
        challenge_ensure_user_match(str(uuid.uuid4()), str(uuid.uuid4()))
    assert exc.value.status_code == 403


def test_challenge_guard_rejects_invalid_target_uuid() -> None:
    with pytest.raises(HTTPException) as exc:
        challenge_ensure_user_match("not-a-uuid", str(uuid.uuid4()))
    assert exc.value.status_code == 422


def test_custom_guard_allows_same_uuid() -> None:
    user_id = str(uuid.uuid4())
    custom_ensure_user_match(user_id, user_id)


def test_custom_guard_rejects_different_uuid() -> None:
    with pytest.raises(HTTPException) as exc:
        custom_ensure_user_match(str(uuid.uuid4()), str(uuid.uuid4()))
    assert exc.value.status_code == 403


def test_custom_guard_rejects_invalid_target_uuid() -> None:
    with pytest.raises(HTTPException) as exc:
        custom_ensure_user_match("invalid", str(uuid.uuid4()))
    assert exc.value.status_code == 422


def test_onboarding_guard_allows_same_user_id() -> None:
    user_id = str(uuid.uuid4())
    onboarding_ensure_user_match(user_id, user_id)


def test_onboarding_guard_rejects_different_user_id() -> None:
    with pytest.raises(HTTPException) as exc:
        onboarding_ensure_user_match(str(uuid.uuid4()), str(uuid.uuid4()))
    assert exc.value.status_code == 403
