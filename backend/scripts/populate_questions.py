"""
scripts/populate_questions.py

Populate question_bank with real LLM-generated questions.

Usage:
    python3.13 scripts/populate_questions.py --topic geography --count 5
    python3.13 scripts/populate_questions.py --topic history --count 5
"""

import asyncio
import json
import sys
import argparse
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from database.models import QuestionBank, Base
from database.concept_models import Concept, QuestionConcept
from services.llm import LLMClient
from database.irt import difficulty_to_beta
import config


async def populate_topic(
    db: AsyncSession,
    llm_client: LLMClient,
    topic: str,
    n_questions_per_concept: int = 5,
    n_per_difficulty: int = 1,
) -> int:
    """
    Populate questions for a topic.

    Args:
        db: Database session
        llm_client: LLM client
        topic: Topic name (geography, history, mix)
        n_questions_per_concept: Total questions per concept
        n_per_difficulty: How many of each difficulty (1-5)

    Returns:
        Total questions created
    """
    from sqlalchemy import select

    # Get all concepts for this topic
    concepts_stmt = select(Concept).where(Concept.topic == topic)
    result = await db.execute(concepts_stmt)
    concepts = result.scalars().all()

    if not concepts:
        print(f"[FAIL] No concepts found for topic: {topic}")
        return 0

    print(f"\n[INFO] Populating {topic} with {len(concepts)} concepts")
    print(f"   Questions per concept: {n_questions_per_concept}")

    total_created = 0

    for concept in concepts:
        print(f"\n   > {concept.name} ({concept.description or ''})")

        created_for_concept = 0

        # Generate questions across difficulty levels
        for difficulty in range(1, 6):
            for attempt in range(n_per_difficulty):
                try:
                    print(f"      Generating difficulty {difficulty}...", end=" ", flush=True)

                    # Create concept-focused prompt
                    prompt = create_concept_prompt(
                        topic=topic,
                        concept=concept.name,
                        difficulty=difficulty,
                    )

                    # Generate MCQ via LLM
                    question_data = await llm_client.generate_mcq(
                        context=prompt,
                        topic=topic,
                        difficulty=difficulty,
                    )

                    if not question_data:
                        print("[FAIL]")
                        continue

                    # Create QuestionBank record
                    question = QuestionBank(
                        question_text=question_data["text"],
                        correct_answer=question_data["correctAnswer"],
                        options_json=json.dumps(question_data["options"]),
                        explanation=question_data.get("explanation", ""),
                        topic=topic,
                        difficulty_irt=difficulty_to_beta(difficulty),
                        source="llm",
                        primary_concept_id=concept.id,
                        times_seen=0,
                        usage_count=0,
                    )

                    db.add(question)
                    await db.flush()

                    # Link to concept
                    qc = QuestionConcept(
                        question_id=question.id,
                        concept_id=concept.id,
                        is_primary=True,
                    )
                    db.add(qc)
                    await db.commit()

                    created_for_concept += 1
                    total_created += 1
                    print("[OK]")

                except Exception as e:
                    print(f"[ERROR] {e}")
                    await db.rollback()
                    continue

        print(f"      Created: {created_for_concept} questions")

    print(f"\n[SUCCESS] Total questions created: {total_created}")
    return total_created


def create_concept_prompt(topic: str, concept: str, difficulty: int) -> str:
    """Create detailed prompt for concept-specific question generation."""

    difficulty_guidance = {
        1: "very basic fact that anyone should know",
        2: "basic known fact with minor context",
        3: "requires connecting two related facts",
        4: "requires deeper knowledge or less-famous facts",
        5: "expert-level, obscure, or advanced reasoning required",
    }

    guidance = difficulty_guidance.get(difficulty, "intermediate")

    context = f"""Topic: {topic}
Concept: {concept}
Difficulty: {difficulty}/5 - Generate a {guidance}

Generate a multiple-choice question about {concept} that teaches understanding of this concept.
Make sure the question tests factual knowledge and understanding, not just memorization.
Include plausible but clearly wrong answers that match the difficulty level.
"""

    return context


async def main():
    parser = argparse.ArgumentParser(description="Populate question bank with LLM-generated questions")
    parser.add_argument("--topic", required=True, help="Topic: geography, history, or mix")
    parser.add_argument("--count", type=int, default=5, help="Questions per concept (default: 5)")
    parser.add_argument("--dry-run", action="store_true", help="Don't save, just preview")

    args = parser.parse_args()

    if args.topic not in ["geography", "history", "mix"]:
        print(f"[FAIL] Invalid topic: {args.topic}. Must be: geography, history, or mix")
        sys.exit(1)

    # Load config
    # config module values are already imported at top

    # Create async engine
    engine = create_async_engine(
        config.DATABASE_URL,
        echo=False,
        future=True,
    )

    # Create session factory
    AsyncSessionLocal = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    # Initialize LLM client
    if not config.GROQ_API_KEY:
        print("[FAIL] GROQ_API_KEY not set in environment")
        sys.exit(1)

    llm_client = LLMClient(api_key=config.GROQ_API_KEY)

    try:
        async with AsyncSessionLocal() as session:
            total = await populate_topic(
                session,
                llm_client,
                topic=args.topic,
                n_questions_per_concept=args.count,
            )

            if total > 0:
                print(f"\n[SUCCESS] Successfully created {total} questions for {args.topic}")
            else:
                print(f"\n[WARN] No questions created for {args.topic}")

    finally:
        await llm_client.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
