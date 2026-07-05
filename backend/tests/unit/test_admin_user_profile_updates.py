"""Regression tests for test admin user profile updates behavior."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import routers.admin as admin_module


class _FakeAdminDb:
    def __init__(self, users):
        self._users = {str(user.id): user for user in users}
        self.commit_calls = 0
        self.refresh_calls = 0
        self.rollback_calls = 0

    async def get(self, _model, obj_id):
        return self._users.get(str(obj_id))

    async def scalar(self, *_args, **_kwargs):
        return None

    async def commit(self):
        self.commit_calls += 1

    async def refresh(self, _row):
        self.refresh_calls += 1

    async def rollback(self):
        self.rollback_calls += 1


def _make_user(**overrides):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    base = {
        "id": uuid.uuid4(),
        "email": "learner@adaptiq.dev",
        "username": "learner_1",
        "level": "Novice",
        "points": 7,
        "is_active": True,
        "is_admin": False,
        "ban_until": None,
        "ban_reason": None,
        "created_at": now,
        "last_login": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_admin_update_user_allows_profile_edit(monkeypatch):
    admin_user = _make_user(is_admin=True)
    target_user = _make_user()
    db = _FakeAdminDb([target_user])

    async def _no_duplicate_username(_db, _username, _exclude_user_id):
        return None

    async def _no_duplicate_email(_db, _email, _exclude_user_id):
        return None

    monkeypatch.setattr(admin_module, "_find_other_user_by_username", _no_duplicate_username)
    monkeypatch.setattr(admin_module, "_find_other_user_by_email", _no_duplicate_email)

    payload = admin_module.AdminUserUpdateIn(
        username="updated_user",
        email="Updated.User@adaptiq.dev",
        level="Scholar",
        points=42,
    )

    result = await admin_module.admin_update_user(
        user_id=str(target_user.id),
        is_active=None,
        is_admin=None,
        ban_minutes=None,
        ban_reason=None,
        clear_ban=False,
        body=payload,
        current=(admin_user, None),
        db=db,
    )

    assert result["success"] is True
    assert result["changed"] is True
    assert target_user.username == "updated_user"
    assert target_user.email == "updated.user@adaptiq.dev"
    assert target_user.level == "Scholar"
    assert target_user.points == 42
    assert db.commit_calls == 1
    assert db.refresh_calls == 1


@pytest.mark.asyncio
async def test_admin_update_user_rejects_duplicate_username(monkeypatch):
    admin_user = _make_user(is_admin=True)
    target_user = _make_user()
    db = _FakeAdminDb([target_user])

    async def _duplicate_username(_db, _username, _exclude_user_id):
        return _make_user(username="taken_name")

    monkeypatch.setattr(admin_module, "_find_other_user_by_username", _duplicate_username)

    payload = admin_module.AdminUserUpdateIn(username="taken_name")

    with pytest.raises(HTTPException) as exc:
        await admin_module.admin_update_user(
            user_id=str(target_user.id),
            is_active=None,
            is_admin=None,
            ban_minutes=None,
            ban_reason=None,
            clear_ban=False,
            body=payload,
            current=(admin_user, None),
            db=db,
        )

    assert exc.value.status_code == 400
    assert "Username already taken" in str(exc.value.detail)
    assert db.commit_calls == 0


@pytest.mark.asyncio
async def test_admin_update_user_rejects_duplicate_email(monkeypatch):
    admin_user = _make_user(is_admin=True)
    target_user = _make_user()
    db = _FakeAdminDb([target_user])

    async def _no_duplicate_username(_db, _username, _exclude_user_id):
        return None

    async def _duplicate_email(_db, _email, _exclude_user_id):
        return _make_user(email="dup@adaptiq.dev")

    monkeypatch.setattr(admin_module, "_find_other_user_by_username", _no_duplicate_username)
    monkeypatch.setattr(admin_module, "_find_other_user_by_email", _duplicate_email)

    payload = admin_module.AdminUserUpdateIn(email="dup@adaptiq.dev")

    with pytest.raises(HTTPException) as exc:
        await admin_module.admin_update_user(
            user_id=str(target_user.id),
            is_active=None,
            is_admin=None,
            ban_minutes=None,
            ban_reason=None,
            clear_ban=False,
            body=payload,
            current=(admin_user, None),
            db=db,
        )

    assert exc.value.status_code == 400
    assert "Email already registered" in str(exc.value.detail)
    assert db.commit_calls == 0


@pytest.mark.asyncio
async def test_admin_update_user_rejects_invalid_email_payload():
    admin_user = _make_user(is_admin=True)
    target_user = _make_user()
    db = _FakeAdminDb([target_user])

    payload = admin_module.AdminUserUpdateIn(email="not-an-email")

    with pytest.raises(HTTPException) as exc:
        await admin_module.admin_update_user(
            user_id=str(target_user.id),
            is_active=None,
            is_admin=None,
            ban_minutes=None,
            ban_reason=None,
            clear_ban=False,
            body=payload,
            current=(admin_user, None),
            db=db,
        )

    assert exc.value.status_code == 400
    assert "Invalid email" in str(exc.value.detail)
    assert db.commit_calls == 0


def test_admin_user_update_payload_validates_points_non_negative():
    with pytest.raises(ValidationError):
        admin_module.AdminUserUpdateIn(points=-1)


@pytest.mark.asyncio
async def test_admin_update_user_cannot_self_demote_from_admin():
    same_user_id = uuid.uuid4()
    admin_user = _make_user(id=same_user_id, is_admin=True)
    db = _FakeAdminDb([admin_user])

    payload = admin_module.AdminUserUpdateIn(is_admin=False)

    with pytest.raises(HTTPException) as exc:
        await admin_module.admin_update_user(
            user_id=str(same_user_id),
            is_active=None,
            is_admin=None,
            ban_minutes=None,
            ban_reason=None,
            clear_ban=False,
            body=payload,
            current=(admin_user, None),
            db=db,
        )

    assert exc.value.status_code == 400
    assert "cannot remove your own admin access" in str(exc.value.detail).lower()
    assert db.commit_calls == 0


@pytest.mark.asyncio
async def test_admin_update_user_applies_timed_ban():
    admin_user = _make_user(is_admin=True)
    target_user = _make_user()
    db = _FakeAdminDb([target_user])

    result = await admin_module.admin_update_user(
        user_id=str(target_user.id),
        is_active=None,
        is_admin=None,
        ban_minutes=15,
        ban_reason="e2e cleanup test",
        clear_ban=False,
        body=None,
        current=(admin_user, None),
        db=db,
    )

    assert result["success"] is True
    assert result["changed"] is True
    assert target_user.ban_until is not None
    assert target_user.ban_reason == "e2e cleanup test"
    assert result["user"]["is_banned_now"] is True
    assert db.commit_calls == 1
    assert db.refresh_calls == 1


@pytest.mark.asyncio
async def test_admin_update_user_clears_timed_ban():
    future = datetime.now(timezone.utc).replace(tzinfo=None)
    admin_user = _make_user(is_admin=True)
    target_user = _make_user(ban_until=future, ban_reason="temporary")
    db = _FakeAdminDb([target_user])

    result = await admin_module.admin_update_user(
        user_id=str(target_user.id),
        is_active=None,
        is_admin=None,
        ban_minutes=None,
        ban_reason=None,
        clear_ban=True,
        body=None,
        current=(admin_user, None),
        db=db,
    )

    assert result["success"] is True
    assert result["changed"] is True
    assert target_user.ban_until is None
    assert target_user.ban_reason is None
    assert result["user"]["is_banned_now"] is False
    assert db.commit_calls == 1
