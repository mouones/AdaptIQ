# PvP Improvements - Before & After Comparison

## 1️⃣ CONCEPT SELECTION (Most Important Change)

### ❌ BEFORE (pvp_service.py line ~120)
```python
# Get user's concept IDs for matching
concept_rows = await db.execute(
    select(UserConceptTheta.concept_id)
    .where(UserConceptTheta.user_id == user_id)
    .order_by(UserConceptTheta.response_count.desc())  # ❌ WRONG: volume ≠ mastery
    .limit(10)
)
concept_ids = [str(row[0]) for row in concept_rows.fetchall()]
```

**Problem:**
- Counts total attempts, not quality
- 20 wrong answers = 20 right answers
- Ignores `mastery_level` field (exists but unused!)
- Ignores IRT `theta` (ability level)

### ✅ AFTER
```python
async def get_user_mastered_concepts(
    db: AsyncSession,
    user_id: uuid.UUID,
    min_response_count: int = 5,
) -> List[uuid.UUID]:
    """Get concepts player has MASTERED (INTERMEDIATE+)."""
    concepts = await db.execute(
        select(UserConceptTheta)
        .where(
            UserConceptTheta.user_id == user_id,
            UserConceptTheta.mastery_level.in_(["INTERMEDIATE", "ADVANCED"]),  # ✓ Filters to mastered
            UserConceptTheta.response_count >= min_response_count,  # ✓ Must practice
        )
        .order_by(UserConceptTheta.theta.desc())  # ✓ Sort by ability, not count
        .limit(15)
    )
    return [row.concept_id for row in concepts.scalars().all()]

# Usage:
concepts_user1 = await get_user_mastered_concepts(db, user1_id)
concepts_user2 = await get_user_mastered_concepts(db, user2_id)
shared_concepts = set(concepts_user1) & set(concepts_user2)
```

**Benefits:**
- ✓ Only uses MASTERED concepts
- ✓ Uses IRT ability (theta)
- ✓ Respects mastery_level field
- ✓ More candidates for better matching (15 vs 10)

---

## 2️⃣ CONCEPT AFFINITY SCORING

### ❌ BEFORE (pvp_service.py line ~160)
```python
best_score = 0
my_concepts = set(json.loads(entry.concepts_json or "[]"))

for candidate in candidates:
    their_concepts = set(json.loads(candidate.concepts_json or "[]"))
    overlap = len(my_concepts & their_concepts)  # ❌ Binary: just count
    elo_closeness = 1.0 - abs(entry.elo_rating - candidate.elo_rating) / ELO_MAX_DIFF
    score = overlap * 2 + elo_closeness  # ❌ Crude weighting
    if score > best_score:
        best = candidate
        best_score = score
```

**Problem:**
- Treats all shared concepts equally
- Concept with both at 90% mastery = concept with both at 10%
- Doesn't penalize theta mismatches
- If player A is +2.0 and player B is -1.0 in shared concept → unfair

### ✅ AFTER
```python
async def calculate_concept_affinity(
    db: AsyncSession,
    user1_id: uuid.UUID,
    user2_id: uuid.UUID,
    shared_concept_ids: Set[uuid.UUID],
) -> float:
    """Affinity weighted by mastery alignment."""
    if not shared_concept_ids:
        return 0.0
    
    total_affinity = 0.0
    for concept_id in shared_concept_ids:
        # Get both players' IRT theta for this concept
        theta1 = float(await db.scalar(
            select(UserConceptTheta.theta).where(
                UserConceptTheta.user_id == user1_id,
                UserConceptTheta.concept_id == concept_id,
            )
        ) or 0.0)
        
        theta2 = float(await db.scalar(
            select(UserConceptTheta.theta).where(
                UserConceptTheta.user_id == user2_id,
                UserConceptTheta.concept_id == concept_id,
            )
        ) or 0.0)
        
        # ✓ Penalize large gaps: affinity = 1/(1+|theta1-theta2|)
        # theta_diff=0 → affinity=1.0 (perfect match)
        # theta_diff=1 → affinity=0.5 (acceptable)
        # theta_diff=3 → affinity=0.25 (poor match)
        theta_diff = abs(theta1 - theta2)
        affinity = 1.0 / (1.0 + theta_diff)
        total_affinity += affinity
    
    return total_affinity / len(shared_concept_ids)

# Usage:
affinity_score = await calculate_concept_affinity(
    db, entry.user_id, candidate.user_id, shared_concepts
)
```

**Benefits:**
- ✓ Rewards skill-matched pairs
- ✓ Penalizes theta mismatches
- ✓ Nuanced scoring (0-1 range, not binary)
- ✓ Fairer matchups

**Example Output:**
```
Shared concept "Algebra":
- Both at θ=+1.5 → affinity=1.0 (perfect)
- One at +2.0, one at +1.5 → affinity=0.67 (good)
- One at +2.0, one at -1.0 → affinity=0.40 (poor match, penalized!)
```

---

## 3️⃣ QUESTION SELECTION (Big Change)

