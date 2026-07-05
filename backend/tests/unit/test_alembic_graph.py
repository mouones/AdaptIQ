"""Regression tests for the active Alembic revision graph."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _script_directory() -> ScriptDirectory:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def test_alembic_graph_has_single_root_single_head_and_expected_chain():
    script = _script_directory()

    assert script.get_bases() == ["20260411_01"]
    assert script.get_heads() == ["20260704_02"]

    chain = list(reversed([revision.revision for revision in script.walk_revisions()]))
    assert chain == [
        "20260411_01",
        "20260411_02",
        "20260411_03",
        "20260412_01_add_pvp",
        "20260413_01_answer_uniqueness",
        "20260415_01_governance",
        "20260516_01",
        "20260516_02",
        "bb15d1154671",
        "20260530_01",
        "20260604_01",
        "20260604_02",
        "20260604_03",
        "20260604_04",
        "20260611_01",
        "20260611_02",
        "20260704_01",
        "20260704_02",
    ]


def test_alembic_revisions_do_not_reference_missing_parents():
    script = _script_directory()
    revisions = {revision.revision for revision in script.walk_revisions()}

    for revision in script.walk_revisions():
        down_revision = revision.down_revision
        if down_revision is None:
            continue
        parents = down_revision if isinstance(down_revision, tuple) else (down_revision,)
        assert set(parents).issubset(revisions)


def test_alembic_env_imports_visual_models_for_autogenerate():
    env_source = (BACKEND_ROOT / "alembic" / "env.py").read_text(encoding="utf-8")

    assert "database.visual_models" in env_source
