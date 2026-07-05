"""
scripts/cleanup_stale_data.py — One-time database cleanup for production readiness.

Removes test artifacts and stale sessions. Safe to run multiple times (idempotent).
Uses synchronous psycopg2 connection via DATABASE_URL_SYNC.

Usage:
    cd backend
    python scripts/cleanup_stale_data.py
"""
import os
import sys

# Ensure backend/ is on sys.path so config imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from sqlalchemy import create_engine, text

DATABASE_URL_SYNC = os.getenv(
    "DATABASE_URL_SYNC",
    "postgresql+psycopg2://pfe:fNvtHCN8bVWuFiDiG3ngJf1_xPLALLqU@localhost:5433/adaptiq_mw_db",
)


# ═══════════════════════════════════════════════════════════════════════════
# STALE USER FILTER
# ═══════════════════════════════════════════════════════════════════════════
# Matches e2e test users, smoke test users, and timestamped test users.
# Preserves seed users (points > 0) and manually-created test accounts.

STALE_USER_FILTER = """
(
    username LIKE 'e2e_%%'
    OR username LIKE 'smoke_%%'
    OR username LIKE 'testuser%%'
    OR username = 'DemoUser'
    OR username = 'testscholar2'
    OR email LIKE '%%@test.com'
)
AND points = 0
"""

# Users we explicitly keep (our real test accounts)
KEEP_EMAILS = [
    "testscholar@gmail.com",
    "scholar2@gmail.com",
    "onboardtest@gmail.com",
    "newscholar@gmail.com",
]