### ❌ BEFORE (pvp_service.py line ~185)
```python
# Fetch 5 random questions for the topic
topic_filter = topic.lower()
stmt = (
    select(QuestionBank)
    .where(QuestionBank.topic.ilike(f"%{topic_filter}%"))
    .order_by(func.random())
    .limit(50)  # ❌ No filtering!
)

result = await db.execute(stmt)
candidates = result.scalars().all()
questions = candidates[:5]  # ❌ Random, no quality checks

# Build JSON and create match
```

**Problems:**
- ❌ No concept filtering (could ask about unknown topics)
- ❌ No difficulty matching (mismatch in skill level)
- ❌ No reaper filtering (questions already answered)
- ❌ Hardcoded 5 questions
- ❌ Completely random selection

### ✅ AFTER
```python
async def select_pvp_questions_improved(
    db: AsyncSession,
    user1_id: uuid.UUID,
    user2_id: uuid.UUID,
    topic: str,
    count: int = 5,
) -> List[QuestionBank]:
    """
    Select questions with THREE criteria:
    1. From concepts BOTH players have mastered
    2. With difficulty matching their average IRT ability
    3. NOT previously answered (avoid reaper)
    """
    
    # Step 1: Find shared mastered concepts
    concepts_user1 = await get_user_mastered_concepts(db, user1_id)
    concepts_user2 = await get_user_mastered_concepts(db, user2_id)
    shared_concepts = set(concepts_user1) & set(concepts_user2)
    
    # Step 2: Get reaper-avoided question IDs ✓ NEW
    answered_user1 = await get_user_answered_questions(db, user1_id)
    answered_user2 = await get_user_answered_questions(db, user2_id)
    all_answered = answered_user1 | answered_user2
    
    # Step 3: Select questions from shared concepts with matching difficulty
    selected_questions = []
    
    for concept_id in shared_concepts:
        if len(selected_questions) >= count:
            break
        
        # Get both players' IRT theta for this concept
        theta1 = float(await db.scalar(
            select(UserConceptTheta.theta).where(...)
        ) or 0.0)
        theta2 = float(await db.scalar(
            select(UserConceptTheta.theta).where(...)
        ) or 0.0)
        
        # Use average theta to find target difficulty
        avg_theta = (theta1 + theta2) / 2.0
        target_difficulty = beta_to_difficulty(avg_theta)  # IRT → 1-5
        
        # Select questions:
        # - From this concept ✓
        # - With difficulty ±1 of target ✓
        # - NOT previously answered ✓
        candidates = await db.execute(
            select(QuestionBank)
            .join(QuestionConcept, ...)
            .where(
                QuestionConcept.concept_id == concept_id,
                QuestionBank.topic.ilike(f"%{topic}%"),
                QuestionBank.id.notin_(all_answered),  # ✓ Reaper filter!
                QuestionBank.difficulty.between(  # ✓ Difficulty match!
                    target_difficulty - 1,
                    target_difficulty + 1
                ),
            )
            .order_by(func.random())
            .limit(10)
        )
        
        candidates_list = candidates.scalars().all()
        if candidates_list:
            selected = random.choice(candidates_list)
            selected_questions.append(selected)
    
    # Step 4: Fallback if not enough shared-concept questions
    if len(selected_questions) < count:
        needed = count - len(selected_questions)
        fallback = await db.execute(
            select(QuestionBank)
            .where(
                QuestionBank.topic.ilike(f"%{topic}%"),
                QuestionBank.id.notin_(all_answered),  # ✓ Still avoid reaper!
                QuestionBank.id.notin_([q.id for q in selected_questions]),
            )
            .order_by(func.random())
            .limit(needed)
        )
        selected_questions.extend(fallback.scalars().all())
    
    return selected_questions[:count]
```

**Benefits:**
- ✓ Only asks about SHARED learned concepts
- ✓ Difficulty matches player IRT ability
- ✓ Avoids "reaper" questions completely
- ✓ Fair, balanced matches
- ✓ Mostly NEW questions

**Example:**
```
Player A mastered: Algebra(θ=+1.5), Geometry(θ=+0.5)
Player B mastered: Algebra(θ=+1.8), Calculus(θ=+0.2)
Shared: Algebra only

Match gets 5 questions from Algebra:
- Difficulty level ≈ (1.5+1.8)/2 = 1.65 → difficulty 2
- None from Geometry (only A knows)
- None from Calculus (only B knows)
- None previously answered (reaper filtered)
→ Fair, challenging, fresh questions!
```

---

## 4️⃣ ELO MATCHING RANGE

### ❌ BEFORE
```python
ELO_MAX_DIFF = 300  # ±300 is TOO LOOSE

query = select(PvPMatchmakingQueue).where(
    PvPMatchmakingQueue.elo_rating >= entry.elo_rating - 300,
    PvPMatchmakingQueue.elo_rating <= entry.elo_rating + 300,
)
```

**Problem:**
- Can match 1000 Elo vs 1300 Elo
- In chess, ±200 is considered balanced
- ±300 is almost random

