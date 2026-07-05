"""
seeds/seed.py — Idempotent database seeding for AdaptIQ.

Seeds:
  - Concepts (per topic)
  - Questions (per concept, stored in question_bank)
  - QuestionConcept links (question → concept mapping)

Runs automatically on app startup if the database is empty (no concepts).
Safe to call multiple times — checks before inserting.
"""
import json
import uuid
import logging
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy import select, func, and_

from database.models import Base, User, QuestionBank
from database.concept_models import Concept, QuestionConcept

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# SEED DATA — Questions grouped by Topic → Concept
# ═══════════════════════════════════════════════════════════════════════════

SEED_DATA = {
    "history": {
        "Ancient Egypt": [
            {
                "text": "Which pharaoh built the Great Pyramid of Giza?",
                "correct": "Khufu",
                "wrong": ["Menkaure", "Khafre", "Pepi II"],
                "explanation": "Khufu (c. 2589-2566 BCE) built the Great Pyramid, the only surviving Wonder of the Ancient World.",
                "difficulty_irt": -1.0,
            },
            {
                "text": "What year did ancient Egypt end as an independent civilization?",
                "correct": "30 BC",
                "wrong": ["332 BC", "146 BC", "395 AD"],
                "explanation": "Egypt became a Roman province after Cleopatra VII's death in 30 BC.",
                "difficulty_irt": 0.0,
            },
            {
                "text": "What writing system was used in ancient Egypt?",
                "correct": "Hieroglyphics",
                "wrong": ["Cuneiform", "Latin script", "Runic alphabet"],
                "explanation": "Ancient Egyptians used hieroglyphics for formal writing on monuments and papyrus.",
                "difficulty_irt": -1.5,
            },
        ],
        "Ancient Rome": [
            {
                "text": "Who was the first Roman Emperor?",
                "correct": "Augustus",
                "wrong": ["Julius Caesar", "Nero", "Constantine"],
                "explanation": "Augustus (27 BC - 14 AD) was the first emperor after defeating Mark Antony.",
                "difficulty_irt": -0.5,
            },
            {
                "text": "In what year did the Western Roman Empire fall?",
                "correct": "476 AD",
                "wrong": ["410 AD", "395 AD", "500 AD"],
                "explanation": "The Western Roman Empire fell in 476 AD with the deposition of Romulus Augustulus.",
                "difficulty_irt": 0.0,
            },
            {
                "text": "What language did the Romans speak?",
                "correct": "Latin",
                "wrong": ["Greek", "Italian", "Aramaic"],
                "explanation": "Latin was the official language of the Roman Empire and influenced many modern languages.",
                "difficulty_irt": -2.0,
            },
        ],
        "Medieval Europe": [
            {
                "text": "Who was crowned as the first Holy Roman Emperor?",
                "correct": "Charlemagne",
                "wrong": ["Otto I", "Frederick Barbarossa", "Charles V"],
                "explanation": "Charlemagne was crowned on Christmas Day 800 AD by Pope Leo III.",
                "difficulty_irt": 0.5,
            },
            {
                "text": "What was the Black Death?",
                "correct": "A bubonic plague pandemic",
                "wrong": ["A volcanic eruption", "A famine", "A war"],
                "explanation": "The Black Death (1347-1351) killed 30-60% of Europe's population.",
                "difficulty_irt": -1.0,
            },
        ],
        "World War I": [
            {
                "text": "In which year did World War I begin?",
                "correct": "1914",
                "wrong": ["1912", "1916", "1918"],
                "explanation": "WWI began on July 28, 1914, triggered by the assassination of Archduke Franz Ferdinand.",
                "difficulty_irt": -1.5,
            },
            {
                "text": "Which event triggered the start of World War I?",
                "correct": "Assassination of Archduke Franz Ferdinand",
                "wrong": ["Sinking of the Lusitania", "Treaty of Versailles", "Russian Revolution"],
                "explanation": "The assassination of Archduke Franz Ferdinand of Austria on June 28, 1914 triggered WWI.",
                "difficulty_irt": -0.5,
            },
        ],
        "World War II": [
            {
                "text": "When did World War II end?",
                "correct": "1945",
                "wrong": ["1944", "1946", "1943"],
                "explanation": "WWII ended in 1945 with Germany's surrender in May and Japan's in September.",
                "difficulty_irt": -1.5,
            },
            {
                "text": "What was the code name for the Allied invasion of Normandy?",
                "correct": "Operation Overlord",
                "wrong": ["Operation Barbarossa", "Operation Market Garden", "Operation Torch"],
                "explanation": "D-Day, June 6, 1944, was part of Operation Overlord, the largest seaborne invasion in history.",
                "difficulty_irt": 0.5,
            },
        ],
        "Cold War": [
            {
                "text": "What wall symbolized the division between East and West during the Cold War?",
                "correct": "Berlin Wall",
                "wrong": ["Great Wall of China", "Hadrian's Wall", "Western Wall"],
                "explanation": "The Berlin Wall (1961-1989) divided East and West Berlin during the Cold War.",
                "difficulty_irt": -1.0,
            },
        ],
    },
    "geography": {
        "African Countries": [
            {
                "text": "What is the capital of South Africa (executive)?",
                "correct": "Pretoria",
                "wrong": ["Johannesburg", "Cape Town", "Durban"],
                "explanation": "South Africa has three capitals: Pretoria (executive), Cape Town (legislative), and Bloemfontein (judicial).",
                "difficulty_irt": 0.5,
            },
            {
                "text": "Which is the longest river in Africa?",
                "correct": "Nile River",
                "wrong": ["Congo River", "Niger River", "Zambezi River"],
                "explanation": "The Nile River is approximately 6,650 km long, the longest in Africa and possibly the world.",
                "difficulty_irt": -0.5,
            },
            {
                "text": "Which is the largest country in Africa by area?",
                "correct": "Algeria",
                "wrong": ["Democratic Republic of Congo", "Sudan", "Libya"],
                "explanation": "Algeria became the largest African country by area (2.38M km²) after South Sudan's independence.",
                "difficulty_irt": 1.0,
            },
        ],
        "Asian Capitals": [
            {
                "text": "What is the capital of Japan?",
                "correct": "Tokyo",
                "wrong": ["Kyoto", "Osaka", "Yokohama"],
                "explanation": "Tokyo has been Japan's capital since 1868.",
                "difficulty_irt": -2.0,
            },
            {
                "text": "Which city is the capital of Thailand?",
                "correct": "Bangkok",
                "wrong": ["Chiang Mai", "Phuket", "Pattaya"],
                "explanation": "Bangkok (Krung Thep Maha Nakhon) is Thailand's capital and largest city.",
                "difficulty_irt": -1.0,
            },
            {
                "text": "What is the capital of Mongolia?",
                "correct": "Ulaanbaatar",
                "wrong": ["Astana", "Bishkek", "Tashkent"],
                "explanation": "Ulaanbaatar is Mongolia's capital and home to nearly half the country's population.",
                "difficulty_irt": 1.5,
            },
        ],
        "European Geography": [
            {
                "text": "Which European country has the most inhabitants?",
                "correct": "Germany",
                "wrong": ["France", "United Kingdom", "Italy"],
                "explanation": "Germany has about 83 million inhabitants, making it the most populous EU country.",
                "difficulty_irt": -0.5,
            },
            {
                "text": "What is the smallest country in Europe?",
                "correct": "Vatican City",
                "wrong": ["Monaco", "San Marino", "Liechtenstein"],
                "explanation": "Vatican City is both the smallest country in Europe and the world at 0.44 km².",
                "difficulty_irt": -1.0,
            },
        ],
    },
}