def main():
    engine = create_engine(DATABASE_URL_SYNC, echo=False)
    print("=" * 60)
    print("AdaptIQ — Database Cleanup Script")
    print("=" * 60)

    with engine.begin() as conn:
        # ── PHASE 1: Count what we're about to delete ─────────────────
        stale_count = conn.execute(text(f"""
            SELECT COUNT(*) FROM users
            WHERE {STALE_USER_FILTER}
              AND email NOT IN :keep_emails
        """), {"keep_emails": tuple(KEEP_EMAILS)}).scalar()
        print(f"\n[1/6] Found {stale_count} stale test users to remove")

        if stale_count > 0:
            # Get the IDs for dependency-ordered deletion
            stale_ids_result = conn.execute(text(f"""
                SELECT id FROM users
                WHERE {STALE_USER_FILTER}
                  AND email NOT IN :keep_emails
            """), {"keep_emails": tuple(KEEP_EMAILS)})
            stale_ids = [row[0] for row in stale_ids_result.fetchall()]

            # ── Delete in FK dependency order (children first) ────────
            # Layer 1: Leaf tables (no children)
            r1 = conn.execute(text(
                "DELETE FROM pvp_match_answers WHERE user_id = ANY(:ids)"
            ), {"ids": stale_ids}).rowcount
            print(f"   Deleted {r1} pvp_match_answers")

            # pvp_match_answers also references pvp_matches, so delete
            # answers for matches where BOTH players are stale
            r1b = conn.execute(text("""
                DELETE FROM pvp_match_answers WHERE match_id IN (
                    SELECT id FROM pvp_matches
                    WHERE user1_id = ANY(:ids) AND user2_id = ANY(:ids)
                )
            """), {"ids": stale_ids}).rowcount
            print(f"   Deleted {r1b} pvp_match_answers (from stale-vs-stale matches)")

            r2 = conn.execute(text(
                "DELETE FROM user_onboarding_topics WHERE user_id = ANY(:ids)"
            ), {"ids": stale_ids}).rowcount
            print(f"   Deleted {r2} user_onboarding_topics")

            r3 = conn.execute(text(
                "DELETE FROM user_onboarding_flags WHERE user_id = ANY(:ids)"
            ), {"ids": stale_ids}).rowcount
            print(f"   Deleted {r3} user_onboarding_flags")

            r4 = conn.execute(text(
                "DELETE FROM user_responses WHERE user_id = ANY(:ids)"
            ), {"ids": stale_ids}).rowcount
            print(f"   Deleted {r4} user_responses")

            r5 = conn.execute(text(
                "DELETE FROM user_fact_progress WHERE user_id = ANY(:ids)"
            ), {"ids": stale_ids}).rowcount
            print(f"   Deleted {r5} user_fact_progress")

            r6 = conn.execute(text(
                "DELETE FROM user_topic_mastery WHERE user_id = ANY(:ids)"
            ), {"ids": stale_ids}).rowcount
            print(f"   Deleted {r6} user_topic_mastery")

            r7 = conn.execute(text(
                "DELETE FROM custom_sessions WHERE user_id = ANY(:ids)"
            ), {"ids": stale_ids}).rowcount
            print(f"   Deleted {r7} custom_sessions")

            # challenge_answers links via session_id, not user_id
            r8 = conn.execute(text("""
                DELETE FROM challenge_answers WHERE session_id IN (
                    SELECT id FROM challenge_sessions WHERE user_id = ANY(:ids)
                )
            """), {"ids": stale_ids}).rowcount
            print(f"   Deleted {r8} challenge_answers")

            r9 = conn.execute(text(
                "DELETE FROM challenge_sessions WHERE user_id = ANY(:ids)"
            ), {"ids": stale_ids}).rowcount
            print(f"   Deleted {r9} challenge_sessions")

            r9b = conn.execute(text(
                "DELETE FROM challenge_ranking WHERE user_id = ANY(:ids)"
            ), {"ids": stale_ids}).rowcount
            print(f"   Deleted {r9b} challenge_ranking")

            r10 = conn.execute(text(
                "DELETE FROM pvp_matchmaking_queue WHERE user_id = ANY(:ids)"
            ), {"ids": stale_ids}).rowcount
            print(f"   Deleted {r10} pvp_matchmaking_queue")

            r11 = conn.execute(text(
                "DELETE FROM pvp_ratings WHERE user_id = ANY(:ids)"
            ), {"ids": stale_ids}).rowcount
            print(f"   Deleted {r11} pvp_ratings")

            # PvP matches: only delete if BOTH players are stale
            r12 = conn.execute(text("""
                DELETE FROM pvp_matches
                WHERE user1_id = ANY(:ids) AND user2_id = ANY(:ids)
            """), {"ids": stale_ids}).rowcount
            print(f"   Deleted {r12} pvp_matches (both players stale)")

            # For matches where only ONE player is stale, we keep the match
            # but need to remove the stale user's FK reference — skip these
            # (they'll become orphaned but the match data is preserved)

            # Now safe to delete the users (CASCADE handles classic_sessions,
            # user_concept_theta, user_concept_repeat_queue)
            r_users = conn.execute(text(f"""
                DELETE FROM users
                WHERE {STALE_USER_FILTER}
                  AND email NOT IN :keep_emails
            """), {"keep_emails": tuple(KEEP_EMAILS)}).rowcount
            print(f"   DONE: Deleted {r_users} stale users")

        # ── PHASE 2: Clean orphaned PvP queue entries ─────────────────
        q_count = conn.execute(text(
            "DELETE FROM pvp_matchmaking_queue WHERE status = 'matched'"
        )).rowcount
        print(f"\n[2/6] Cleaned {q_count} orphaned PvP queue entries (status=matched)")

        # ── PHASE 3: Abandon stale PvP matches ───────────────────────
        m_count = conn.execute(text("""
            UPDATE pvp_matches
            SET status = 'abandoned', ended_at = NOW()
            WHERE status = 'active'
              AND started_at < NOW() - interval '1 hour'
        """)).rowcount
        print(f"\n[3/6] Abandoned {m_count} stale PvP matches (active > 1 hour)")

        # ── PHASE 4: Complete stale challenge sessions ────────────────
        ch_count = conn.execute(text("""
            UPDATE challenge_sessions
            SET is_completed = true, ended_at = NOW()
            WHERE is_completed = false
              AND started_at < NOW() - interval '1 hour'
        """)).rowcount
        print(f"\n[4/6] Force-completed {ch_count} stale challenge sessions")

        # ── PHASE 5: Complete stale classic + custom sessions ─────────
        cl_count = conn.execute(text("""
            UPDATE classic_sessions
            SET ended_at = NOW()
            WHERE ended_at IS NULL
              AND created_at < NOW() - interval '1 hour'
        """)).rowcount
        cu_count = conn.execute(text("""
            UPDATE custom_sessions
            SET ended_at = NOW()
            WHERE ended_at IS NULL
              AND started_at < NOW() - interval '1 hour'
        """)).rowcount
        print(f"\n[5/6] Force-completed {cl_count} classic + {cu_count} custom stale sessions")

        # ── PHASE 6: Refresh statistics ───────────────────────────────
        conn.execute(text("ANALYZE"))
        print(f"\n[6/6] Ran ANALYZE on all tables")

    # ── FINAL REPORT ──────────────────────────────────────────────────
    with engine.connect() as conn:
        user_count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
        active_matches = conn.execute(text(
            "SELECT COUNT(*) FROM pvp_matches WHERE status = 'active'"
        )).scalar()
        queue_count = conn.execute(text(
            "SELECT COUNT(*) FROM pvp_matchmaking_queue"
        )).scalar()

        print("\n" + "=" * 60)
        print("CLEANUP COMPLETE — Final State:")
        print(f"  Users remaining:        {user_count}")
        print(f"  Active PvP matches:     {active_matches}")
        print(f"  PvP queue entries:      {queue_count}")
        print("=" * 60)


if __name__ == "__main__":
    main()