### ✅ AFTER
```python
ELO_MAX_DIFF = 150  # ✓ Reduced to 150

query = select(PvPMatchmakingQueue).where(
    PvPMatchmakingQueue.elo_rating >= entry.elo_rating - ELO_MAX_DIFF,
    PvPMatchmakingQueue.elo_rating <= entry.elo_rating + ELO_MAX_DIFF,
)

# Hard reject if too far
if elo_diff > ELO_MAX_DIFF:
    return 0.0  # Don't match this candidate
```

**Benefits:**
- ✓ Tighter skill matching
- ✓ More balanced opponents
- ✓ Better Elo prediction

---

## 5️⃣ COMPOSITE SCORING

### ❌ BEFORE
```python
score = overlap * 2 + elo_closeness
```

**Problems:**
- Overlap could be 0-15 (huge range)
- Elo closeness 0-1 (small range)
- Unbalanced weighting
- No concept for recency

### ✅ AFTER
```python
async def score_candidate(db, entry, candidate):
    # Factor 1: Elo (40%)
    elo_diff = abs(float(entry.elo_rating) - float(candidate.elo_rating))
    if elo_diff > ELO_MAX_DIFF:
        return 0.0  # Hard reject
    elo_score = 1.0 - (elo_diff / float(ELO_MAX_DIFF))
    
    # Factor 2: Concept Affinity (50%) ✓ MOST IMPORTANT
    affinity_score = await calculate_concept_affinity(db, ...)
    
    # Factor 3: Recency (10%)
    time_diff = now - candidate.joined_at
    recency_score = 1.0 if time_diff < 600 else 0.3
    
    # Composite: prioritize affinity > elo > recency
    final_score = (
        affinity_score * 0.50 +
        elo_score * 0.40 +
        recency_score * 0.10
    )
    
    return final_score  # 0.0-1.0
```

**Benefits:**
- ✓ Balanced weighting (all 0-1 range)
- ✓ Prioritizes affinity (most important)
- ✓ Threshold-based (score < 0.1 = reject)
- ✓ Includes recency preference

**Weighting:**
```
50% Concept Affinity  (shared learned concepts)
40% Elo Closeness     (skill level match)
10% Recency           (prefer active players)
```

---

## 6️⃣ "REAPER" QUESTION FILTERING

### ❌ BEFORE
```python
# No filtering at all - same questions can repeat across rooms!

# In Classic room:
Player A answers Q1, Q2, Q3

# In PvP match later:
Q1, Q2 appear again!
→ Player A has huge advantage (seen before, memory, practice)
→ Opponent B is at disadvantage (first time)
→ UNFAIR!
```

### ✅ AFTER
```python
async def get_user_answered_questions(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> Set[uuid.UUID]:
    """Get ALL questions user has answered (all rooms)."""
    result = await db.execute(
        select(UserResponse.question_id)
        .where(UserResponse.user_id == user_id)
    )
    return set(row[0] for row in result.fetchall())

# In question selection:
answered_user1 = await get_user_answered_questions(db, user1_id)
answered_user2 = await get_user_answered_questions(db, user2_id)
all_answered = answered_user1 | answered_user2

# Filter out reaper questions:
candidates = await db.execute(
    select(QuestionBank)
    .where(
        QuestionBank.id.notin_(all_answered),  # ✓ Only NEW questions!
        ...
    )
)
```

**Benefits:**
- ✓ Reaper questions blocked
- ✓ Both players get fresh questions
- ✓ Fair competitive advantage
- ✓ No fatigue from repetition

---

## 📊 SUMMARY TABLE

| Aspect | Before | After | Impact |
|--------|--------|-------|--------|
| **Concept Selection** | response_count | mastery_level + theta | HIGH |
| **Affinity Scoring** | Binary (0-15) | Weighted (0-1) | HIGH |
| **Question Difficulty** | Random | IRT matched | HIGH |
| **Reaper Filtering** | None (0%) | All checked | HIGH |
| **Elo Range** | ±300 | ±150 | MEDIUM |
| **Composite Scoring** | overlap*2 + elo | 50% affinity + 40% elo + 10% recency | MEDIUM |

---

## 🚀 IMPLEMENTATION EFFORT

| Phase | Change | Time |
|-------|--------|------|
| 1 | Concept mastery selection | 1h |
| 2 | Affinity scoring | 1.5h |
| 3 | Question selection + reaper | 2h |
| 4 | Composite scoring | 1h |
| 5 | Testing + deployment | 1.5h |
| **Total** | | **7 hours** |

---

## 🧪 TESTING CHECKLIST

- [ ] Test `get_user_mastered_concepts()` returns only INTERMEDIATE+
- [ ] Test `calculate_concept_affinity()` with different theta combinations
- [ ] Test reaper filtering removes answered questions
- [ ] Test difficulty matching matches avg_theta correctly
- [ ] Test composite scoring weights properly
- [ ] E2E: Run 2 matches, verify fairness metrics
- [ ] E2E: Verify same questions don't repeat across rooms
- [ ] Monitor Elo changes (should be tighter)

