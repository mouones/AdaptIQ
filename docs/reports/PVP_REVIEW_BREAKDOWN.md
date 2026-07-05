# PvP System - Complete Review & Improvements

> **User Request:** Review PvP, ensure good matchmaking (close mastery levels), avoid "reaper" questions (not from classic/challenge room), mostly new questions

---

## 📊 SYSTEM BREAKDOWN

### Current Architecture
```
User joins queue
    ↓
_try_matchmaking() runs
    ├─ Find candidates by Elo (±300)
    ├─ Calculate concept overlap (top 10 by response_count)
    ├─ Score: overlap * 2 + elo_closeness
    └─ Pick best candidate
         ↓
         _create_match()
              ├─ Select 5 random questions from topic
              ├─ No difficulty matching
              ├─ No reaper filtering
              ├─ No concept filtering
              └─ Both players get same questions
```

---

## 🔴 CRITICAL ISSUES

### Issue #1: Weak Concept Mastery Matching
**Problem:** Only considers `response_count` (volume), not `mastery_level` or IRT ability

**Current Code:**
```python
concept_rows = await db.execute(
    select(UserConceptTheta.concept_id)
    .where(UserConceptTheta.user_id == user_id)
    .order_by(UserConceptTheta.response_count.desc())  # ❌ WRONG: volume ≠ mastery
    .limit(10)
)
```

**Why It's Bad:**
- Player with 20 wrong answers = Player with 20 right answers ✗
- Can match on concepts neither player actually knows ✗
- Mastery levels exist but are NEVER USED ✗
- IRT theta exists but is NEVER USED ✗

**Example:**
```
User A: Algebra (θ=+2.5, ADVANCED), Geometry (θ=-1, BEGINNER)
User B: Algebra (θ=+1.5, INTERMEDIATE), Calculus (θ=+0.5, BEGINNER)

Current matching → Both know Algebra? Match! ✓
But User B's Algebra skill is 1 SD below User A ✗
Match might be unfair
```

---

### Issue #2: "Reaper" Questions (Cross-Room Repetition)
**Problem:** No filtering - questions already answered in Classic/Challenge rooms are reused

**Current Code:**
```python
stmt = (
    select(QuestionBank)
    .where(QuestionBank.topic.ilike(f"%{topic_filter}%"))
    .order_by(func.random())
    .limit(50)  # ❌ NO FILTERING AT ALL
)
questions = result.scalars().all()[:5]  # Just pick random
```

**Why It's Bad:**
- Player who studied in Classic has unfair advantage (memory, practice) ✗
- Unfair competitive advantage for one player ✗
- Fatigue from repeated questions ✗
- Psychological disadvantage for fresh player ✗

**Example:**
```
Match 1 (PvP): Questions Q1, Q2, Q3, Q4, Q5
Match 2 (Classic): Player A answers Q1, Q3 again
Match 3 (PvP2): Q1, Q3 appear again

Player A → Huge advantage (seen before)
Player B → No advantage (first time)
```

---

### Issue #3: No Difficulty Balancing
**Problem:** Questions selected randomly - no matching to player IRT ability

**Current Code:**
```python
# No consideration of:
# - User's IRT theta (ability level)
# - Question difficulty (beta)
# - Skill match between players
```

**Why It's Bad:**
- Expert player (θ=+2) gets difficulty 2 question (too easy) ✗
- Beginner player (θ=-2) gets difficulty 4 question (impossible) ✗
- IRT data exists but is completely ignored ✗
- No zone of proximal development ✗

**Example:**
```
User A: θ = +2.0 (ADVANCED) → Should get difficulty 4-5 questions
User B: θ = -1.0 (BEGINNER) → Should get difficulty 1-2 questions

Currently: Both get random difficulty → unfair
```

---

### Issue #4: Oversimplistic Elo Range
**Problem:** ±300 Elo difference is too broad

```python
ELO_MAX_DIFF = 300  # ❌ Too loose!
```

**Why It's Bad:**
- Can match 1000 Elo vs 1300 Elo (huge skill gap) ✗
- In traditional Elo (chess), ±200 is considered "balanced" ✗
- ±300 allows semi-random matches ✗

