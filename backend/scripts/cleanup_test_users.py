"""Remove generated test users and their dependent rows.

Usage:
    python scripts/cleanup_test_users.py --dry-run
    python scripts/cleanup_test_users.py --apply --yes

The script only targets known generated test account patterns and never
prints email addresses or secrets.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from config import DATABASE_URL  # noqa: E402
from database.models import User  # noqa: E402


TEST_EMAIL_PATTERNS = [
    "test%@%",
    "copilot%@%",
    "pw-smoke-%@example.test",
    "pw-smoke-%@example.com",
    "idem_%@example.com",
    "security_%@example.com",
    "sec_%@example.com",
    "challenge_deep_%@example.com",
    "test_custom_%@example.com",
    "onboarding_%@example.com",
    "user1_%@example.com",
    "user2_%@example.com",
    "livepvpfix%@%",
    "challenge%@%",
    "auditpvp%@%",
    "test_skip%@%",
    "geo_scope%@%",
    "geo-scope%@%",
    "pvp%@%",
    "sec_cookies%@%",
    "sec-cookies%@%",
    "e2e%@%",
    "flowtest%@%",
    "e2e_user%@%",
    "%+user1_%@%",
    "%+user2_%@%",
    "%+test%@%",
    "%+copilot%@%",
    "%+livepvpfix%@%",
    "%+challenge%@%",
    "%+auditpvp%@%",
    "%+sec_%@%",
    "%+challenge_deep%@%",
    "%+test_custom%@%",
    "%+onboarding%@%",
    "%+test_skip%@%",
    "%+geo_scope%@%",
    "%+geo-scope%@%",
    "%+pvp%@%",
    "%+sec_cookies%@%",
    "%+sec-cookies%@%",
    "%+e2e%@%",
    "%+flowtest%@%",
    "%+e2e_user%@%",
]

TEST_USERNAME_PATTERNS = [
    "test%",
    "copilot%",
    "pw_smoke_%",
    "pw-smoke-%",
    "idem_%",
    "security_%",
    "sec_%",
    "challenge_deep_%",
    "test_custom_%",
    "onboarding_%",
    "testuser1_%",
    "testuser2_%",
    "user1_%",
    "user2_%",
    "livepvpfix%",
    "challenge%",
    "auditpvp%",
    "test_skip%",
    "geo_scope%",
    "geo-scope%",
    "pvp%",
    "sec_cookies%",
    "sec-cookies%",
    "e2e%",
    "flowtest%",
    "e2e_user%",
]

DEPENDENT_PRE_DELETES = [
    (
        "challenge_answers",
        "DELETE FROM challenge_answers WHERE session_id IN (SELECT id FROM challenge_sessions WHERE user_id = :uid)",
        "SELECT COUNT(*) FROM challenge_answers WHERE session_id IN (SELECT id FROM challenge_sessions WHERE user_id = :uid)",
    ),
    (
        "pvp_match_answers",
        """
        DELETE FROM pvp_match_answers
        WHERE match_id IN (
            SELECT id
            FROM pvp_matches
            WHERE user1_id = :uid OR user2_id = :uid OR winner_id = :uid
        )
        """,
        """
        SELECT COUNT(*)
        FROM pvp_match_answers
        WHERE match_id IN (
            SELECT id
            FROM pvp_matches
            WHERE user1_id = :uid OR user2_id = :uid OR winner_id = :uid
        )
        """,
    ),
]

PRE_DELETE_TABLES = {name for name, _, _ in DEPENDENT_PRE_DELETES}


def _like_pattern_to_regex(pattern: str) -> re.Pattern[str]:
    out = ["^"]
    for char in pattern:
        if char == "%":
            out.append(".*")
        elif char == "_":
            out.append(".")
        else:
            out.append(re.escape(char))
    out.append("$")
    return re.compile("".join(out), re.IGNORECASE)


_EMAIL_MATCHERS = [_like_pattern_to_regex(pattern) for pattern in TEST_EMAIL_PATTERNS]
_USERNAME_MATCHERS = [_like_pattern_to_regex(pattern) for pattern in TEST_USERNAME_PATTERNS]

IDENTITY_BUCKET_PREFIXES = {
    "test_prefix": ("test",),
    "copilot_prefix": ("copilot",),
    "security_prefix": ("security", "sec_", "sec-", "sec."),
    "challenge_prefix": ("challenge", "challenge_deep"),
    "pvp_prefix": ("pvp", "livepvpfix", "auditpvp"),
    "e2e_prefix": ("e2e", "e2e_user", "flowtest", "pw-smoke", "pw_smoke"),
    "geo_scope_prefix": ("geo_scope", "geo-scope"),
    "onboarding_prefix": ("onboarding",),
    "user_fixture_prefix": ("user1_", "user2_", "testuser1_", "testuser2_"),
}


def is_generated_test_identity(email: str | None, username: str | None) -> bool:
    """Return True for known generated local/live-test accounts."""
    email_value = str(email or "").strip()
    username_value = str(username or "").strip()
    return any(pattern.match(email_value) for pattern in _EMAIL_MATCHERS) or any(
        pattern.match(username_value) for pattern in _USERNAME_MATCHERS
    )


def identity_bucket_names(email: str | None, username: str | None) -> list[str]:
    """Return redacted cleanup buckets for matched generated identities."""
    values = [
        str(email or "").strip().lower(),
        str(username or "").strip().lower(),
    ]
    if "@" in values[0]:
        local, _domain = values[0].split("@", 1)
        values.append(local)
        if "+" in local:
            values.append(local.split("+", 1)[1])

    buckets = []
    for bucket, prefixes in IDENTITY_BUCKET_PREFIXES.items():
        if any(value.startswith(prefix) for value in values for prefix in prefixes):
            buckets.append(bucket)
    return buckets or ["other_generated_pattern"]


def _test_user_filter():
    filters = [User.email.ilike(pattern) for pattern in TEST_EMAIL_PATTERNS]
    filters.extend(User.username.ilike(pattern) for pattern in TEST_USERNAME_PATTERNS)
    return or_(*filters)


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


async def _user_reference_columns(db) -> dict[str, list[str]]:
    rows = await db.execute(
        text(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND column_name IN ('user_id', 'user1_id', 'user2_id', 'winner_id')
              AND table_name <> 'users'
            ORDER BY table_name, column_name
            """
        )
    )
    by_table: dict[str, list[str]] = {}
    for table_name, column_name in rows.fetchall():
        by_table.setdefault(str(table_name), []).append(str(column_name))
    return by_table


