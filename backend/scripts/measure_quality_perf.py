"""Quality & performance measurement harness (roadmap validation).

Two independent modes so flags can be evaluated before promoting them to default:

  Quality  (offline, reads the DB):
    Computes the observed correct-rate overall and per served difficulty bucket
    from `user_responses`. The ZPD target is a 60-75% correct-rate; this reports
    how much answer volume lands inside that band (higher is better targeting).
    Use before/after `ENABLE_IRT_LOGIT_SCALE` to see success-rate-at-target move.

  Latency  (live, hits a running backend):
    Signs up a throwaway user (cookie auth), then times N classic question
    requests and reports p50/p95/p99. Use before/after Wave A
    (`ENABLE_CANDIDATE_POOL_SAMPLING` + `ENABLE_SEEN_SET_CACHE`).

Usage:
    python scripts/measure_quality_perf.py --quality
    python scripts/measure_quality_perf.py --latency --base http://127.0.0.1:8000 --n 40

Never prints secrets. Latency mode creates a disposable account only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

ZPD_LOW = 0.60
ZPD_HIGH = 0.75


async def measure_quality() -> dict:
    from sqlalchemy import Integer, cast, func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from config import DATABASE_URL
    from database.models import UserResponse

    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            total = int(await db.scalar(select(func.count()).select_from(UserResponse)) or 0)
            correct = int(
                await db.scalar(
                    select(func.count()).select_from(UserResponse).where(
                        UserResponse.answered_correct == True  # noqa: E712
                    )
                )
                or 0
            )
            rows = (
                await db.execute(
                    select(
                        UserResponse.difficulty_sent,
                        func.count().label("n"),
                        func.sum(cast(UserResponse.answered_correct, Integer)).label("c"),
                    ).group_by(UserResponse.difficulty_sent)
                )
            ).all()
    finally:
        await engine.dispose()

    by_difficulty = {}
    in_band = 0
    for difficulty, n, c in rows:
        n = int(n or 0)
        c = int(c or 0)
        rate = round(c / n, 3) if n else 0.0
        by_difficulty[str(difficulty)] = {"responses": n, "correct_rate": rate}
        if ZPD_LOW <= rate <= ZPD_HIGH:
            in_band += n

    overall = round(correct / total, 3) if total else 0.0
    return {
        "mode": "quality",
        "total_responses": total,
        "overall_correct_rate": overall,
        "zpd_target": [ZPD_LOW, ZPD_HIGH],
        "responses_in_zpd_band": in_band,
        "pct_responses_in_zpd_band": round((in_band / total) * 100, 1) if total else 0.0,
        "by_difficulty_sent": dict(sorted(by_difficulty.items())),
        "note": "higher pct_responses_in_zpd_band = better difficulty targeting",
    }


async def measure_latency(base: str, n: int) -> dict:
    import httpx

    ts = int(time.time())
    email = f"perfprobe_{ts}_{uuid.uuid4().hex[:6]}@example.com"
    username = f"perfprobe_{ts}"
    password = "PerfProbe123!"

    timings_ms: list[float] = []
    async with httpx.AsyncClient(base_url=base, timeout=30.0) as client:
        signup = await client.post(
            "/api/auth/signup",
            json={"email": email, "username": username, "password": password},
        )
        signup.raise_for_status()
        csrf = client.cookies.get("adaptiq_csrf")
        headers = {"X-CSRF-Token": csrf} if csrf else {}

        session_id: str | None = None
        for _ in range(n):
            body = {"topic": "history", "difficulty": 3}
            if session_id:
                body["session_id"] = session_id
            t0 = time.perf_counter()
            resp = await client.post("/api/rooms/classic/questions", json=body, headers=headers)
            timings_ms.append((time.perf_counter() - t0) * 1000.0)
            if resp.status_code == 200:
                data = resp.json()
                session_id = data.get("session_id") or session_id
                # Answer so the next request advances (and warms the seen-set cache).
                qid = data.get("id")
                if qid and session_id:
                    await client.post(
                        "/api/rooms/classic/answers",
                        json={
                            "session_id": session_id,
                            "question_id": qid,
                            "selected_index": 0,
                            "time_taken": 5,
                            "used_hint": False,
                        },
                        headers=headers,
                    )

    timings_ms.sort()

    def pct(p: float) -> float:
        if not timings_ms:
            return 0.0
        idx = min(len(timings_ms) - 1, int(round((p / 100.0) * (len(timings_ms) - 1))))
        return round(timings_ms[idx], 1)

    return {
        "mode": "latency",
        "base": base,
        "requests": len(timings_ms),
        "p50_ms": pct(50),
        "p95_ms": pct(95),
        "p99_ms": pct(99),
        "min_ms": round(timings_ms[0], 1) if timings_ms else 0.0,
        "max_ms": round(timings_ms[-1], 1) if timings_ms else 0.0,
        "mean_ms": round(statistics.mean(timings_ms), 1) if timings_ms else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Quality & performance measurement harness")
    parser.add_argument("--quality", action="store_true", help="offline correct-rate / ZPD-band analysis")
    parser.add_argument("--latency", action="store_true", help="live classic question latency probe")
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="backend base URL for --latency")
    parser.add_argument("--n", type=int, default=40, help="number of requests for --latency")
    args = parser.parse_args()

    if not args.quality and not args.latency:
        args.quality = True

    result: dict = {}
    if args.quality:
        result["quality"] = asyncio.run(measure_quality())
    if args.latency:
        result["latency"] = asyncio.run(measure_latency(args.base, args.n))

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