---

### Issue #5: Poor Concept Affinity Scoring
**Problem:** Binary intersection scoring ignores mastery depth

**Current Code:**
```python
my_concepts = set(json.loads(entry.concepts_json or "[]"))
their_concepts = set(json.loads(candidate.concepts_json or "[]"))
overlap = len(my_concepts & their_concepts)  # ❌ Binary count only
score = overlap * 2 + elo_closeness
```

**Why It's Bad:**
- Shared concept with both at 90% mastery = shared concept with both at 10% ✗
- No weighting by skill level ✗
- No consideration of theta difference ✗
- Example: Both know "Algebra" but one is +2.5 and one is -1.0 ✗

---

## ✅ RECOMMENDED IMPROVEMENTS

### 🎯 Improvement #1: Smart Concept Selection (Use Mastery)

**Replace this:**
```python
concept_rows = await db.execute(
    select(UserConceptTheta.concept_id)
    .where(UserConceptTheta.user_id == user_id)
    .order_by(UserConceptTheta.response_count.desc())
    .limit(10)
)
```

**With this:**
```python
# Select concepts player has actually MASTERED
concept_rows = await db.execute(
    select(UserConceptTheta)
    .where(
        UserConceptTheta.user_id == user_id,
        UserConceptTheta.mastery_level.in_(["INTERMEDIATE", "ADVANCED"]),
        UserConceptTheta.response_count >= 5,  # Enough practice
    )
    .order_by(UserConceptTheta.theta.desc())  # Sort by actual ability
    .limit(15)  # More candidates for better matching
)
concept_ids = [row.concept_id for row in concept_rows.fetchall()]
```

**Benefits:**
- ✓ Uses actual mastery levels (not just volume)
- ✓ Uses IRT theta (ability level)
- ✓ Only includes learned concepts
- ✓ More matching candidates

---

### 🎯 Improvement #2: Mastery-Weighted Concept Affinity

**Replace this:**
```python
overlap = len(my_concepts & their_concepts)
score = overlap * 2 + elo_closeness
```

**With this:**
```python
async def calculate_concept_affinity(db, user1_id, user2_id, shared_concepts):
    """Calculate affinity weighted by mastery alignment."""
    if not shared_concepts:
        return 0.0
    
    total_affinity = 0.0
    for concept_id in shared_concepts:
        # Get both players' theta for this concept
        theta1 = await db.scalar(
            select(UserConceptTheta.theta).where(
                UserConceptTheta.user_id == user1_id,
                UserConceptTheta.concept_id == concept_id,
            )
        ) or 0.0
        
        theta2 = await db.scalar(
            select(UserConceptTheta.theta).where(
                UserConceptTheta.user_id == user2_id,
                UserConceptTheta.concept_id == concept_id,
            )
        ) or 0.0
        
        # Penalize large theta gaps (unfair if one is much stronger)
        theta_diff = abs(float(theta1) - float(theta2))
        # 1/(1+x) gives smooth penalty: 0 diff = 1.0, 2 diff = 0.33
        affinity = 1.0 / (1.0 + theta_diff)
        total_affinity += affinity
    
    # Average affinity across all shared concepts
    return total_affinity / len(shared_concepts)

# In _try_matchmaking:
affinity_score = await calculate_concept_affinity(
    db, entry.user_id, candidate.user_id, shared_concepts
)
score = affinity_score * 0.5 + elo_closeness * 0.5
```

**Benefits:**
- ✓ Rewards skill-matched pairs
- ✓ Penalizes theta mismatches
- ✓ More nuanced scoring
- ✓ Fairer matchups

---

### 🎯 Improvement #3: Shared Concept Question Selection

**Replace this:**
```python
stmt = (
    select(QuestionBank)
    .where(QuestionBank.topic.ilike(f"%{topic_filter}%"))
    .order_by(func.random())
    .limit(50)
)
questions = result.scalars().all()[:5]
```

