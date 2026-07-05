"""Offline question-difficulty recalibration (roadmap item 2).

Item difficulty (`question_bank.difficulty_irt`) is frozen at seed/generation time
and never learns from real answer data. This standalone job aggregates
`user_responses` outcomes per question and writes a learned difficulty into the
**shadow** column `difficulty_irt_calibrated` (never the served `difficulty_irt`),
so recalibration stays off the request path and remains reversible until a value
is reviewed and promoted.

Model: 1PL. Assuming an average learner ability θ≈0 across a question's responses,
    p_correct = 1 / (1 + exp(-(θ - β)))  ⇒  β = ln((1 - p) / p)   (θ=0)
The learned β is mapped back to the 1-5 difficulty scale used by the column via the
inverse of difficulty_to_beta_continuous (difficulty = β + 3), clamped to [1, 5].

Only questions with at least --min-sample responses are recalibrated.

Usage:
    python scripts/recalibrate_question_difficulty.py --dry-run
    python scripts/recalibrate_question_difficulty.py --apply --min-sample 20

The script never prints secrets. Dry-run performs no writes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Integer, cast, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from config import DATABASE_URL  # noqa: E402
from database.models import QuestionBank, UserResponse  # noqa: E402

# Clamp the observed correct-rate away from 0/1 so the logit stays finite.
_P_MIN = 0.05
_P_MAX = 0.95
_DIFF_MIN = 1.0
_DIFF_MAX = 5.0


def learned_difficulty(correct: int, total: int) -> float:
    """Map an observed correct-rate to a 1-5 difficulty via the 1PL logit (θ=0)."""
    if total <= 0:
        return 3.0
    p = correct / total
    p = max(_P_MIN, min(_P_MAX, p))
    beta = math.log((1.0 - p) / p)  # harder question (low p) -> higher beta
    difficulty = beta + 3.0  # inverse of difficulty_to_beta_continuous
    return round(max(_DIFF_MIN, min(_DIFF_MAX, difficulty)), 3)


async def recalibrate(min_sample: int, apply: bool) -> dict:
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    scanned = 0
    eligible = 0
    updated = 0
    changed_examples: list[dict] = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        async with factory() as db:
            # Aggregate outcomes per question in a single grouped query.
            rows = (
                await db.execute(
                    select(
                        UserResponse.question_id,
                        func.count().label("total"),
                        func.sum(cast(UserResponse.answered_correct, Integer)).label("correct"),
                    ).group_by(UserResponse.question_id)
                )
            ).all()

            for question_id, total, correct in rows:
                scanned += 1
                total = int(total or 0)
                correct = int(correct or 0)
                if total < min_sample:
                    continue
                eligible += 1
                new_difficulty = learned_difficulty(correct, total)

                if apply:
                    await db.execute(
                        update(QuestionBank)
                        .where(QuestionBank.id == question_id)
                        .values(
                            difficulty_irt_calibrated=new_difficulty,
                            calibrated_at=now,
                            calibration_sample=total,
                        )
                    )
                updated += 1
                if len(changed_examples) < 10:
                    changed_examples.append(
                        {
                            "question_id": str(question_id),
                            "sample": total,
                            "correct_rate": round(correct / total, 3),
                            "calibrated_difficulty": new_difficulty,
                        }
                    )

            if apply:
                await db.commit()
    finally:
        await engine.dispose()

    return {
        "mode": "apply" if apply else "dry-run",
        "min_sample": min_sample,
        "questions_with_responses": scanned,
        "eligible_for_calibration": eligible,
        "calibrated": updated,
        "examples": changed_examples,
        "note": "writes shadow column difficulty_irt_calibrated; served difficulty_irt unchanged",
        "status": "ok",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline question-difficulty recalibration")
    parser.add_argument("--apply", action="store_true", help="write the shadow column (default: dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="no writes (default)")
    parser.add_argument("--min-sample", type=int, default=20, help="min responses per question to calibrate")
    args = parser.parse_args()

    apply = bool(args.apply) and not args.dry_run
    result = asyncio.run(recalibrate(min_sample=args.min_sample, apply=apply))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
