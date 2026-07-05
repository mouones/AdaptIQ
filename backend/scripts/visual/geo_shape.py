"""add_shape_columns.py - run once from adaptiq-backend/.

This script creates the `iso2` and `shape_svg` columns used by Visual Room
geography questions. It reads backend/.env when available so the DB config
matches the rest of the project.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _load_env() -> None:
    backend_env = BACKEND_ROOT / ".env"
    if backend_env.exists():
        load_dotenv(backend_env)
    else:
        load_dotenv()


def _db_kwargs() -> dict[str, str | int]:
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://adaptiq:adaptiq@localhost:5433/adaptiq_db",
    )
    parsed = urlparse(database_url.replace("postgresql+asyncpg://", "postgresql://", 1))
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5433,
        "dbname": (parsed.path or "/adaptiq_db").lstrip("/"),
        "user": parsed.username or "adaptiq",
        "password": parsed.password or "adaptiq",
    }


def ensure_shape_columns() -> None:
    conn = psycopg2.connect(**_db_kwargs())
    cur = conn.cursor()

    cur.execute(
        """
        ALTER TABLE visual_questions
            ADD COLUMN IF NOT EXISTS iso2      VARCHAR(2)  DEFAULT NULL,
            ADD COLUMN IF NOT EXISTS shape_svg TEXT        DEFAULT NULL;
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_visual_iso2
            ON visual_questions (iso2);
        """
    )

    conn.commit()

    cur.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'visual_questions'
          AND column_name IN ('iso2', 'shape_svg')
        ORDER BY column_name;
        """
    )
    rows = cur.fetchall()
    for name, data_type in rows:
        print(f"  {name}: {data_type}")

    conn.close()


def main() -> None:
    _load_env()
    ensure_shape_columns()
    print("Done.")


if __name__ == "__main__":
    main()