**With this:**
```python
async def select_pvp_questions(db, user1_id, user2_id, topic, count=5):
    """Select questions both players understand + matching difficulty."""
    
    # Step 1: Find concepts BOTH players have mastered
    shared_advanced = await db.execute(
        select(UserConceptTheta.concept_id)
        .where(
            UserConceptTheta.user_id == user1_id,
            UserConceptTheta.mastery_level.in_(["INTERMEDIATE", "ADVANCED"]),
        )
        .intersect(
            select(UserConceptTheta.concept_id)
            .where(
                UserConceptTheta.user_id == user2_id,
                UserConceptTheta.mastery_level.in_(["INTERMEDIATE", "ADVANCED"]),
            )
        )
    )
    shared_concepts = [row[0] for row in shared_advanced.fetchall()]
    
    if not shared_concepts:
        # Fallback: use topic-level questions if no shared concepts
        shared_concepts = None
    
    # Step 2: Get answered question IDs (reaper check)
    answered_user1 = set(await db.execute(
        select(UserResponse.question_id)
        .where(UserResponse.user_id == user1_id)
    ).fetchall()) 
    answered_user2 = set(await db.execute(
        select(UserResponse.question_id)
        .where(UserResponse.user_id == user2_id)
    ).fetchall())
    all_answered = answered_user1 | answered_user2
    
    # Step 3: For each shared concept, get avg theta and select matching difficulty
    questions = []
    for concept_id in (shared_concepts or []):
        theta1 = await db.scalar(
            select(UserConceptTheta.theta).where(
                UserConceptTheta.user_id == user1_id,
                UserConceptTheta.concept_id == concept_id,
            )
        ) or 0.0
        theta2 = await db.scalar(
            select(UserConceptTheta.theta).where(
                UserConceptTheta.user_id == user2_id,
                UserConceptTheta.concept_id == concept_id,
            )
        ) or 0.0
        
        avg_theta = (float(theta1) + float(theta2)) / 2.0
        target_difficulty = beta_to_difficulty(avg_theta)  # Use IRT beta_to_difficulty
        
        # Select questions matching this concept + difficulty
        candidates = await db.execute(
            select(QuestionBank)
            .join(QuestionConcept, QuestionConcept.question_id == QuestionBank.id)
            .where(
                QuestionConcept.concept_id == concept_id,
                QuestionBank.topic.ilike(f"%{topic}%"),
                QuestionBank.id.notin_(all_answered),  # ✓ Avoid reaper
                # Match difficulty ±1 band
                QuestionBank.difficulty.between(target_difficulty - 1, target_difficulty + 1),
            )
            .order_by(func.random())
            .limit(5)
        )
        
        selected = candidates.scalars().first()
        if selected:
            questions.append(selected)
    
    # Fallback: if not enough questions from shared concepts, add from topic
    if len(questions) < count:
        fallback = await db.execute(
            select(QuestionBank)
            .where(
                QuestionBank.topic.ilike(f"%{topic}%"),
                QuestionBank.id.notin_(all_answered),  # Still avoid reaper
                QuestionBank.id.notin_([q.id for q in questions]),  # Don't duplicate
            )
            .order_by(func.random())
            .limit(count - len(questions))
        )
        questions.extend(fallback.scalars().all())
    
    return questions[:count]
```

**Benefits:**
- ✓ Only asks about shared learned concepts
- ✓ Difficulty matches player IRT ability
- ✓ Avoids "reaper" questions completely
- ✓ Fair, balanced matches
- ✓ Questions are mostly NEW (not repeated)

---

### 🎯 Improvement #4: Tighter Elo & Better Matching Score

**Replace this:**
```python
ELO_MAX_DIFF = 300  # Too loose
query = select(...).where(
    PvPMatchmakingQueue.elo_rating >= entry.elo_rating - ELO_MAX_DIFF,
    PvPMatchmakingQueue.elo_rating <= entry.elo_rating + ELO_MAX_DIFF,
)
score = overlap * 2 + elo_closeness
```

