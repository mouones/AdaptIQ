# AdaptIQ — Learning Engine Design

> Part 4 of the design dossier, and the core of the project. This explains the
> algorithms behind adaptation, scoring, and progression — and *why* each was
> chosen. Code references: `backend/database/irt.py`,
> `backend/services/concept_irt.py`, `backend/services/classic_service.py`,
> `backend/services/pvp_service.py`, `backend/config.py`.

## 1. The measurement model — Item Response Theory (1PL)

### 1.1 Why IRT (and not "streak counters")
A naïve adaptive quiz nudges difficulty up on a correct answer and down on a wrong
one. That is noisy (one lucky guess swings it) and has no notion of *how much*
information an answer carries. **Item Response Theory** models the probability that
a learner answers an item correctly as a function of two numbers on the same
scale:

- **θ (theta)** — the learner's **ability**.
- **β (beta)** — the item's **difficulty**.

AdaptIQ uses the **1-parameter logistic (1PL / Rasch)** model:

```
P(correct | θ, β) = 1 / (1 + e^-(θ − β))
```

- When θ = β, P = 0.5 (the item is maximally *informative* — it tells us the most
  about the learner).
- θ and β live on a shared logit scale, clamped to ±3 (`THETA_RANGE`,
  `BETA_RANGE` in `irt.py`).

**Why 1PL and not 2PL/3PL?** The 2-parameter model adds per-item *discrimination*
and the 3-parameter model adds *guessing*. Both are more expressive but need far
more response data per item to estimate stably — impractical for an online system
that also *generates* new items constantly. 1PL gives a robust, cheap, online-updatable
estimate, which is the right trade-off here (recorded in
[02 — Requirements §4](./02-requirements.md)).

### 1.2 The online update
After each answer, θ moves by a gradient step toward what was observed
(`update_theta` / `ConceptIRT.compute_update`):

```
p          = P(correct | θ, β)
θ_new      = clamp( θ + LEARN_RATE · (correct − p) , −3, +3 )   # LEARN_RATE = 0.3
```

Intuition: if the learner got a hard item right (`correct=1`, `p` small), the
surprise `(correct − p)` is large and θ jumps up; an expected outcome barely moves
θ. A parallel **variance** term (`theta_variance`, decayed ×0.95 per response)
tracks *confidence* in the estimate — it starts wide and narrows as evidence
accumulates, and a learner is considered "confident/known" after ≥5 responses on a
concept.

### 1.3 Per-**concept** ability (the important design choice)
Ability is **not** a single global number. A learner can be strong on World-War-II
dates and weak on African capitals. AdaptIQ therefore stores θ **per (user,
concept)** in `user_concept_theta`, and updates only the concepts attached to the
answered question. This is what makes the personalisation meaningful — the system
builds a *mastery profile*, not one blunt skill number.

- `Concept.topic` = broad family, `Concept.scope` = narrower context,
  `Concept.name` = the specific concept; questions link to concepts via
  `question_concepts`.
- A θ is bucketed into a human-readable **mastery level**
  (BEGINNER → NOVICE → INTERMEDIATE → ADVANCED → EXPERT) for display.

## 2. Choosing the next question — the Zone of Proximal Development

Learning is fastest where success is likely but not guaranteed. AdaptIQ targets a
**ZPD band** of P(correct) ∈ **[0.60, 0.75]**. Inverting the 1PL equation gives the
β range that produces that success band for a given ability:

```
β = θ + ln((1 − P) / P)
  → β_high = θ − 0.405   (P = 0.60, the harder end)
  → β_low  = θ − 1.099   (P = 0.75, the easier end)
```

(`target_beta_range` in `irt.py`.) The selector (`classic_service.select_next_question`)
then:

1. Averages the session concepts' θ.
2. **Cold-start:** if the user has < 5 total responses, it widens the band (θ
   unknown → explore broadly) instead of trusting a noisy estimate.
3. Filters the question bank to that difficulty band, **excluding everything the
   user has already seen** (see §4), preferring concept-targeted rows.
4. Falls back progressively (broader band → any topic row → generate) so the room
   never stalls.

