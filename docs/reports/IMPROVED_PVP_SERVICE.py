"""
IMPROVED PvP SERVICE - Smart Matchmaking & Question Selection
This file contains the enhanced versions of key functions from pvp_service.py

Key improvements:
1. Concept mastery filtering (not just volume)
2. Mastery-weighted affinity scoring  
3. Difficulty-aware + reaper-free question selection
4. Tighter Elo matching
5. Better composite scoring

HOW TO USE:
- Copy these functions into your pvp_service.py
- Replace the existing implementations
- Test before deploying
"""

import json
import uuid
import random
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Set, Dict, List
from math import log10 as math_log10, pow as math_pow

from sqlalchemy import select, delete, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from database.pvp_models import (
    PvPMatchmakingQueue,
    PvPMatch,
    PvPMatchAnswer,
    PvPRating,
)
from database.models import User, QuestionBank, UserResponse
from database.concept_models import UserConceptTheta, QuestionConcept
from database.irt import beta_to_difficulty

logger = logging.getLogger(__name__)

ELO_K_NEW     = 32
ELO_K_REGULAR = 16
ELO_DEFAULT   = 1000.0
ELO_MAX_DIFF  = 150  # ✓ IMPROVED: Reduced from 300 to 150


# ═══════════════════════════════════════════════════════════════════════════
# IMPROVED: SMART CONCEPT SELECTION (Use Mastery, Not Volume)
# ═══════════════════════════════════════════════════════════════════════════

async def get_user_mastered_concepts(
    db: AsyncSession,
    user_id: uuid.UUID,
    min_response_count: int = 5,
) -> List[uuid.UUID]:
    """
    ✓ NEW: Get concepts a user has actually MASTERED.
    
    Instead of top-10 by response_count (volume = wrong answer count too),
    return only concepts where player has demonstrated competency.
    
    Args:
        db: AsyncSession
        user_id: User UUID
        min_response_count: Minimum attempts to consider "learned"
    
    Returns:
        List of concept UUIDs sorted by IRT theta (ability)
    """
    concepts = await db.execute(
        select(UserConceptTheta)
        .where(
            UserConceptTheta.user_id == user_id,
            # ✓ Only INTERMEDIATE or ADVANCED concepts
            UserConceptTheta.mastery_level.in_(["INTERMEDIATE", "ADVANCED"]),
            # ✓ Must have enough practice to validate mastery
            UserConceptTheta.response_count >= min_response_count,
        )
        # ✓ Sort by actual ability (theta), not attempt count
        .order_by(UserConceptTheta.theta.desc())
        .limit(15)  # ✓ More candidates for better matching
    )
    
    concept_rows = concepts.scalars().all()
    logger.info(
        "User %s has %d mastered concepts (INTERMEDIATE+)",
        str(user_id)[:8],
        len(concept_rows),
    )
    
    return [row.concept_id for row in concept_rows]


# ═══════════════════════════════════════════════════════════════════════════
# IMPROVED: MASTERY-WEIGHTED CONCEPT AFFINITY SCORING
# ═══════════════════════════════════════════════════════════════════════════

