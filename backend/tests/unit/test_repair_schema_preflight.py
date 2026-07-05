"""Regression tests for repair script schema-drift detection."""

import pytest

from scripts import repair_data_integrity as repair


def test_expected_alembic_heads_follow_local_migration_graph():
    assert repair.expected_alembic_heads() == ["20260704_02"]


@pytest.mark.asyncio
async def test_schema_preflight_reports_revision_and_column_drift(monkeypatch):
    async def fake_current_revisions(_db):
        return ["20260604_01"]

    async def fake_missing_columns(_db):
        return {"concepts": ["scope"]}

    monkeypatch.setattr(repair, "expected_alembic_heads", lambda: ["20260704_02"])
    monkeypatch.setattr(repair, "current_alembic_revisions", fake_current_revisions)
    monkeypatch.setattr(repair, "missing_required_columns", fake_missing_columns)

    result = await repair.schema_preflight(object())

    assert result is not None
    assert result["status"] == "schema_out_of_date"
    assert result["current_revisions"] == ["20260604_01"]
    assert result["expected_heads"] == ["20260704_02"]
    assert result["missing_required_columns"] == {"concepts": ["scope"]}
    assert "alembic upgrade head" in result["message"]


@pytest.mark.asyncio
async def test_schema_preflight_accepts_current_schema(monkeypatch):
    async def fake_current_revisions(_db):
        return ["20260704_02"]

    async def fake_missing_columns(_db):
        return {}

    monkeypatch.setattr(repair, "expected_alembic_heads", lambda: ["20260704_02"])
    monkeypatch.setattr(repair, "current_alembic_revisions", fake_current_revisions)
    monkeypatch.setattr(repair, "missing_required_columns", fake_missing_columns)

    assert await repair.schema_preflight(object()) is None