### 2.1 A subtlety we document honestly: the scale-unification flag
The stored `question_bank.difficulty_irt` is a **1–5 bucket**, while the ZPD math
is in **logits**. Feeding one where the other is expected mis-targets the band.
This is corrected behind the default-off flag **`ENABLE_IRT_LOGIT_SCALE`**, which
converts consistently (1–5 ⇄ logit β) in both the θ update and the band filter.
It is a flag (not an unconditional change) so the behavioural shift can be
measured before becoming the default — see
[05 — Quality & Performance](./05-quality-and-performance.md).

## 3. Spaced repetition
Getting an item wrong should bring it back later; getting it right should retire
it. AdaptIQ enqueues wrong answers into `user_concept_repeat_queue` with high
probability and correct answers with low probability, each "due" after a number of
future topic exposures (`classic_service`). Due items bypass the global
"seen" filter — the *only* intentional repeat path — reinforcing exactly the
concepts a learner is shaky on.

## 4. Anti-repetition (cross-room)
A question already answered in *any* room should not reappear. The selector unions
the user's `user_responses`, challenge answers, and PvP answers into a "seen" set
and excludes it. Because that union is expensive on the hot path, it is cacheable
behind **`ENABLE_SEEN_SET_CACHE`** (a per-user Redis set) — see
[05](./05-quality-and-performance.md).

## 5. Progression systems (engagement, layered on top of θ)

θ measures *ability*; the following measure *progress and motivation*. They are
deliberately separate so gameplay feels rewarding without corrupting the ability
estimate.

### 5.1 Points & global level
Per answer (`classic_service._compute_points_delta`): a correct answer earns a base
award plus a speed bonus; wrong answers and hint usage apply penalties; the total
is floored at 0. Cumulative points map to a **global level** ladder
(`compute_level`): **Novice → Apprentice → Scholar → Expert → Master** at
0 / 100 / 500 / 1500 / 5000 points.

### 5.2 Challenge ranks
Challenge is a ranked mode with **levels 1–5** and **ranks E → D → C → B → A**
(cumulative `rank_points` thresholds 0/1000/3000/7000/15000). Per-answer points are
a signed table keyed by level (higher levels risk/reward more). In-session level
moves on **streaks** — up after 4 correct, down after 2 wrong — and the current
rank gates which levels are playable. All thresholds are config knobs
(`CHALLENGE_*` in `config.py`).

### 5.3 PvP ELO
1-v-1 duels use the classic **Elo** rating (`pvp_service`):

```
expected = 1 / (1 + 10^((elo_opponent − elo_you) / 400))
Δ        = round( K · (actual − expected) , 1 )      # K = 32 for < 30 matches, else 16
```

Ratings live in `pvp_ratings.elo_rating` (the source of truth) and are finalised
exactly once per match (idempotent). **Matchmaking** scores candidates by a blend
of topic affinity, ELO closeness (±300 window), and recency. A denormalised
`users.elo_global` mirror is kept in sync on match finalize for cheap leaderboard
reads.

## 6. Content trust (why a generated question is safe to serve)
Because questions are LLM-generated, the engine cannot assume they are correct. The
generation path grounds the model in retrieved facts (RAG + a 40/40/20
fact/narrative/pattern blend), then a **validator** enforces structural rules
(length, ≥1 structured fact, narrative-quality threshold) and a **confidence
scorer** must clear an accept threshold before a question enters the served pool.
A **governance** layer adds block-rules, admin approval, and an audit trail. The
guiding principle: *a hallucinated fact is worse than no question* — so the system
prefers to fall back to a verified bank item rather than serve a doubtful one.

## 7. End-to-end: one answer, start to finish
1. Client submits `{session_id, question_id, selected_index, time_taken}`.
2. Server verifies against the stored current question (Redis) under a session
   lock (in-process, or cross-process Redis lock behind `ENABLE_REDIS_SESSION_LOCK`).
3. Grades; updates **θ** for each of the question's concepts; updates
   **mastery level** and **variance**.
4. Applies **points/level**; enqueues **spaced-repetition** if wrong.
5. Records a `user_responses` row (the durable signal).
6. Selects the **next question** in the (possibly updated) ZPD band, excluding
   seen ids, and returns it with feedback.

Every heavyweight step that *can* be pre-computed already was, by the background
worker — which is what keeps this loop fast. That is the subject of
[05 — Quality & Performance](./05-quality-and-performance.md).