async def calculate_concept_affinity(
    db: AsyncSession,
    user1_id: uuid.UUID,
    user2_id: uuid.UUID,
    shared_concept_ids: Set[uuid.UUID],
) -> float:
    """
    ✓ NEW: Calculate affinity weighted by mastery alignment.
    
    Old: overlap = len(set1 & set2) → binary 0/1 per concept
    New: affinity = 1/(1+theta_diff) → penalizes skill mismatches
    
    Example:
        Shared concept "Algebra":
        - User A theta=+2.0, User B theta=+1.8 → affinity=0.96 (great match!)
        - User A theta=+2.0, User B theta=-1.0 → affinity=0.40 (poor match)
        - User A theta=+0.5, User B theta=+0.3 → affinity=0.87 (good)
    
    Args:
        db: AsyncSession
        user1_id: First player UUID
        user2_id: Second player UUID
        shared_concept_ids: Set of concept UUIDs both know
    
    Returns:
        float: 0.0-1.0 affinity score (higher = better match)
    """
    if not shared_concept_ids:
        return 0.0
    
    total_affinity = 0.0
    
    for concept_id in shared_concept_ids:
        # Get both players' ability (theta) for this concept
        theta1_row = await db.scalar(
            select(UserConceptTheta.theta)
            .where(
                UserConceptTheta.user_id == user1_id,
                UserConceptTheta.concept_id == concept_id,
            )
        )
        theta1 = float(theta1_row or 0.0)
        
        theta2_row = await db.scalar(
            select(UserConceptTheta.theta)
            .where(
                UserConceptTheta.user_id == user2_id,
                UserConceptTheta.concept_id == concept_id,
            )
        )
        theta2 = float(theta2_row or 0.0)
        
        # ✓ Penalize large theta gaps
        # If gap = 0: affinity = 1.0 (perfect match)
        # If gap = 1: affinity = 0.5 (acceptable)
        # If gap = 3: affinity = 0.25 (poor match)
        theta_diff = abs(theta1 - theta2)
        affinity = 1.0 / (1.0 + theta_diff)
        total_affinity += affinity
    
    # Average affinity across shared concepts
    avg_affinity = total_affinity / len(shared_concept_ids)
    
    logger.debug(
        "Affinity between user1=%s user2=%s: shared=%d avg_affinity=%.2f",
        str(user1_id)[:8],
        str(user2_id)[:8],
        len(shared_concept_ids),
        avg_affinity,
    )
    
    return avg_affinity


# ═══════════════════════════════════════════════════════════════════════════
# IMPROVED: REAPER-AWARE QUESTION LOOKUP
# ═══════════════════════════════════════════════════════════════════════════

