"""Read-only local smoke checks for database and external RAG retrieval.

This helper intentionally avoids creating users, sessions, seed data, or test
rows. It is meant for quick validation against the currently configured local
services.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from config import DATABASE_URL  # noqa: E402
from rag.wikidata import fetch_wikidata_facts, format_wikidata_as_context  # noqa: E402
from rag.wikipedia import fetch_wikipedia_context  # noqa: E402


VISUAL_TABLES = ("visual_questions", "visual_sessions")


def mask_url(url: str) -> str:
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    return f"{scheme}://***@{rest.split('@', 1)[1]}"


def context_mentions_question(context: str, question: str) -> bool:
    tokens = {
        token.strip(".,;:!?()[]{}'\"").lower()
        for token in question.split()
        if len(token.strip(".,;:!?()[]{}'\"")) >= 5
    }
    if not tokens:
        return bool(context.strip())
    lowered = context.lower()
    return any(token in lowered for token in tokens)


async def db_preflight(database_url: str = DATABASE_URL) -> dict[str, Any]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            revision = await conn.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
            rows = await conn.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name IN (:questions_table, :sessions_table)
                    """
                ),
                {
                    "questions_table": "visual_questions",
                    "sessions_table": "visual_sessions",
                },
            )
            found_tables = {row[0] for row in rows}
            missing_tables = sorted(set(VISUAL_TABLES) - found_tables)
            return {
                "database_url": mask_url(database_url),
                "alembic_revision": revision,
                "missing_visual_tables": missing_tables,
                "ok": not missing_tables and bool(revision),
            }
    finally:
        await engine.dispose()


async def rag_smoke(question: str, topic: str, difficulty: int) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        wiki = await fetch_wikipedia_context(
            topic=topic,
            difficulty=difficulty,
            client=client,
            n_sentences=4,
        )
        facts = await fetch_wikidata_facts(
            topic=topic,
            difficulty=difficulty,
            client=client,
        )

    wiki_context = wiki.get("context", "") if isinstance(wiki, dict) else ""
    facts_context = format_wikidata_as_context(facts, n=5) if facts else ""
    combined_context = "\n\n".join(part for part in (wiki_context, facts_context) if part)

    return {
        "question": question,
        "topic": topic,
        "difficulty": difficulty,
        "wikipedia_sources": wiki.get("sources", []) if isinstance(wiki, dict) else [],
        "wikidata_facts": len(facts or []),
        "has_context": bool(combined_context.strip()),
        "context_mentions_question_terms": context_mentions_question(combined_context, question),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-db", action="store_true")
    parser.add_argument("--skip-rag", action="store_true")
    parser.add_argument(
        "--question",
        default="What made the Roman Empire influential?",
        help="Question used only for read-only relevance checks.",
    )
    parser.add_argument("--topic", default="History")
    parser.add_argument("--difficulty", type=int, default=3)
    args = parser.parse_args()

    result: dict[str, Any] = {}
    ok = True

    if not args.skip_db:
        result["database"] = await db_preflight()
        ok = ok and result["database"]["ok"]

    if not args.skip_rag:
        result["rag"] = await rag_smoke(args.question, args.topic, args.difficulty)
        ok = ok and result["rag"]["has_context"]

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
