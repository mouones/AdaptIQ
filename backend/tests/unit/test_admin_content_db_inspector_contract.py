"""Regression tests for test admin content db inspector contract behavior."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routers.admin as admin_module


def _make_admin_current():
    return (SimpleNamespace(id=uuid.uuid4(), is_admin=True), None)


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeInspectorConnection:
    def __init__(self, schema_rows):
        self._schema_rows = schema_rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def run_sync(self, _fn, *args, **kwargs):
        if args:
            target = args[0]
            for t in self._schema_rows:
                if t.get("name") == target:
                    return t
            return None
        return self._schema_rows


class _FakeBind:
    def __init__(self, schema_rows):
        self._schema_rows = schema_rows

    def connect(self):
        return _FakeInspectorConnection(self._schema_rows)


class _FakeAdminDb:
    def __init__(
        self,
        *,
        concepts=None,
        questions=None,
        scalar_values=None,
        schema_rows=None,
        table_rows=None,
        total_rows=0,
    ):
        self._concepts = {str(c.id): c for c in (concepts or [])}
        self._questions = {str(q.id): q for q in (questions or [])}
        self._scalar_values = list(scalar_values or [])
        self._table_rows = list(table_rows or [])
        self._total_rows = int(total_rows)
        self.bind = _FakeBind(schema_rows or [])

        self.added = []
        self.deleted = []
        self.executed = []
        self.commit_calls = 0
        self.refresh_calls = 0
        self.flush_calls = 0
        self.rollback_calls = 0

    async def scalar(self, *_args, **_kwargs):
        if self._scalar_values:
            return self._scalar_values.pop(0)
        return None

    async def get(self, model, obj_id):
        model_name = getattr(model, "__name__", "")
        key = str(obj_id)
        if model_name == "Concept":
            return self._concepts.get(key)
        if model_name == "QuestionBank":
            return self._questions.get(key)
        return None

    def add(self, obj):
        self.added.append(obj)
        name = obj.__class__.__name__
        if name == "Concept":
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self._concepts[str(obj.id)] = obj
        if name == "QuestionBank":
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self._questions[str(obj.id)] = obj

    async def delete(self, obj):
        self.deleted.append(obj)
        name = obj.__class__.__name__
        if name == "Concept":
            self._concepts.pop(str(obj.id), None)
        if name == "QuestionBank":
            self._questions.pop(str(obj.id), None)

    async def execute(self, query, params=None):
        qtext = str(query).lower()
        self.executed.append((qtext, params))
        if "count(*)" in qtext:
            return _ScalarResult(self._total_rows)
        return _RowsResult(self._table_rows)

    async def commit(self):
        self.commit_calls += 1

    async def refresh(self, row):
        self.refresh_calls += 1
        if not getattr(row, "id", None):
            row.id = uuid.uuid4()
        if hasattr(row, "created_at") and getattr(row, "created_at", None) is None:
            row.created_at = datetime.now(timezone.utc).replace(tzinfo=None)

    async def flush(self):
        self.flush_calls += 1
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def rollback(self):
        self.rollback_calls += 1


def _make_concept(**overrides):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    base = {
        "id": uuid.uuid4(),
        "name": "Roman Empire",
        "topic": "History",
        "scope": "general",
        "description": "Ancient era",
        "created_at": now,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_question(**overrides):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    base = {
        "id": uuid.uuid4(),
        "question_text": "When did WWII end?",
        "correct_answer": "1945",
        "options_json": '["1944","1945","1946","1950"]',
        "explanation": "World War II ended in 1945.",
        "topic": "History",
        "difficulty_irt": 2.0,
        "source": "admin",
        "usage_count": 0,
        "times_seen": 0,
        "last_served_at": None,
        "created_at": now,
        "primary_concept_id": None,
        "gov_approved": True,
        "gov_safe": True,
        "gov_flags_json": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_admin_create_concept_returns_payload():
    db = _FakeAdminDb(scalar_values=[None])
    body = admin_module.AdminConceptCreateIn(name="Cold War", topic="History", description="USSR vs USA")

    payload = await admin_module.admin_create_concept(body=body, current=_make_admin_current(), db=db)

    assert payload["name"] == "Cold War"
    assert payload["topic"] == "History"
    assert payload["scope"] == "general"
    assert payload["description"] == "USSR vs USA"
    assert uuid.UUID(payload["concept_id"])
    assert db.commit_calls == 1
    assert db.refresh_calls == 1


@pytest.mark.asyncio
async def test_admin_update_concept_changes_description():
    concept = _make_concept(description="Old description")
    db = _FakeAdminDb(concepts=[concept], scalar_values=[])
    body = admin_module.AdminConceptUpdateIn(description="Updated description")

    payload = await admin_module.admin_update_concept(
        concept_id=str(concept.id),
        body=body,
        current=_make_admin_current(),
        db=db,
    )

    assert payload["changed"] is True
    assert concept.description == "Updated description"
    assert db.commit_calls == 1
    assert db.refresh_calls == 1


@pytest.mark.asyncio
async def test_admin_delete_concept_requires_force_when_blocked():
    concept = _make_concept()
    db = _FakeAdminDb(concepts=[concept], scalar_values=[1, 0, 0])

    with pytest.raises(HTTPException) as exc:
        await admin_module.admin_delete_concept(
            concept_id=str(concept.id),
            force=False,
            current=_make_admin_current(),
            db=db,
        )

    assert exc.value.status_code == 409
    assert "force=true" in str(exc.value.detail)
    assert db.commit_calls == 0


@pytest.mark.asyncio
async def test_admin_delete_concept_force_cascades_and_succeeds():
    concept = _make_concept()
    db = _FakeAdminDb(concepts=[concept], scalar_values=[2, 1, 1])

    payload = await admin_module.admin_delete_concept(
        concept_id=str(concept.id),
        force=True,
        current=_make_admin_current(),
        db=db,
    )

    assert payload["success"] is True
    assert payload["force"] is True
    assert len(db.executed) == 4
    assert db.commit_calls == 1


@pytest.mark.asyncio
async def test_admin_create_question_returns_admin_payload():
    db = _FakeAdminDb()
    body = admin_module.AdminQuestionCreateIn(
        question_text="Who discovered penicillin?",
        correct_answer="Alexander Fleming",
        options=["Marie Curie", "Alexander Fleming", "Louis Pasteur", "Isaac Newton"],
        explanation="Penicillin was discovered by Alexander Fleming in 1928.",
        topic="History",
        difficulty_irt=1.5,
        source="admin",
        primary_concept_id=None,
    )

    payload = await admin_module.admin_create_question(body=body, current=_make_admin_current(), db=db)

    assert payload["question_text"] == "Who discovered penicillin?"
    assert payload["correct_answer"] == "Alexander Fleming"
    assert payload["topic"] == "History"
    assert payload["options"] == ["Marie Curie", "Alexander Fleming", "Louis Pasteur", "Isaac Newton"]
    assert uuid.UUID(payload["id"])
    assert db.flush_calls == 1
    assert db.commit_calls == 1
    assert db.refresh_calls == 1


@pytest.mark.asyncio
async def test_admin_update_question_changes_explanation():
    row = _make_question(explanation="Old")
    db = _FakeAdminDb(questions=[row])
    body = admin_module.AdminQuestionUpdateIn(explanation="New explanation")

    payload = await admin_module.admin_update_question(
        question_id=str(row.id),
        body=body,
        current=_make_admin_current(),
        db=db,
    )

    assert payload["changed"] is True
    assert row.explanation == "New explanation"
    assert db.commit_calls == 1
    assert db.refresh_calls == 1


@pytest.mark.asyncio
async def test_admin_delete_question_success():
    row = _make_question()
    db = _FakeAdminDb(questions=[row])

    payload = await admin_module.admin_delete_question(
        question_id=str(row.id),
        current=_make_admin_current(),
        db=db,
    )

    assert payload["success"] is True
    assert payload["question_id"] == str(row.id)
    assert db.commit_calls == 1
    assert len(db.deleted) == 1


@pytest.mark.asyncio
async def test_admin_db_schema_returns_tables_and_count():
    schema = [
        {
            "name": "users",
            "row_count": 3,
            "columns": [{"name": "id", "type": "UUID", "nullable": False, "primary_key": True}],
        }
    ]
    db = _FakeAdminDb(schema_rows=schema)

    payload = await admin_module.admin_db_schema(current=_make_admin_current(), db=db)

    assert payload["total_tables"] == 1
    assert payload["tables"][0]["name"] == "users"


@pytest.mark.asyncio
async def test_admin_db_table_rows_returns_paginated_payload():
    schema = [
        {
            "name": "users",
            "row_count": 2,
            "columns": [{"name": "id", "type": "UUID", "nullable": False, "primary_key": True}],
        }
    ]
    rows = [{"id": uuid.uuid4(), "email": "test@example.com"}]
    db = _FakeAdminDb(schema_rows=schema, table_rows=rows, total_rows=2)

    payload = await admin_module.admin_db_table_rows(
        table_name="users",
        limit=5,
        offset=0,
        current=_make_admin_current(),
        db=db,
    )

    assert payload["table"] == "users"
    assert payload["total"] == 2
    assert payload["limit"] == 5
    assert payload["offset"] == 0
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["email"] == "te***@example.com"


@pytest.mark.asyncio
async def test_admin_db_table_rows_rejects_unknown_table():
    db = _FakeAdminDb(schema_rows=[])

    with pytest.raises(HTTPException) as exc:
        await admin_module.admin_db_table_rows(
            table_name="missing_table",
            limit=5,
            offset=0,
            current=_make_admin_current(),
            db=db,
        )

    assert exc.value.status_code == 404
    assert "Table not found" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_admin_toggle_custom_topic_active_succeeds():
    db = _FakeAdminDb(schema_rows=[])
    topic = admin_module.Topic(
        type="History",
        slug="history-ww2",
        name="World War II",
        is_active=True,
    )
    db.added.append(topic)
    
    body = admin_module.AdminCustomTopicToggleActiveIn(slug="history-ww2", is_active=False)
    
    async def scalar_mock(query, *args, **kwargs):
        return topic
    db.scalar = scalar_mock

    payload = await admin_module.admin_toggle_custom_topic_active(
        body=body,
        current=_make_admin_current(),
        db=db,
    )

    assert payload["slug"] == "history-ww2"
    assert payload["is_active"] is False
    assert topic.is_active is False
    assert db.commit_calls == 1