async def cleanup(*, apply: bool, yes: bool) -> dict:
    if apply and not yes:
        raise SystemExit("--apply requires --yes")

    engine = create_async_engine(DATABASE_URL, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            matched_rows = (
                await db.execute(select(User.id, User.email, User.username).where(_test_user_filter()))
            ).all()
            user_ids = [row[0] for row in matched_rows]
            bucket_counts: dict[str, int] = {}
            for _uid, email, username in matched_rows:
                for bucket in identity_bucket_names(email, username):
                    bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
            result = {
                "mode": "apply" if apply else "dry-run",
                "matched_users": len(user_ids),
                "matched_identity_buckets": dict(sorted(bucket_counts.items())),
                "dependent_rows_deleted": {},
                "users_deleted": 0,
            }

            if not user_ids:
                await db.rollback()
                return result

            reference_columns = await _user_reference_columns(db)

            if apply:
                for uid in user_ids:
                    for table_name, delete_sql, _ in DEPENDENT_PRE_DELETES:
                        deleted = await db.execute(text(delete_sql), {"uid": uid})
                        result["dependent_rows_deleted"][table_name] = (
                            result["dependent_rows_deleted"].get(table_name, 0)
                            + int(deleted.rowcount or 0)
                        )

                    for table_name, columns in reference_columns.items():
                        if table_name in PRE_DELETE_TABLES:
                            continue
                        where_clause = " OR ".join(f"{_quote_ident(column)} = :uid" for column in columns)
                        statement = text(f"DELETE FROM {_quote_ident(table_name)} WHERE {where_clause}")
                        deleted = await db.execute(statement, {"uid": uid})
                        result["dependent_rows_deleted"][table_name] = (
                            result["dependent_rows_deleted"].get(table_name, 0)
                            + int(deleted.rowcount or 0)
                        )

                deleted_users = await db.execute(delete(User).where(User.id.in_(user_ids)))
                result["users_deleted"] = int(deleted_users.rowcount or 0)
                await db.commit()
            else:
                for table_name, _, count_sql in DEPENDENT_PRE_DELETES:
                    total = 0
                    for uid in user_ids:
                        total += int(await db.scalar(text(count_sql), {"uid": uid}) or 0)
                    if total:
                        result["dependent_rows_deleted"][table_name] = total

                for table_name, columns in reference_columns.items():
                    if table_name in PRE_DELETE_TABLES:
                        continue
                    total = 0
                    for uid in user_ids:
                        where_clause = " OR ".join(f"{_quote_ident(column)} = :uid" for column in columns)
                        count_stmt = text(
                            f"SELECT COUNT(*) FROM {_quote_ident(table_name)} WHERE {where_clause}"
                        )
                        total += int(await db.scalar(count_stmt, {"uid": uid}) or 0)
                    if total:
                        result["dependent_rows_deleted"][table_name] = total
                result["users_deleted"] = len(user_ids)
                await db.rollback()

            return result
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Required with --apply")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = asyncio.run(cleanup(apply=args.apply, yes=args.yes))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