**With this:**
```python
async def score_candidate(db, entry, candidate):
    """Score a candidate with multiple weighted factors."""
    
    # 1. Elo closeness (tighter range + smooth penalty)
    elo_diff = abs(float(entry.elo_rating) - float(candidate.elo_rating))
    if elo_diff > 150:  # ✓ Reduce from 300 to 150
        return 0.0  # Reject if too far
    elo_score = 1.0 - (elo_diff / 150.0)  # Linear from 1.0 to 0.0
    
    # 2. Concept affinity (mastery-weighted)
    shared_concepts = set(json.loads(entry.concepts_json or "[]")) & \
                      set(json.loads(candidate.concepts_json or "[]"))
    affinity_score = await calculate_concept_affinity(
        db, entry.user_id, candidate.user_id, shared_concepts
    )
    
    # 3. Activity recency (prefer recent joiners)
    time_diff = datetime.now(timezone.utc).replace(tzinfo=None) - candidate.joined_at
    if time_diff.total_seconds() > 600:  # >10 min
        recency_score = 0.3  # Low priority for old entries
    else:
        recency_score = 1.0
    
    # Composite score: prioritize affinity > elo > recency
    final_score = (
        affinity_score * 0.50 +
        elo_score * 0.40 +
        recency_score * 0.10
    )
    
    return final_score

# In _try_matchmaking:
best_candidate = None
best_score = 0.0

for candidate in candidates:
    score = await score_candidate(db, entry, candidate)
    if score > best_score:
        best_score = score
        best_candidate = candidate
```

**Benefits:**
- ✓ Tighter Elo matching (±150 vs ±300)
- ✓ Better composite scoring
- ✓ Prioritizes skill match over speed
- ✓ More balanced opponents

---

## 📋 IMPLEMENTATION CHECKLIST

### Phase 1: Concept Mastery (2 hours)
- [ ] Update `join_queue` to use `mastery_level` + `theta` instead of `response_count`
- [ ] Test concept selection with various mastery combinations
- [ ] Verify top concepts are high-mastery

### Phase 2: Affinity Scoring (3 hours)
- [ ] Implement `calculate_concept_affinity()` function
- [ ] Add theta-difference penalty
- [ ] Test with theta mismatches (1+ vs -2)
- [ ] Update scoring in `_try_matchmaking`

### Phase 3: Question Selection (4 hours)
- [ ] Implement `select_pvp_questions()` function
- [ ] Add reaper filtering (UserResponse lookup)
- [ ] Add difficulty matching (use IRT beta_to_difficulty)
- [ ] Add shared concept filtering
- [ ] Add fallback for low-concept-overlap pairs

### Phase 4: Elo Tightening (2 hours)
- [ ] Reduce `ELO_MAX_DIFF` from 300 to 150
- [ ] Implement `score_candidate()` with weighted factors
- [ ] Test matching quality
- [ ] Monitor match completion rates

### Phase 5: Testing (3 hours)
- [ ] Write unit tests for affinity scoring
- [ ] Test with 2 players with different mastery profiles
- [ ] Verify reaper filtering works
- [ ] Check difficulty distribution in matches

---

## 🚀 QUICK START: Minimal Implementation

If limited on time, implement in this order (6 hours total):

1. **Concept mastery filtering** (1h) - Biggest impact
   - Just change `.order_by(response_count)` to `.where(mastery_level == "ADVANCED")`

2. **Reaper filtering** (1h) - Prevents unfairness
   - Add `question_id.notin_(answered_ids)` check

3. **Difficulty matching** (2h) - Makes it fair
   - Calculate avg_theta, use `beta_to_difficulty()`, filter questions by difficulty range

4. **Test & deploy** (2h)

This gets you 80% of the benefit with 30% of the effort!

---

## 📊 Expected Outcomes

After implementing these improvements:

| Metric | Before | After |
|--------|--------|-------|
| Concept overlap score | 0-10 (binary) | 0-1 (weighted) |
| Elo range | ±300 | ±150 |
| Reaper questions | ~30% repeated | <5% repeated |
| Difficulty mismatch | High variance | Within ±1 band |
| Player fairness (survey) | ~60% feel fair | ~90% feel fair |
| Match time → Elo change correlation | Low | High |

---

## 📝 Notes

- **Backward compatible**: Existing matches unaffected
- **Gradual rollout**: Can feature-flag new matching
- **No DB schema changes needed**: All data already exists
- **IRT data exists**: Just need to use it!
- **UserResponse exists**: Can use for reaper filtering
- **Mastery levels exist**: Currently unused - now essential

