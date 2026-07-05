"""Regression tests for test admin overview contract behavior."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routers.admin as admin_module


class _ScalarNoneDb:
    async def scalar(self, *_args, **_kwargs):
        return None

    async def execute(self, *_args, **_kwargs):
        class _Rows:
            def all(self):
                return [
                    ("seed", 2),
                    ("llm", 3),
                    ("custom_llm_simple", 5),
                ]

        return _Rows()


@pytest.mark.asyncio
async def test_admin_overview_payload_contract_has_expected_keys() -> None:
    payload = await admin_module.admin_overview(current=None, db=_ScalarNoneDb())

    assert set(payload.keys()) == {
        "users",
        "questions",
        "sessions",
        "concepts",
        "responses",
        "pvp",
    }

    assert payload["users"].keys() >= {"total", "active", "admin", "banned", "latest_created_at"}
    assert payload["questions"].keys() >= {
        "total",
        "llm_generated",
        "generated",
        "seeded",
        "admin",
        "unknown",
        "by_category",
        "by_source",
        "cached",
        "latest_created_at",
    }
    assert payload["sessions"].keys() >= {"classic", "challenge", "custom", "pvp"}
    assert payload["concepts"].keys() >= {"total", "mastery_rows"}
    assert payload["responses"].keys() >= {"total"}
    assert payload["pvp"].keys() >= {"total_matches", "rated_players"}

    assert isinstance(payload["users"]["total"], int)
    assert payload["users"]["latest_created_at"] is None
    assert payload["questions"]["llm_generated"] == 8
    assert payload["questions"]["generated"] == 8
    assert payload["questions"]["seeded"] == 2
    assert payload["questions"]["by_source"]["custom_llm_simple"] == 5


def test_require_admin_rejects_non_admin() -> None:
    user = SimpleNamespace(is_admin=False)

    with pytest.raises(HTTPException) as exc:
        admin_module._require_admin((user, None))

    assert exc.value.status_code == 403


def test_require_admin_allows_admin() -> None:
    user = SimpleNamespace(is_admin=True)

    assert admin_module._require_admin((user, None)) is user
