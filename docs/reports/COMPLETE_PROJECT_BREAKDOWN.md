# AdaptIQ Project - Complete Technical Breakdown

**Project Type:** Adaptive Learning Platform (Educational Quiz System)  
**Stack:** FastAPI (Backend) + React/TypeScript (Frontend) + PostgreSQL + Redis  
**Current Date:** May 10, 2026

---

## TABLE OF CONTENTS
1. [High-Level Architecture](#high-level-architecture)
2. [Project Structure](#project-structure)
3. [Database Layer & Models](#database-layer--models)
4. [API Routers & Endpoints](#api-routers--endpoints)
5. [Services Layer (Business Logic)](#services-layer-business-logic)
6. [Configuration & Dependencies](#configuration--dependencies)
7. [Key Application Flows](#key-application-flows)
8. [Important Algorithms & Mechanics](#important-algorithms--mechanics)

---

## HIGH-LEVEL ARCHITECTURE

### System Overview
```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend (React/TS)                     │
│  • Classic Room | Challenge Room | PvP | Custom | Admin        │
└─────────────────────┬───────────────────────────────────────────┘
                      │ HTTP/REST API (with JWT auth)
┌─────────────────────┴───────────────────────────────────────────┐
│                    FastAPI Backend (main.py)                    │
│  ┌─────────────────────────────────────────────────────────────┤
│  │ Routers (7 main routers)                                    │
│  │ • auth.py        → Registration, login, JWT, user stats    │
│  │ • classic_room.py → Adaptive quiz (IRT-based)              │
│  │ • challenge.py   → Ranked challenges with levels           │
│  │ • pvp.py         → Player-vs-Player matchmaking + Elo      │
│  │ • custom.py      → User-generated question rooms           │
│  │ • onboarding.py  → User onboarding flow                    │
│  │ • governance.py  → Content moderation & rule management    │
│  │ • admin.py       → Admin dashboard API                     │
│  └─────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────┤
│  │ Services Layer (Business Logic)                             │
│  │ • classic_service.py      → IRT selection, repeat queues   │
│  │ • challenge_service.py    → Ranked progression logic       │
│  │ • pvp_service.py          → Matchmaking, Elo calculation   │
│  │ • custom_service.py       → Custom room management         │
│  │ • concept_service.py      → Concept discovery & tracking   │
│  │ • llm.py                  → Groq LLM integration (MCQ gen) │
│  │ • session.py              → Session state management       │
│  │ • governance_service.py   → Content validation rules       │
│  └─────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────┤
│  │ Database Access Layer (SQLAlchemy ORM)                      │
│  │ • CRUD operations via models                                │
│  │ • Async session management                                  │
│  └─────────────────────────────────────────────────────────────┘
├─────────────┬──────────────────┬──────────────────┬──────────┤
│  PostgreSQL │     Redis        │   Groq LLM API   │ HTTP Cls │
│  Database   │  (OTP, Sessions) │ (Question Gen)   │ External │
└─────────────┴──────────────────┴──────────────────┴──────────┘
```

### Architecture Patterns
- **Dependency Injection:** FastAPI `Depends()` for auth, DB, Redis
- **Async/Await:** All I/O is async via SQLAlchemy `AsyncSession`
- **Rate Limiting:** slowapi middleware with per-endpoint limits
- **Structured Logging:** structlog for dev/prod-aware logging
- **CORS:** Configurable origin whitelist (localhost:3000, 5173, 9000)

---

## PROJECT STRUCTURE

```
backend/
├── main.py                          # FastAPI app initialization & lifespan
├── config.py                        # Environment config (loaded from .env)
├── dependencies.py                  # Shared rate limiter singleton
│
├── routers/                         # API endpoint handlers
│   ├── auth.py                      # 1. Authentication & user profile (450+ lines)
│   ├── classic_room.py              # 2. Adaptive training quiz
│   ├── challenge.py                 # 3. Ranked challenge mode
│   ├── pvp.py                       # 4. Player vs player matchmaking
│   ├── custom.py                    # 5. Custom question rooms
│   ├── onboarding.py                # 6. User onboarding
│   ├── governance.py                # 7. Content moderation rules
│   └── admin.py                     # 8. Admin dashboard (not a router)
│
├── services/                        # Business logic layer
│   ├── classic_service.py           # IRT selection, repeat queues
│   ├── challenge_service.py         # Challenge progression
│   ├── pvp_service.py               # Matchmaking & Elo ratings
│   ├── custom_service.py            # Custom room logic
│   ├── concept_service.py           # Concept discovery
│   ├── concept_irt.py               # Per-concept IRT calibration
│   ├── onboarding_service.py        # Onboarding state
│   ├── governance_service.py        # Content moderation
│   ├── llm.py                       # Groq LLM client (MCQ generation)
│   ├── session.py                   # Session state management
│   ├── monitoring.py                # Metrics & health checks
│   └── security/
│       └── bcrypt_utils.py          # Password hashing helpers
│
├── database/                        # SQLAlchemy ORM models
│   ├── models.py                    # Base: User, UserResponse, QuestionBank
│   ├── challenge_models.py          # Challenge room models
│   ├── concept_models.py            # Concept & IRT tracking models
│   ├── pvp_models.py                # PvP matchmaking & rating models
│   ├── custom_models.py             # Custom room models
│   ├── onboarding_models.py         # Onboarding state models
│   ├── governance_models.py         # Governance/moderation models
│   ├── crud.py                      # CRUD helpers
│   ├── irt.py                       # IRT math utilities
│   └── __init__.py
│
├── schemas/                         # Pydantic request/response models
│   ├── auth.py
│   ├── classic.py
│   ├── challenge.py
│   ├── pvp.py
│   ├── custom.py
│   ├── onboarding.py
│   ├── governance.py
│   ├── types.py
│   └── __init__.py
│
├── rag/                             # Retrieval-Augmented Generation
│   ├── agentic.py                   # Agentic RAG pipeline
│   ├── hf_dataset.py                # Hugging Face dataset loading
│   ├── wikipedia.py                 # Wikipedia retrieval
│   ├── wikidata.py                  # Wikidata structured facts
│   └── __init__.py
│
├── seeds/                           # Database seeding
│   └── seed.py                      # Initial data population
│
├── scripts/                         # Utility & admin scripts
│   ├── admin_diag_postman.py
│   ├── live_room_harvest.py
│   ├── cleanup_stale_data.py
│   ├── generate_real_test_user_history.py
│   ├── setup_test_users.py
│   └── [other utilities]
│
├── tests/                           # pytest test suite (40+ test files)
│   ├── test_auth_*.py
│   ├── test_classic_*.py
│   ├── test_challenge*.py
│   ├── test_pvp_*.py
│   ├── test_custom_*.py
│   ├── test_governance.py
│   ├── e2e_test.py
│   └── [integration tests]
│
├── alembic/                         # Database migrations
│   ├── env.py
│   └── versions/                    # Migration files (numbered)
│
├── logs/                            # Runtime logs (gitignored)
│   ├── backend.log
│   ├── backend-error.log
│   └── root_artifacts/
│
├── generated/                       # Output artifacts
│   ├── test_users.csv
│   ├── test_users.json
│   └── reports/
│
├── requirements.txt                 # Python dependencies
├── Dockerfile                       # Container setup
├── docker-compose.yml               # Multi-service orchestration
├── config.py                        # Application configuration
├── alembic.ini                      # Migration config
├── pytest.ini                       # Test config
└── README.md                        # Project docs

frontend/
├── src/
│   ├── pages/                       # Page components (Classic, Challenge, PvP, etc)
│   ├── services/                    # API clients & business logic
│   ├── components/                  # Reusable React components
│   └── App.tsx
├── package.json
├── vite.config.ts
├── tsconfig.json
└── [other React config]
```

---

## DATABASE LAYER & MODELS

### Core Models (database/models.py)

#### **User** [Primary Key: UUID]
```
- id (UUID, PK)
- email (String, unique, indexed)
- username (String, unique)
- password_hash (String, bcrypt)
- points (Integer, default=0)
- level (String, default="Novice")
- elo_global (Float, default=0.0)
- created_at (DateTime, UTC naive)
- last_login (DateTime, nullable)
- is_active (Boolean, default=True)
- is_admin (Boolean, default=False)
```

#### **UserResponse** [Tracks every quiz answer]
```
- id (UUID, PK)
- user_id (UUID, indexed)
- session_id (UUID, indexed)
- question_id (UUID)
- topic (String, indexed with user_id)
- difficulty_sent (Integer)
- answered_correct (Boolean)
- time_taken (Integer, seconds)
- used_hint (Boolean)
- created_at (DateTime, indexed)
```

#### **QuestionBank** [Cached questions with IRT params]
```
- id (UUID, PK)
- question_text (Text)
- correct_answer (Text)
- options_json (Text, JSON array)
- explanation (Text)
- topic (String, indexed)
- difficulty_irt (Float, IRT difficulty parameter)
- discrimination (Float, IRT discrimination)
- usage_count (Integer)
- times_seen (Integer)
- last_served_at (DateTime, nullable)
- source (String, "llm" | "custom" | "challenge_llm")
- primary_concept_id (UUID, nullable, FK→Concept)
- gov_approved (Boolean, governance flag)
- gov_safe (Boolean, governance flag)
- gov_confidence (Float, 0-1, trust score)
- gov_fact_trust (Float, 0-1)
- gov_narrative_quality (Float, 0-1)
- gov_sources_json (Text, JSON sources)
- gov_flags_json (Text, governance alerts)
- gov_checked_at (DateTime, nullable)
```

### Concept Models (database/concept_models.py)

#### **Concept** [Knowledge graph nodes]
```
- id (UUID, PK)
- name (String, unique, indexed)
- topic (String, indexed)
- description (Text, nullable)
- created_at (DateTime)
```

#### **QuestionConcept** [Many-to-many: Questions ↔ Concepts]
```
- id (UUID, PK)
- question_id (UUID, FK→QuestionBank, CASCADE, indexed)
- concept_id (UUID, FK→Concept, CASCADE, indexed)
- is_primary (Boolean, true if main concept)
- created_at (DateTime)
- Unique constraint: (question_id, concept_id)
```

#### **UserConceptTheta** [IRT parameters per user-concept]
```
- id (UUID, PK)
- user_id (UUID, FK→User, CASCADE, indexed)
- concept_id (UUID, FK→Concept, CASCADE, indexed)
- theta (Float, ability estimate, starts at 0)
- theta_variance (Float, uncertainty, starts at 1.0)
- response_count (Integer, # of responses)
- exposure_count (Integer, # of times shown)
- mastery_level (String, "BEGINNER"|"INTERMEDIATE"|"ADVANCED")
- first_seen_at (DateTime, nullable)
- last_played_at (DateTime, nullable)
- last_updated (DateTime)
- created_at (DateTime)
- Unique constraint: (user_id, concept_id)
```

#### **UserConceptRepeatQueue** [Spaced repetition tracking]
```
- id (UUID, PK)
- user_id (UUID, FK→User, CASCADE, indexed)
- concept_id (UUID, FK→Concept, CASCADE, indexed)
- question_id (UUID, FK→QuestionBank, CASCADE)
- repeat_probability (Float, 0-1)
- due_after_session (Integer, # sessions before next repeat)
- created_at (DateTime)
```

#### **ClassicSession** [Per-session tracking]
```
- id (UUID, PK)
- user_id (UUID, FK→User, CASCADE, indexed)
- topic (String)
- questions_answered (Integer)
- correct_count (Integer)
- concepts (JSON, list of concept IDs selected)
- created_at (DateTime)
- ended_at (DateTime, nullable)
```

### Challenge Models (database/challenge_models.py)

#### **ChallengeSession** [Ranked challenge progression]
```
- id (UUID, PK)
- user_id (UUID, FK→User)
- topic (String)
- starting_level (Integer, 1-5)
- current_level (Integer, 1-5)
- rank_points (Integer, accumulated)
- streak_correct (Integer)
- streak_wrong (Integer)
- total_questions (Integer)
- correct_answers (Integer)
- started_at (DateTime)
- ended_at (DateTime, nullable)
- is_completed (Boolean)
```

#### **ChallengeAnswer** [Per-question tracking in challenge]
```
- id (UUID, PK)
- session_id (UUID, FK→ChallengeSession)
- question_id (UUID)
- chosen_answer (Text)
- is_correct (Boolean)
- points_change (Integer, +/- delta)
- level_at_answer (Integer)
- time_taken (Float, seconds)
- created_at (DateTime)
- Unique constraint: (session_id, question_id)
```

#### **ChallengeRanking** [User's challenge rank & stats]
```
- user_id (UUID, PK, FK→User)
- current_rank (String, "E"-"S" grade)
- rank_points (Integer)
- total_sessions (Integer)
- total_questions (Integer)
- highest_streak (Integer)
- updated_at (DateTime)
```

### PvP Models (database/pvp_models.py)

#### **PvPMatchmakingQueue** [Waiting players]
```
- id (UUID, PK)
- user_id (UUID, FK→User, indexed)
- topic (String)
- elo_rating (Float, 1000.0 default)
- concepts_json (Text, JSON array of concept IDs)
- joined_at (DateTime)
- status (String, "waiting"|"matched"|"expired")
```

#### **PvPMatch** [Active/completed 1v1 match]
```
- id (UUID, PK)
- user1_id (UUID, FK→User, indexed)
- user2_id (UUID, FK→User, indexed)
- topic (String)
- status (String, "active"|"completed"|"cancelled")
- total_questions (Integer, default=5)
- questions_json (Text, JSON array of question data)
- user1_score (Integer)
- user2_score (Integer)
- user1_finished (Boolean)
- user2_finished (Boolean)
- winner_id (UUID, nullable)
- elo_change (Float, signed delta for user1)
- started_at (DateTime)
- ended_at (DateTime, nullable)
- created_at (DateTime)
```

#### **PvPMatchAnswer** [Per-question answer in PvP]
```
- id (UUID, PK)
- match_id (UUID, FK→PvPMatch, indexed)
- user_id (UUID, FK→User)
- question_id (UUID)
- question_index (Integer, 0-based)
- chosen_answer (String)
- is_correct (Boolean)
- time_taken (Float, seconds)
- answered_at (DateTime)
- Unique constraint: (match_id, user_id, question_index)
```

#### **PvPRating** [User's Elo rating & match history]
```
- id (UUID, PK)
- user_id (UUID, FK→User, unique, indexed)
- elo_rating (Float, 1000.0 default)
- total_matches (Integer)
- total_wins (Integer)
- total_losses (Integer)
- total_draws (Integer)
- win_streak (Integer)
- best_streak (Integer)
- updated_at (DateTime)
```

### Custom Room Models (database/custom_models.py)
*Similar structure to Classic, allowing user-created question sets*

### Onboarding Models (database/onboarding_models.py)
*Tracks user setup: survey completion, tour viewing, preferences*

### Governance Models (database/governance_models.py)
*Rules, blocked patterns, content approval workflows*

---

## API ROUTERS & ENDPOINTS

### 1. Authentication Router (routers/auth.py)

**Prefix:** `/api/auth`  
**Rate Limits:** Vary per endpoint (10-120/minute)

| HTTP | Endpoint | Function | Purpose |
|------|----------|----------|---------|
| POST | `/signup` | `signup()` [L432] | Register new user, return JWT + profile |
| POST | `/login` | `login()` [L493] | Authenticate with email+password, return JWT |
| GET | `/me` | `me()` [L529] | Get authenticated user + token issue time |
| GET | `/profile` | `profile()` [L542] | Get user fields only (no wrapper) |
| GET | `/stats` | `stats()` [L555] | Dashboard stats (points, accuracy, streaks, room progress) |
| GET | `/stats/daily-trend` | `stats_daily_trend()` [L724] | Activity trend (7-90 days) for charts |
| POST | `/forgot-password` | `forgot_password()` [L780] | Request OTP for password reset |
| POST | `/reset-password` | `reset_password()` [L816] | Verify OTP + set new password |
| POST | `/bootstrap-admin` | `bootstrap_admin()` [L857] | Promote user to admin (dev only, requires secret key) |

**Key Helpers (Internal):**
- `_hash_password()` - bcrypt hashing (12 rounds)
- `_verify_password()` - bcrypt comparison
- `_create_access_token()` - JWT creation (30 min expiry)
- `_build_user_out()` - SQLAlchemy → Pydantic DTO
- `get_db()` [L255] - FastAPI dependency for AsyncSession
- `get_redis()` [L267] - FastAPI dependency for Redis client
- `get_current_user()` [L273] - Bearer token validation, returns (User, issued_at)
- OTP helpers: `_save_otp()`, `_read_otp()`, `_bump_otp_attempts()`, `_delete_otp()`

**Pydantic Models (Defined in auth.py):**
- `SignupRequest`, `LoginRequest`, `ForgotPasswordRequest`, `ResetPasswordRequest`
- `AuthUserOut`, `AuthResponse`, `MeOut`
- `UserStatsOut`, `RoomProgressOut`, `RoomLocksOut`
- `DailyTrendOut`, `DailyTrendPointOut`

---

### 2. Classic Room Router (routers/classic_room.py)

**Prefix:** `/api/rooms/classic`  
**Purpose:** Adaptive quiz using Item Response Theory (IRT)

| HTTP | Endpoint | Function | Purpose |
|------|----------|----------|---------|
| POST | `/questions` | `generate_question()` [L244] | Start/continue session, get next adaptive question |
| POST | `/hints` | `generate_hint()` [L496] | Get LLM-generated study hint (anti-answer-leak) |
| POST | `/answers` | `submit_answer()` [L557] | Submit answer, get feedback + next question |

**Key Helpers:**
- `get_session_svc()` [L64] - FastAPI dependency for SessionService
- `_generate_classic_question_from_llm()` [L96] - LLM fallback generation
- Topic normalization, shuffling logic, IRT ZPD targeting

**Pydantic Models:**
- `QuestionRequest`, `QuestionResponse`
- `HintRequest`, `HintResponse`
- `SubmitAnswerRequest`, `SubmitAnswerResponse`

---

### 3. Challenge Router (routers/challenge.py)

**Prefix:** `/api/challenge`  
**Purpose:** Ranked challenge mode with level progression

| HTTP | Endpoint | Function | Purpose |
|------|----------|----------|---------|
| GET | `/user/{user_id}/rank` | ? | Get user's challenge rank ("E"-"S") |
| POST | `/start-session` | ? | Create new challenge session, pick topic + level |
| GET | `/session/{session_id}` | ? | Get current session state |
| POST | `/change-level` | ? | Move to next/prev difficulty level |
| POST | `/generate-question` | ? | Get next question for session |
| POST | `/submit-answer` | ? | Submit answer, update rank points |
| POST | `/end-session` | ? | Finalize session, lock in rank changes |

---

### 4. PvP Router (routers/pvp.py)

**Prefix:** `/api/pvp`  
**Purpose:** Player-vs-Player matchmaking with Elo ranking

| HTTP | Endpoint | Function | Purpose |
|------|----------|----------|---------|
| POST | `/join-queue` | `join_queue_endpoint()` [L67] | Add player to matchmaking queue |
| DELETE | `/leave-queue` | `leave_queue_endpoint()` [L93] | Remove player from queue |
| GET | `/queue-status` | `queue_status_endpoint()` [L112] | Check if matched + waiting time |
| GET | `/match/{match_id}` | `get_match_endpoint()` [L137] | Get current match state (questions, scores) |
| POST | `/match/{match_id}/answer` | `submit_answer_endpoint()` [L200] | Submit answer to current question |
| POST | `/match/{match_id}/end` | `end_match_endpoint()` [L237] | End match, calculate Elo changes |
| GET | `/user/{user_id}/rating` | `get_rating_endpoint()` [L270] | Get user's Elo + match stats |
| GET | `/leaderboard` | `get_leaderboard_endpoint()` [L293] | Top players by Elo rating |

**Key Algorithms:**
- **Matchmaking:** Elo proximity + concept affinity scoring
- **Elo Calculation:** K=32 (first 30 games), K=16 (after); formula: ΔElo = K*(1 - ExpectedScore)

---

### 5. Custom Room Router (routers/custom.py)

**Purpose:** User-generated question rooms

| HTTP | Endpoint | Purpose |
|------|----------|---------|
| POST | ? | Create custom room |
| GET | ? | List user's custom rooms |
| POST | ? | Add questions to room |
| POST | ? | Start session in custom room |

---

### 6. Onboarding Router (routers/onboarding.py)

**Prefix:** `/api/onboarding`

| HTTP | Endpoint | Function | Purpose |
|------|----------|----------|---------|
| GET | `/{user_id}/status` | `onboarding_status()` [L77] | Get onboarding completion state |
| POST | `/{user_id}/survey` | `submit_onboarding_survey()` [L95] | Submit onboarding survey answers |
| POST | `/{user_id}/skip` | `skip_onboarding_route()` [L119] | Skip onboarding (mark as complete) |
| POST | `/{user_id}/mark-tour-seen` | `mark_tour_seen_route()` [L136] | Mark UI tour as viewed |

---

### 7. Governance Router (routers/governance.py)

**Prefix:** `/api/governance`  
**Purpose:** Content moderation & rule management

| HTTP | Endpoint | Function | Purpose |
|------|----------|----------|---------|
| GET | `/blocked-rules` | `list_blocked_rules()` [L60] | List all content blocking rules |
| POST | `/blocked-rules` | `create_blocked_rule()` [L95] | Create new moderation rule |
| DELETE | `/blocked-rules/{rule_id}` | ? | Remove moderation rule |
| GET | `/audits` | ? | View audit trail |

---

### 8. Admin Router (routers/admin.py)

**Prefix:** `/api/admin`  
**Access:** Requires `is_admin=True` (or localhost for read-only in dev)

| HTTP | Endpoint | Purpose |
|------|----------|---------|
| GET | `/overview` | System-wide stats (user count, total questions, etc.) |
| GET | `/top-concepts` | Most-tracked concepts by student count |
| GET | `/users` | Paginated user list with stats |
| GET | `/users/{id}` | User detail: sessions, mastery, activity |
| PATCH | `/users/{id}` | Toggle user active/admin status |
| GET | `/questions` | Paginated question list (with governance status) |
| GET | `/sessions` | All session types (Classic, Challenge, PvP, Custom) |
| GET | `/monitoring` | Request stats, error logs, system health |

---

## SERVICES LAYER (BUSINESS LOGIC)

### ClassicService (services/classic_service.py)

**Purpose:** Adaptive question selection using IRT + concept targeting

**Key Methods:**

```python
get_user_seen_question_ids()
  → Returns set of question IDs already shown to user
  
select_concept()
  → Choose next concept based on:
    • mastery_gap (distance from target theta)
    • recency (time since last played)
    • repeat_due (queue probability)

select_question_via_irt()
  → Query QuestionBank for questions matching:
    • Target difficulty (ZPD: theta ± 0.5 to 1.5)
    • Topic + concept
    • Not seen by user (governance check)
    • Respects repeat queue

compute_points_delta()
  → Points = 10 (base) + (30 - time_taken) / 3 - 5 (wrong) - 3 (hint)
  
update_theta_after_response()
  → Recalibrate IRT parameters using response outcome
```

**Spaced Repetition Logic:**
- 25% chance wrong answer → added to repeat queue
- 1% chance correct answer → added to repeat queue
- Repeat shown after 7 more sessions with that concept

---

### ChallengeService (services/challenge_service.py)

**Purpose:** Ranked progression with levels (1-5)

**Key Features:**
- Level ↑ on 5 consecutive correct answers
- Level ↓ on 3 consecutive wrong answers
- Rank points accumulate (determine final rank E-S)
- Questions difficulty scales with level

---

### PvPService (services/pvp_service.py)

**Purpose:** Matchmaking, match generation, Elo calculation

**Key Functions:**

```python
get_or_create_rating(db, user_id)
  → Fetch or initialize PvPRating row

join_queue(db, user_id, topic)
  → Add to PvPMatchmakingQueue

find_match(db, user_id)
  → Look for opponent with:
    • Same topic
    • Elo within ±300
    • Shared concept knowledge
    
create_match(db, user1_id, user2_id, topic)
  → Generate 5 shared questions
  → Create PvPMatch row
  
calculate_elo_change(elo1, elo2, outcome1)
  → K = 32 if total_matches < 30 else 16
  → Expected = 1 / (1 + 10^((elo2-elo1)/400))
  → ΔElo = K * (outcome - expected)
  
end_match(db, match_id)
  → Update scores, determine winner
  → Apply Elo changes
  → Update PvPRating row
```

**Elo Constants:**
- `ELO_DEFAULT = 1000.0`
- `ELO_K_NEW = 32` (first 30 matches)
- `ELO_K_REGULAR = 16` (after 30 matches)
- `ELO_MAX_DIFF = 300` (matchmaking range)

---

### ConceptService (services/concept_service.py)

**Purpose:** Discovery & tracking of learning concepts

**Key Methods:**
- Extract concepts from LLM-generated questions
- Update UserConceptTheta (ability estimates)
- Track mastery levels (BEGINNER → INTERMEDIATE → ADVANCED)

---

### LLMClient (services/llm.py)

**Purpose:** Groq Llama 3.1-8B MCQ generation

**Key Features:**
- Difficulty-aware prompts (1-5 scale)
- JSON response parsing with retry logic
- Rate-limit handling (429 + backoff)
- Answer shuffling AFTER generation (prevents first-position bias)

**Prompt Template:**
```
Generate a multiple-choice question about {topic} at difficulty {level}.
Return ONLY valid JSON: {
  "text": "question",
  "correct": "answer",
  "wrong1": "...", "wrong2": "...", "wrong3": "...",
  "explanation": "1-2 sentences why correct"
}
```

---

### SessionService (services/session.py)

**Purpose:** In-memory session state management

**Tracks Per-Session:**
- current_question_index
- asked_question_ids
- user_theta estimate
- topic context

---

### GovernanceService (services/governance_service.py)

**Purpose:** Content validation & moderation

**Features:**
- Blocked rule evaluation
- Fact-checking integration
- Safety scoring (0-1 confidence)
- Audit trail logging

---

## CONFIGURATION & DEPENDENCIES

### config.py (Environment Variables)

```
# DATABASE
DATABASE_URL=postgresql+asyncpg://adaptiq:adaptiq@localhost:5432/adaptiq_db

# REDIS
REDIS_URL=redis://localhost:6379/0

# LLM
GROQ_API_KEY=<groq-api-key>

# APP
ENVIRONMENT=development|production
LOG_LEVEL=INFO|DEBUG
AUTO_CREATE_TABLES=true
CORS_ORIGINS=http://localhost:3000,http://localhost:5173,...

# AUTH
JWT_SECRET_KEY=<min-32-chars>
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
JWT_MIN_SECRET_LENGTH=32

# OTP
OTP_LENGTH=6
OTP_EXPIRE_SECONDS=300
OTP_MAX_ATTEMPTS=3

# SCORING
POINTS_BASE_AWARD=10
POINTS_TIME_BONUS_DIVISOR=3
POINTS_HINT_PENALTY=3
POINTS_WRONG_PENALTY=5

# FEATURE FLAGS
ENABLE_IDEMPOTENCY=true
ENABLE_CONCEPT_TRACKING=true
ENABLE_CONCEPT_DISPLAY=true
ENABLE_TRUSTWORTHY_GENERATION=false
DEV_BYPASS_AUTH=false

# QUIZ
QUIZ_TIME_LIMIT_SECONDS=30
QUIZ_QUESTIONS_PER_SESSION=10
```

### dependencies.py

```python
limiter = Limiter(key_func=get_remote_address)
  → Singleton rate limiter (slowapi)
  
# DB, Redis, HTTP client dependencies defined in auth.py:
get_db(request: Request) → AsyncSession
get_redis(request: Request) → aioredis.Redis | None
get_current_user(auth_header) → (User, issued_at)
```

### main.py Lifespan

```
@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP:
    ✓ Connect to PostgreSQL (create tables if AUTO_CREATE_TABLES=true)
    ✓ Auto-seed if concepts/questions empty
    ✓ Connect to Redis (fallback to in-memory _otp_store)
    ✓ Initialize HTTP client
    ✓ Initialize Groq LLM client (if GROQ_API_KEY set)
    ✓ Load Hugging Face dataset (RAG)
    ✓ Set up logger
    
    # ROUTERS REGISTERED:
    app.include_router(auth_router)
    app.include_router(classic_router)
    app.include_router(challenge_router)
    app.include_router(pvp_router)
    app.include_router(custom_router)
    app.include_router(onboarding_router)
    app.include_router(governance_router)
    app.include_router(admin_router)
    
    # MIDDLEWARE:
    CORSMiddleware(allow_origins=CORS_ORIGINS)
    SlowAPIMiddleware (rate limiting)
    Exception handlers (global)
    
    # SHUTDOWN:
    ✓ Close DB engine
    ✓ Close Redis connection
    ✓ Close HTTP client
```

---

## KEY APPLICATION FLOWS

### Flow 1: User Registration & Login

```
1. POST /api/auth/signup
   ├─ Validate email uniqueness
   ├─ Validate username uniqueness
   ├─ Hash password (bcrypt, 12 rounds)
   ├─ Create User row
   └─ Return JWT + AuthUserOut

2. POST /api/auth/login
   ├─ Query User by email
   ├─ Verify password hash
   ├─ Check is_active
   ├─ Update last_login timestamp
   ├─ Generate JWT (sub=user_id, exp=30min, jti=uuid)
   └─ Return JWT + AuthUserOut

3. GET /api/auth/me (requires auth)
   ├─ Extract JWT from Authorization header
   ├─ Validate signature + expiry
   ├─ Fetch User row
   └─ Return user + issued_at
```

### Flow 2: Classic Room Adaptive Quiz

```
1. POST /api/rooms/classic/questions (no session_id)
   ├─ Create ClassicSession
   ├─ Select concept (weighted by mastery_gap, recency)
   ├─ Select question via IRT ZPD (difficulty = user theta ± 0.5 to 1.5)
   ├─ Shuffle options (hide correct answer)
   └─ Return QuestionResponse {question, options, session_id}

2. POST /api/rooms/classic/hints (requires session_id)
   ├─ Fetch question
   ├─ Generate hint via LLM (anti-answer-leak prompt)
   └─ Return HintResponse

3. POST /api/rooms/classic/answers (with chosen_answer)
   ├─ Verify answer (case-insensitive)
   ├─ Calculate points (base + time bonus - penalties)
   ├─ Create UserResponse row
   ├─ Update UserConceptTheta (IRT recalibration)
   ├─ Check spaced repetition (25% wrong → queue, 1% correct → queue)
   ├─ Increment session counters
   ├─ If session complete: update User.points, update mastery_level
   ├─ Select next question (or null if 10 questions done)
   └─ Return SubmitAnswerResponse {feedback, points_delta, next_question}

4. Auto-repeats → Loop until session_end (or user leaves)
```

### Flow 3: PvP Matchmaking & Match

```
1. POST /api/pvp/join-queue
   ├─ Create/get PvPRating
   ├─ Add to PvPMatchmakingQueue (status="waiting")
   ├─ Attempt immediate match with opponent (Elo ±300, same topic)
   ├─ If match found:
   │   ├─ Generate 5 shared questions
   │   ├─ Create PvPMatch
   │   ├─ Delete both queue entries
   │   └─ Return match_id
   └─ Else: return "waiting" status

2. GET /api/pvp/queue-status
   ├─ Check if matched
   └─ Return {match_id} or {status: "waiting", time_waiting}

3. GET /api/pvp/match/{match_id}
   ├─ Fetch PvPMatch + questions
   ├─ Return current question, scores, timer
   └─ Return PvPMatchOut

4. POST /api/pvp/match/{match_id}/answer
   ├─ Record PvPMatchAnswer
   ├─ Update user's score
   ├─ Check if both players finished all questions
   └─ Return SubmitAnswerResponse

5. POST /api/pvp/match/{match_id}/end (when both done)
   ├─ Determine winner (higher score)
   ├─ Calculate Elo changes:
   │   ├─ Expected = 1 / (1 + 10^((elo2-elo1)/400))
   │   ├─ K = 32 (if total_matches<30) else 16
   │   ├─ ΔElo_user1 = K * (outcome - expected)
   │   └─ ΔElo_user2 = -ΔElo_user1
   ├─ Update PvPRating rows
   ├─ Update User.elo_global
   ├─ Mark PvPMatch as completed
   └─ Return PvPEndMatchResponse {winner_id, elo_changes}

6. GET /api/pvp/leaderboard
   ├─ Order by elo_rating DESC
   ├─ Return top 100 players
   └─ Return LeaderboardResponse
```

### Flow 4: Challenge Room Progression

```
1. POST /api/challenge/start-session
   ├─ Create ChallengeSession (starting_level=1)
   ├─ Initialize streak_correct=0, rank_points=0
   └─ Return session_id

2. POST /api/challenge/generate-question
   ├─ Select question at difficulty = current_level
   └─ Return question

3. POST /api/challenge/submit-answer
   ├─ Create ChallengeAnswer
   ├─ If correct:
   │   ├─ streak_correct += 1
   │   ├─ streak_wrong = 0
   │   └─ If streak_correct ≥ 5: level_up (cap at 5)
   ├─ If incorrect:
   │   ├─ streak_wrong += 1
   │   ├─ streak_correct = 0
   │   └─ If streak_wrong ≥ 3: level_down (min 1)
   ├─ rank_points += points_change
   └─ Return next question

4. POST /api/challenge/end-session
   ├─ Calculate final rank from rank_points
   ├─ Update ChallengeRanking
   ├─ Mark session as completed
   └─ Return EndSessionOut
```

### Flow 5: Dashboard Stats Computation

```
GET /api/auth/stats
├─ Fetch all UserResponse for user
├─ Calculate global_accuracy = correct_count / total_count
├─ Fetch daily responses (today_start to now)
├─ Calculate daily_accuracy, daily_points, learning_time
├─ Calculate streak_days (consecutive days with activity)
├─ Count sessions per room (Classic, Challenge, Custom, PvP)
├─ Calculate room_progress % (pie chart)
├─ Set room_locks (unlock Challenge after Classic)
└─ Return UserStatsOut
```

---

## IMPORTANT ALGORITHMS & MECHANICS

### 1. Item Response Theory (IRT)

**Purpose:** Adaptive difficulty selection based on ability estimate

**Key Concepts:**
- **Theta (θ):** User ability estimate (starts at 0 = median)
- **Beta (β):** Question difficulty (from discrimination curve)
- **Zone of Proximal Development (ZPD):** θ ± 0.5 to 1.5 = optimal challenge

**IRT Implementation (database/irt.py):**
```python
target_beta_range(user_theta: float) → (min_beta, max_beta)
  → Returns difficulty range for ZPD targeting

beta_to_difficulty(beta: float) → int (1-5)
  → Converts IRT parameter to UI difficulty level
```

**Post-Response Recalibration (ConceptIRT):**
```
After answering question:
1. Calculate likelihood of response given user theta & question beta
2. Update theta using Bayesian posterior
3. Update theta_variance (confidence interval)
4. Update mastery_level based on theta:
   - BEGINNER: theta < -0.5
   - INTERMEDIATE: -0.5 ≤ theta < 1.0
   - ADVANCED: theta ≥ 1.0
```

### 2. Elo Rating System

**Formula:**
```
Expected_Score_A = 1 / (1 + 10^((Elo_B - Elo_A) / 400))
New_Elo_A = Old_Elo_A + K * (Actual_Score - Expected_Score)
```

**K-Factor:**
- K = 32 for players with < 30 matches (higher volatility)
- K = 16 for experienced players (lower volatility)

**Implementation:** `pvp_service.py` functions

### 3. Points Calculation (Classic Room)

```python
def compute_points(correct: bool, time_taken: int, used_hint: bool) -> int:
    if correct:
        remaining_secs = max(0, 30 - time_taken)
        delta = 10 + (remaining_secs // 3)  # Base + time bonus
    else:
        delta = -5  # Wrong penalty
    
    if used_hint:
        delta -= 3  # Hint penalty
    
    return delta
```

**User.points** = cumulative sum of all deltas

### 4. Spaced Repetition Queue

**Logic:**
- When user answers question:
  - If **wrong** (25% probability): Add to `UserConceptRepeatQueue` with `repeat_probability=0.25`
  - If **correct** (1% probability): Add to `UserConceptRepeatQueue` with `repeat_probability=0.01`
  - Repeat shown after 7 more sessions with that concept

**Purpose:** Prevent forgotten knowledge decay using scientifically-backed timing

### 5. Governance & Content Moderation

**Features:**
- `gov_approved` flag on QuestionBank
- `gov_safe` flag (content safety)
- `gov_confidence` score (0-1, trust in validation)
- Blocked rules evaluated before serving questions
- Audit trail in governance_audits table

---

## Frontend Architecture (Brief Overview)

**Technology:** React 19 + TypeScript + Vite + Tailwind CSS

**Key Directories:**
```
frontend/src/
├── pages/
│   ├── ClassicRoom.tsx      → Adaptive quiz UI
│   ├── ChallengeRoom.tsx    → Ranked progression
│   ├── PvPRoom.tsx          → Matchmaking & match UI
│   ├── CustomRoom.tsx       → User-created questions
│   ├── Dashboard.tsx        → User stats & trends
│   ├── AdminDashboard.tsx   → Admin overview
│   └── Auth/
│       ├── LoginPage.tsx
│       ├── SignupPage.tsx
│       └── ProfilePage.tsx
│
├── services/
│   ├── api.ts               → Axios/fetch wrapper with JWT auth
│   ├── authService.ts       → Login, signup, token storage
│   ├── quizService.ts       → Room API calls
│   └── statsService.ts      → Dashboard data fetching
│
├── components/
│   ├── QuestionCard.tsx
│   ├── ProgressBar.tsx
│   ├── Leaderboard.tsx
│   └── [other reusable components]
│
└── App.tsx                  → Main routing & layout
```

**Key Flows:**
1. **Auth:** LocalStorage JWT token → Authorization header on all requests
2. **Adaptive Quiz:** GET question → User answers → POST answer → GET next question
3. **Dashboard:** GET /api/auth/stats → render charts with Chart.js/Recharts
4. **Admin:** GET /api/admin/* endpoints (with admin guard)

---

## Development & Testing

### Running the Application

```bash
# Backend
cd backend
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Frontend
cd frontend
npm install
npm run dev  # Vite dev server on :5173
```

### Database Setup

```bash
# Using Alembic migrations
alembic upgrade head

# Or auto-create via AUTO_CREATE_TABLES=true
```

### Testing

```bash
# Run pytest suite
pytest tests/ -v

# Run specific test file
pytest tests/test_auth_api.py -v

# With coverage
pytest tests/ --cov=backend --cov-report=html
```

### Key Test Files

- `test_auth_*.py` - Authentication flows (signup, login, JWT)
- `test_classic_*.py` - IRT selection, concept tracking
- `test_challenge*.py` - Level progression, ranking
- `test_pvp_*.py` - Matchmaking, Elo calculation
- `test_custom_*.py` - Custom room creation
- `e2e_test.py` - End-to-end scenarios

---

## Important Notes & Known Limitations

1. **Email Not Sent:** OTP code is generated but never emailed (SMTP integration needed)
   - Dev mode logs OTP to console
   - Integrate with SendGrid/AWS SES in production

2. **DEV_BYPASS_AUTH:** When `true`, skips token validation (dev only)

3. **Concept Discovery:** Currently basic; advanced concept extraction via LLM possible

4. **Rate Limiting:** Per-endpoint limits can be tuned in route decorators

5. **IRT Calibration:** Full 3-parameter model possible; currently using 2-parameter

6. **PvP Matchmaking:** Simple Elo + topic matching; could add concept affinity scoring

---

**End of Complete Technical Breakdown**
**Generated:** May 10, 2026