async def seed_all(session_factory: async_sessionmaker) -> None:
    """Idempotent seed function — creates concepts, questions, and their links.

    Checks if concepts already exist before seeding. Safe to call on every startup.

    Args:
        session_factory: SQLAlchemy async session factory
    """
    async with session_factory() as session:
        # If both concepts and questions already exist, treat as seeded baseline.
        concept_count = (await session.execute(select(func.count(Concept.id)))).scalar() or 0
        question_count = (await session.execute(select(func.count(QuestionBank.id)))).scalar() or 0
        if concept_count > 0 and question_count > 0:
            logger.info(
                "Database already seeded (%d concepts, %d questions), skipping...",
                concept_count,
                question_count,
            )
            return

        logger.info("Seeding/repairing baseline concepts and questions...")
        total_concepts = 0
        total_questions = 0

        for topic, subtopics in SEED_DATA.items():
            for concept_name, questions in subtopics.items():
                concept_stmt = select(Concept).where(
                    and_(
                        Concept.name == concept_name,
                        Concept.topic == topic,
                    )
                )
                concept = (await session.execute(concept_stmt)).scalar_one_or_none()
                if concept is None:
                    concept = Concept(
                        id=uuid.uuid4(),
                        name=concept_name,
                        topic=topic,
                        description=f"{concept_name} in {topic}",
                    )
                    session.add(concept)
                    await session.flush()
                    total_concepts += 1

                # Create questions and link to concept
                for q in questions:
                    q_stmt = select(QuestionBank).where(
                        and_(
                            QuestionBank.topic == topic,
                            QuestionBank.question_text == q["text"],
                        )
                    )
                    question = (await session.execute(q_stmt)).scalar_one_or_none()
                    if question is None:
                        question_id = uuid.uuid4()
                        options = [q["correct"]] + q["wrong"]
                        import random
                        random.shuffle(options)

                        question = QuestionBank(
                            id=question_id,
                            question_text=q["text"],
                            options_json=json.dumps(options),
                            correct_answer=q["correct"],
                            explanation=q.get("explanation", ""),
                            difficulty_irt=q.get("difficulty_irt", 0.0),
                            topic=topic,
                            source="seed",
                        )
                        session.add(question)
                        await session.flush()
                        total_questions += 1

                    # Ensure question → concept mapping exists.
                    link_stmt = select(QuestionConcept).where(
                        and_(
                            QuestionConcept.question_id == question.id,
                            QuestionConcept.concept_id == concept.id,
                        )
                    )
                    existing_link = (await session.execute(link_stmt)).scalar_one_or_none()
                    if existing_link is None:
                        session.add(
                            QuestionConcept(
                                question_id=question.id,
                                concept_id=concept.id,
                                is_primary=True,
                            )
                        )

        await session.commit()
        logger.info(
            "Seeding complete: %d concepts created, %d questions created",
            total_concepts,
            total_questions,
        )