async def get_user_answered_questions(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> Set[uuid.UUID]:
    """
    ✓ NEW: Get all questions a user has answered across ALL rooms.
    
    This prevents "reaper questions" - questions the player has already
    answered in Classic/Challenge rooms appearing again in PvP.
    
    Args:
        db: AsyncSession
        user_id: User UUID
    
    Returns:
        Set of question UUIDs player has answered
    """
    result = await db.execute(
        select(UserResponse.question_id)
        .where(UserResponse.user_id == user_id)
    )
    
    answered_ids = set(row[0] for row in result.fetchall())
    
    logger.debug(
        "User %s has answered %d questions",
        str(user_id)[:8],
        len(answered_ids),
    )
    
    return answered_ids


# ═══════════════════════════════════════════════════════════════════════════
# IMPROVED: DIFFICULTY-AWARE QUESTION SELECTION
# ═══════════════════════════════════════════════════════════════════════════

async def select_pvp_questions_improved(
    db: AsyncSession,
    user1_id: uuid.UUID,
    user2_id: uuid.UUID,
    topic: str,
    count: int = 5,
) -> List[QuestionBank]:
    """
    ✓ NEW: Select questions with THREE criteria:
    
    1. From concepts BOTH players have mastered
    2. With difficulty matching their average IRT ability
    3. NOT previously answered (avoid reaper questions)
    
    This ensures fair, fresh, skill-matched questions.
    
    Fallback: If not enough shared concepts, use topic-level questions
    (but still filter for difficulty and reaper avoidance).
    
    Args:
        db: AsyncSession
        user1_id: First player UUID
        user2_id: Second player UUID
        topic: Quiz topic (e.g., "History", "Mixed")
        count: Number of questions to select (default 5)
    
    Returns:
        List of QuestionBank rows
    """
    
    # ═════════════════════════════════════════════════════════════════════
    # Step 1: Find concepts BOTH players have mastered
    # ═════════════════════════════════════════════════════════════════════
    
    concepts_user1 = await get_user_mastered_concepts(db, user1_id)
    concepts_user2 = await get_user_mastered_concepts(db, user2_id)
    
    shared_concepts = set(concepts_user1) & set(concepts_user2)
    
    logger.info(
        "PvP question selection: user1=%s has %d concepts, user2=%s has %d, shared=%d",
        str(user1_id)[:8], len(concepts_user1),
        str(user2_id)[:8], len(concepts_user2),
        len(shared_concepts),
    )
    
    # ═════════════════════════════════════════════════════════════════════
    # Step 2: Get reaper-avoided question IDs
    # ═════════════════════════════════════════════════════════════════════
    
    answered_user1 = await get_user_answered_questions(db, user1_id)
    answered_user2 = await get_user_answered_questions(db, user2_id)
    all_answered = answered_user1 | answered_user2
    
    logger.info(
        "Excluding reaper questions: user1 answered=%d user2 answered=%d union=%d",
        len(answered_user1),
        len(answered_user2),
        len(all_answered),
    )
    
    # ═════════════════════════════════════════════════════════════════════
    # Step 3: Select from shared concepts with difficulty matching
    # ═════════════════════════════════════════════════════════════════════
    
    selected_questions: List[QuestionBank] = []
    
    for concept_id in shared_concepts:
        if len(selected_questions) >= count:
            break
        
        # Get both players' IRT ability for this concept
        theta1_row = await db.scalar(
            select(UserConceptTheta.theta)
            .where(
                UserConceptTheta.user_id == user1_id,
                UserConceptTheta.concept_id == concept_id,
            )
        )
        theta1 = float(theta1_row or 0.0)
        
        theta2_row = await db.scalar(
            select(UserConceptTheta.theta)
            .where(
                UserConceptTheta.user_id == user2_id,
                UserConceptTheta.concept_id == concept_id,
            )
        )
        theta2 = float(theta2_row or 0.0)
        
        # ✓ Use average theta to determine target difficulty
        avg_theta = (theta1 + theta2) / 2.0
        target_difficulty = beta_to_difficulty(avg_theta)
        
        logger.debug(
            "Concept %s: user1_theta=%.2f user2_theta=%.2f avg=%.2f target_diff=%d",
            str(concept_id)[:8],
            theta1, theta2, avg_theta, target_difficulty,
        )
        
        # ✓ Select questions for this concept + difficulty
        candidates = await db.execute(
            select(QuestionBank)
            .join(
                QuestionConcept,
                QuestionConcept.question_id == QuestionBank.id,
            )
            .where(
                QuestionConcept.concept_id == concept_id,
                QuestionBank.topic.ilike(f"%{topic}%"),
                # ✓ REAPER FILTER: Exclude answered questions
                QuestionBank.id.notin_(all_answered),
                # ✓ DIFFICULTY MATCH: Within ±1 band of target
                QuestionBank.difficulty.between(target_difficulty - 1, target_difficulty + 1),
            )
            .order_by(func.random())
            .limit(10)  # Get more candidates, pick randomly
        )
        
        candidates_list = candidates.scalars().all()
        if candidates_list:
            # Pick random from candidates to add variety
            selected = random.choice(candidates_list)
            selected_questions.append(selected)
    
    # ═════════════════════════════════════════════════════════════════════
    # Step 4: Fallback if not enough shared-concept questions
    # ═════════════════════════════════════════════════════════════════════
    
    if len(selected_questions) < count:
        needed = count - len(selected_questions)
        selected_ids = set(q.id for q in selected_questions)
        
        # Get topic-level questions (with reaper filtering still applied)
        fallback = await db.execute(
            select(QuestionBank)
            .where(
                QuestionBank.topic.ilike(f"%{topic}%"),
                # ✓ Still avoid reaper
                QuestionBank.id.notin_(all_answered),
                # Don't duplicate
                QuestionBank.id.notin_(selected_ids),
            )
            .order_by(func.random())
            .limit(needed)
        )
        
        fallback_list = fallback.scalars().all()
        selected_questions.extend(fallback_list)
        
        logger.info(
            "Fallback: Needed %d more questions, got %d from topic-level pool",
            needed, len(fallback_list),
        )
    
    logger.info(
        "Final selection: %d questions (shared concepts=%d, fallback=%d)",
        len(selected_questions),
        len([q for q in selected_questions if q.id in selected_ids]),
        len([q for q in selected_questions if q.id not in selected_ids]),
    )
    
    return selected_questions[:count]


# ═══════════════════════════════════════════════════════════════════════════
# IMPROVED: BETTER CANDIDATE SCORING
# ═══════════════════════════════════════════════════════════════════════════

async def score_candidate(
    db: AsyncSession,
    entry: PvPMatchmakingQueue,
    candidate: PvPMatchmakingQueue,
) -> float:
    """
    ✓ NEW: Score a candidate with weighted factors.
    
    Scoring: 50% concept affinity + 40% Elo + 10% recency
    
    Args:
        db: AsyncSession
        entry: Current player's queue entry
        candidate: Potential opponent's queue entry
    
    Returns:
        float: 0.0-1.0 composite score
    """
    
    # ─── Factor 1: Elo Closeness (40%) ───
    elo_diff = abs(float(entry.elo_rating) - float(candidate.elo_rating))
    
    # ✓ Hard reject if too far apart
    if elo_diff > ELO_MAX_DIFF:  # ✓ 150 instead of 300
        return 0.0
    
    # Linear penalty from 1.0 (identical) to 0.0 (at max diff)
    elo_score = 1.0 - (elo_diff / float(ELO_MAX_DIFF))
    
    # ─── Factor 2: Concept Affinity (50%) ───
    my_concepts = set(json.loads(entry.concepts_json or "[]"))
    their_concepts = set(json.loads(candidate.concepts_json or "[]"))
    shared = my_concepts & their_concepts
    
    affinity_score = await calculate_concept_affinity(
        db, entry.user_id, candidate.user_id, shared
    )
    
    # ─── Factor 3: Recency (10%) ───
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    time_diff = now - candidate.joined_at
    
    if time_diff.total_seconds() > 600:  # >10 min old
        recency_score = 0.3  # Low priority for stale entries
    else:
        recency_score = 1.0
    
    # ─── Composite Score ───
    # Prioritize: Affinity (most important) > Elo > Recency
    final_score = (
        affinity_score * 0.50 +
        elo_score * 0.40 +
        recency_score * 0.10
    )
    
    logger.debug(
        "Candidate score: candidate=%s elo_score=%.2f affinity=%.2f recency=%.2f final=%.2f",
        str(candidate.user_id)[:8],
        elo_score,
        affinity_score,
        recency_score,
        final_score,
    )
    
    return final_score


# ═══════════════════════════════════════════════════════════════════════════
# IMPROVED: UPDATED MATCHMAKING WITH BETTER SCORING
# ═══════════════════════════════════════════════════════════════════════════

async def try_matchmaking_improved(
    db: AsyncSession,
    entry: PvPMatchmakingQueue,
) -> Optional[PvPMatch]:
    """
    ✓ IMPROVED: Matchmaking with better candidate scoring.
    
    Now considers:
    - Mastered concepts (not just volume)
    - Concept affinity with mastery weighting
    - Tighter Elo matching (±150 instead of 300)
    - Composite scoring (affinity > elo > recency)
    
    Args:
        db: AsyncSession
        entry: Player's queue entry
    
    Returns:
        PvPMatch if matched, None if no opponent found
    """
    
    # Clean stale entries (>10 min old)
    stale_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
    await db.execute(
        delete(PvPMatchmakingQueue).where(
            PvPMatchmakingQueue.status == "waiting",
            PvPMatchmakingQueue.joined_at < stale_cutoff,
        )
    )
    
    # Find potential opponents
    query = (
        select(PvPMatchmakingQueue)
        .where(
            PvPMatchmakingQueue.user_id != entry.user_id,
            PvPMatchmakingQueue.status == "waiting",
            # ✓ Tighter Elo range (±150 instead of 300)
            PvPMatchmakingQueue.elo_rating >= entry.elo_rating - ELO_MAX_DIFF,
            PvPMatchmakingQueue.elo_rating <= entry.elo_rating + ELO_MAX_DIFF,
        )
        .order_by(func.random())
        .limit(10)  # More candidates for better selection
    )
    
    # Filter by topic compatibility
    if entry.topic != "Mixed":
        query = query.where(
            (PvPMatchmakingQueue.topic == entry.topic)
            | (PvPMatchmakingQueue.topic == "Mixed")
        )
    
    result = await db.execute(query)
    candidates = result.scalars().all()
    
    if not candidates:
        logger.info("No candidates found for user=%s", str(entry.user_id)[:8])
        return None
    
    # ✓ Score all candidates and pick the best
    best_candidate = None
    best_score = 0.0
    
    for candidate in candidates:
        score = await score_candidate(db, entry, candidate)
        if score > best_score:
            best_score = score
            best_candidate = candidate
    
    if best_score < 0.1:  # Threshold for minimum acceptable score
        logger.info(
            "No good candidates found (best_score=%.2f < 0.1) for user=%s",
            best_score,
            str(entry.user_id)[:8],
        )
        return None
    
    logger.info(
        "Match found: user1=%s user2=%s score=%.2f",
        str(entry.user_id)[:8],
        str(best_candidate.user_id)[:8],
        best_score,
    )
    
    # Create match with improved question selection
    topic = entry.topic if entry.topic != "Mixed" else best_candidate.topic
    match = await create_match_improved(db, entry.user_id, best_candidate.user_id, topic)
    
    # Mark both as matched
    entry.status = "matched"
    best_candidate.status = "matched"
    await db.commit()
    
    return match


# ═══════════════════════════════════════════════════════════════════════════
# IMPROVED: CREATE MATCH WITH BETTER QUESTIONS
# ═══════════════════════════════════════════════════════════════════════════

async def create_match_improved(
    db: AsyncSession,
    user1_id: uuid.UUID,
    user2_id: uuid.UUID,
    topic: str,
) -> PvPMatch:
    """
    ✓ IMPROVED: Create PvP match with smart question selection.
    
    Now selects questions that are:
    - From concepts both players know
    - Matching their difficulty level
    - Not previously answered (no reaper)
    
    Args:
        db: AsyncSession
        user1_id: First player UUID
        user2_id: Second player UUID
        topic: Quiz topic
    
    Returns:
        PvPMatch with improved question_json
    """
    
    # ✓ Use improved question selection
    questions = await select_pvp_questions_improved(
        db,
        user1_id,
        user2_id,
        topic,
        count=5,
    )
    
    # Build questions JSON
    questions_data = []
    for i, q in enumerate(questions):
        options = json.loads(q.options_json or "[]")
        random.shuffle(options)
        questions_data.append({
            "id": str(q.id),
            "text": q.question_text,
            "options": options,
            "correctAnswer": q.correct_answer,
            "explanation": q.explanation or "",
            "index": i,
        })
    
    match = PvPMatch(
        user1_id=user1_id,
        user2_id=user2_id,
        topic=topic,
        status="active",
        total_questions=len(questions_data),
        questions_json=json.dumps(questions_data),
    )
    db.add(match)
    await db.commit()
    await db.refresh(match)
    
    logger.info(
        "PvP match created: %s (user1=%s user2=%s topic=%s, %d questions)",
        str(match.id)[:8],
        str(user1_id)[:8],
        str(user2_id)[:8],
        topic,
        len(questions_data),
    )
    
    return match


# ═══════════════════════════════════════════════════════════════════════════
# MIGRATION INSTRUCTIONS
# ═══════════════════════════════════════════════════════════════════════════

"""
HOW TO INTEGRATE:

1. Add these imports to pvp_service.py:
   from database.concept_models import UserConceptTheta, QuestionConcept
   from database.models import UserResponse
   from database.irt import beta_to_difficulty

2. Update join_queue() to use improved matching:
   # Replace the _try_matchmaking call with:
   match = await try_matchmaking_improved(db, entry)

3. Update _try_matchmaking references to call improved version:
   # In get_queue_status():
   match = await try_matchmaking_improved(db, entry)

4. Update _create_match to use new function:
   # Replace the entire _create_match() with create_match_improved()

5. Test before deploying:
   - Verify concept selection filters to INTERMEDIATE+
   - Verify affinity scoring penalizes theta gaps
   - Verify reaper filtering removes answered questions
   - Verify difficulty matching works

6. Monitor after deployment:
   - Track match quality (player feedback)
   - Track difficulty distribution
   - Track Elo changes (should be more stable)
   - Track reaper occurrence (should <5%)
"""
