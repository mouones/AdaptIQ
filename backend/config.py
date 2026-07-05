"""Central runtime settings for the AdaptIQ backend.

Values are loaded from `backend/.env` first, then from the normal process
environment. Keep tunable room sizes, limits, and source taxonomy here so
runtime behavior can change without editing routers.
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv


# Load the backend/.env file relative to this config module so the app works
# when started from the repo root or another working directory.
env_path = Path(__file__).resolve().parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

logger = logging.getLogger(__name__)


def _parse_bool(value: str | None, default: bool = False) -> bool:
    """Parse common truthy/falsy environment variable strings."""
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("true", "1", "yes", "on")


def _parse_int_pair(value: str | None, default: tuple[int, int]) -> tuple[int, int]:
    """Parse `positive:negative` pairs used by Challenge point settings."""
    if not value:
        return default
    try:
        left, right = value.split(":", 1)
        return int(left.strip()), int(right.strip())
    except Exception:
        logger.warning("Invalid integer pair setting %r; using default %s", value, default)
        return default


def _parse_int_tuple(value: str | None, default: tuple[int, ...]) -> tuple[int, ...]:
    """Parse comma-separated integer lists used for difficulty bucket settings."""
    if not value:
        return default
    out: list[int] = []
    try:
        for part in value.split(","):
            item = part.strip()
            if not item:
                continue
            out.append(int(item))
    except Exception:
        logger.warning("Invalid integer tuple setting %r; using default %s", value, default)
        return default
    return tuple(out) or default


# Database
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://adaptiq:adaptiq@localhost:5433/adaptiq_db",
)

# Redis
REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
REDIS_HOST_PORT: str = os.getenv("REDIS_HOST_PORT", "6379")
_default_redis_url = (
    f"redis://:{REDIS_PASSWORD}@localhost:{REDIS_HOST_PORT}/0"
    if REDIS_PASSWORD
    else f"redis://localhost:{REDIS_HOST_PORT}/0"
)
REDIS_URL: str = os.getenv("REDIS_URL", _default_redis_url)

# LLM
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")

# App
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
LOG_DIR: str = os.getenv("LOG_DIR", "logs")
AUTO_CREATE_TABLES: bool = _parse_bool(os.getenv("AUTO_CREATE_TABLES"), default=True)
ENABLE_PUBLIC_DOCS: bool = _parse_bool(
    os.getenv("ENABLE_PUBLIC_DOCS"),
    default=(ENVIRONMENT.lower() != "production"),
)
ENABLE_DETAILED_ERRORS: bool = _parse_bool(
    os.getenv("ENABLE_DETAILED_ERRORS"),
    default=(ENVIRONMENT.lower() == "development"),
)

# CORS
_cors_origins_env = os.getenv("CORS_ORIGINS", "")
CORS_ORIGINS: list[str] = (
    [origin.strip() for origin in _cors_origins_env.split(",") if origin.strip()]
    if _cors_origins_env
    else [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3001",
        "http://localhost:3001",
    ]
)

# Auth / JWT
JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "change-this-dev-secret-change-this-dev-secret")
JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
JWT_MIN_SECRET_LENGTH: int = int(os.getenv("JWT_MIN_SECRET_LENGTH", "32"))

# SMTP
SMTP_HOST: str = os.getenv("SMTP_HOST", "")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL: str = os.getenv("SMTP_FROM_EMAIL", "")
SMTP_FROM_NAME: str = os.getenv("SMTP_FROM_NAME", "AdaptIQ")
SMTP_USE_TLS: bool = _parse_bool(os.getenv("SMTP_USE_TLS"), default=True)

# OTP
OTP_LENGTH: int = int(os.getenv("OTP_LENGTH", "6"))
OTP_EXPIRE_SECONDS: int = int(os.getenv("OTP_EXPIRE_SECONDS", "300"))
OTP_MAX_ATTEMPTS: int = int(os.getenv("OTP_MAX_ATTEMPTS", "3"))

# Reserved for future signed CSRF variants. The active browser flow uses a
# per-login double-submit CSRF token generated in routers/auth.py.
CSRF_SECRET_KEY: str = os.getenv("CSRF_SECRET_KEY", "")

# Feature Flags
ENABLE_IDEMPOTENCY: bool = _parse_bool(os.getenv("ENABLE_IDEMPOTENCY"), default=True)
ENABLE_CONCEPT_TRACKING: bool = _parse_bool(os.getenv("ENABLE_CONCEPT_TRACKING"), default=True)
ENABLE_CONCEPT_DISPLAY: bool = _parse_bool(os.getenv("ENABLE_CONCEPT_DISPLAY"), default=True)
ENABLE_TRUSTWORTHY_GENERATION: bool = _parse_bool(os.getenv("ENABLE_TRUSTWORTHY_GENERATION"), default=False)
DEV_BYPASS_AUTH: bool = _parse_bool(os.getenv("DEV_BYPASS_AUTH"), default=False)
# When enabled, the classic per-concept IRT update and ZPD question selection
# treat difficulty on a single consistent scale: the stored 1-5 difficulty_irt is
# converted to a logit beta before the theta update, and the logit ZPD band is
# converted to 1-5 bucket bounds before filtering the difficulty_irt column.
# Default off so live scoring/selection behavior is unchanged until opted in.
# See docs/reports/QUALITY_PERF_ROADMAP_2026-07-04.md (Item 1).
ENABLE_IRT_LOGIT_SCALE: bool = _parse_bool(os.getenv("ENABLE_IRT_LOGIT_SCALE"), default=False)
# When enabled (and Redis is available), per-session answer locking uses a Redis
# lock (SET NX PX) so answer-race protection holds across worker processes/replicas.
# Default off falls back to the in-process asyncio lock (safe for single process).
# See QUALITY_PERF_ROADMAP_2026-07-04.md item 6.
ENABLE_REDIS_SESSION_LOCK: bool = _parse_bool(os.getenv("ENABLE_REDIS_SESSION_LOCK"), default=False)
# When enabled, classic question selection samples from a bounded candidate window
# instead of ORDER BY random() over the whole filtered set. See item 5.
ENABLE_CANDIDATE_POOL_SAMPLING: bool = _parse_bool(os.getenv("ENABLE_CANDIDATE_POOL_SAMPLING"), default=False)
CANDIDATE_POOL_SIZE: int = int(os.getenv("CANDIDATE_POOL_SIZE", "25"))
# When enabled (and Redis available), classic selection consults a per-user Redis
# "seen" set to avoid the 3-join seen-question union on every question. See item 3.
ENABLE_SEEN_SET_CACHE: bool = _parse_bool(os.getenv("ENABLE_SEEN_SET_CACHE"), default=False)
SEEN_SET_TTL_SECONDS: int = int(os.getenv("SEEN_SET_TTL_SECONDS", "3600"))
# When enabled, classic/challenge never run LLM/RAG inline on a ready-queue miss;
# they serve a nearest-bucket cached question and enqueue a background refill. See item 4.
ENABLE_NO_INLINE_LLM: bool = _parse_bool(os.getenv("ENABLE_NO_INLINE_LLM"), default=False)
# When enabled, custom-room per-concept theta updates go through the shared
# ConceptIRT path (consistent variance decay + mastery mapping). See item 8.
ENABLE_UNIFIED_CONCEPT_THETA: bool = _parse_bool(os.getenv("ENABLE_UNIFIED_CONCEPT_THETA"), default=False)

# Quiz / Game Rules
QUIZ_TIME_LIMIT_SECONDS: int = int(os.getenv("QUIZ_TIME_LIMIT_SECONDS", "30"))
QUIZ_QUESTIONS_PER_SESSION: int = int(os.getenv("QUIZ_QUESTIONS_PER_SESSION", "10"))
CLASSIC_QUESTIONS_PER_SESSION: int = int(
    os.getenv("CLASSIC_QUESTIONS_PER_SESSION", str(QUIZ_QUESTIONS_PER_SESSION))
)
VISUAL_QUESTIONS_PER_SESSION: int = int(
    os.getenv("VISUAL_QUESTIONS_PER_SESSION", str(QUIZ_QUESTIONS_PER_SESSION))
)
VISUAL_PREGEN_BATCH_SIZE: int = int(os.getenv("VISUAL_PREGEN_BATCH_SIZE", "10"))
PVP_QUESTIONS_PER_MATCH: int = int(os.getenv("PVP_QUESTIONS_PER_MATCH", "5"))
PVP_CANDIDATE_POOL_SIZE: int = int(os.getenv("PVP_CANDIDATE_POOL_SIZE", "100"))
CHALLENGE_POINTS_LEVEL_1: tuple[int, int] = _parse_int_pair(os.getenv("CHALLENGE_POINTS_LEVEL_1"), (3, -1))
CHALLENGE_POINTS_LEVEL_2: tuple[int, int] = _parse_int_pair(os.getenv("CHALLENGE_POINTS_LEVEL_2"), (5, -2))
CHALLENGE_POINTS_LEVEL_3: tuple[int, int] = _parse_int_pair(os.getenv("CHALLENGE_POINTS_LEVEL_3"), (7, -4))
CHALLENGE_POINTS_LEVEL_4: tuple[int, int] = _parse_int_pair(os.getenv("CHALLENGE_POINTS_LEVEL_4"), (9, -6))
CHALLENGE_POINTS_LEVEL_5: tuple[int, int] = _parse_int_pair(os.getenv("CHALLENGE_POINTS_LEVEL_5"), (11, -9))
CHALLENGE_STREAK_UP_THRESHOLD: int = int(os.getenv("CHALLENGE_STREAK_UP_THRESHOLD", "4"))
CHALLENGE_STREAK_DOWN_THRESHOLD: int = int(os.getenv("CHALLENGE_STREAK_DOWN_THRESHOLD", "2"))
CHALLENGE_RANK_D_MIN: int = int(os.getenv("CHALLENGE_RANK_D_MIN", "1000"))
CHALLENGE_RANK_C_MIN: int = int(os.getenv("CHALLENGE_RANK_C_MIN", "3000"))
CHALLENGE_RANK_B_MIN: int = int(os.getenv("CHALLENGE_RANK_B_MIN", "7000"))
CHALLENGE_RANK_A_MIN: int = int(os.getenv("CHALLENGE_RANK_A_MIN", "15000"))
CHALLENGE_SESSION_QUESTION_TTL_SECONDS: int = int(
    os.getenv("CHALLENGE_SESSION_QUESTION_TTL_SECONDS", str(6 * 60 * 60))
)
ADMIN_DB_INSPECTOR_DEFAULT_LIMIT: int = int(os.getenv("ADMIN_DB_INSPECTOR_DEFAULT_LIMIT", "100"))
ADMIN_DB_INSPECTOR_MAX_LIMIT: int = int(os.getenv("ADMIN_DB_INSPECTOR_MAX_LIMIT", "500"))
DATA_REPAIR_BATCH_SIZE: int = int(os.getenv("DATA_REPAIR_BATCH_SIZE", "1000"))
CLEANUP_USER_BATCH_SIZE: int = int(os.getenv("CLEANUP_USER_BATCH_SIZE", "1000"))

POINTS_BASE_AWARD: int = int(os.getenv("POINTS_BASE_AWARD", "10"))
POINTS_TIME_BONUS_DIVISOR: int = int(os.getenv("POINTS_TIME_BONUS_DIVISOR", "3"))
POINTS_HINT_PENALTY: int = int(os.getenv("POINTS_HINT_PENALTY", "3"))
POINTS_WRONG_PENALTY: int = int(os.getenv("POINTS_WRONG_PENALTY", "5"))

# Inactivity Decay
INACTIVITY_DECAY_DAYS: int = int(os.getenv("INACTIVITY_DECAY_DAYS", "14"))
INACTIVITY_DECAY_FACTOR: float = float(os.getenv("INACTIVITY_DECAY_FACTOR", "0.1"))

# Session / Cache TTLs
SESSION_TTL_SECONDS: int = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
IDEMPOTENCY_TTL_SECONDS: int = int(os.getenv("IDEMPOTENCY_TTL_SECONDS", "3600"))
QUESTION_CACHE_TTL_SECONDS: int = int(os.getenv("QUESTION_CACHE_TTL_SECONDS", "3600"))
SESSION_LOCK_TTL_SECONDS: int = int(os.getenv("SESSION_LOCK_TTL_SECONDS", "60"))
SESSION_LOCK_TIMEOUT_SECONDS: int = int(os.getenv("SESSION_LOCK_TIMEOUT_SECONDS", "30"))
QUESTION_PREWARM_LOW_WATERMARK: int = int(os.getenv("QUESTION_PREWARM_LOW_WATERMARK", "2"))
QUESTION_PREWARM_BATCH_SIZE: int = int(os.getenv("QUESTION_PREWARM_BATCH_SIZE", "4"))
CHALLENGE_PREGEN_TARGET_PER_LEVEL: int = int(os.getenv("CHALLENGE_PREGEN_TARGET_PER_LEVEL", "12"))
# Keep challenge warm queues smaller and targeted so live usage does not compete
# with a large background refill burst across every level.
CHALLENGE_PREGEN_BATCH_SIZE: int = int(os.getenv("CHALLENGE_PREGEN_BATCH_SIZE", "3"))
CHALLENGE_PREGEN_TOPUP_THRESHOLD: int = int(os.getenv("CHALLENGE_PREGEN_TOPUP_THRESHOLD", "6"))
CHALLENGE_PREGEN_LEVEL_RADIUS: int = int(os.getenv("CHALLENGE_PREGEN_LEVEL_RADIUS", "1"))
CLASSIC_PREGEN_TARGET_PER_BUCKET: int = int(os.getenv("CLASSIC_PREGEN_TARGET_PER_BUCKET", "50"))
CLASSIC_PREGEN_BATCH_SIZE: int = int(os.getenv("CLASSIC_PREGEN_BATCH_SIZE", "8"))
CLASSIC_PREGEN_TOPUP_THRESHOLD: int = int(os.getenv("CLASSIC_PREGEN_TOPUP_THRESHOLD", "40"))
QUESTION_READY_QUEUE_TTL_SECONDS: int = int(os.getenv("QUESTION_READY_QUEUE_TTL_SECONDS", "21600"))
QUESTION_READY_DIFFICULTY_BUCKETS: tuple[int, ...] = _parse_int_tuple(
    os.getenv("QUESTION_READY_DIFFICULTY_BUCKETS"),
    (1, 2, 3, 4, 5),
)
REDIS_MATCHMAKING_TTL_SECONDS: int = int(os.getenv("REDIS_MATCHMAKING_TTL_SECONDS", "3600"))
PROVIDER_429_BACKOFF_SECONDS: int = int(os.getenv("PROVIDER_429_BACKOFF_SECONDS", "30"))

# Custom Room Configuration
CUSTOM_ROOM_TOPICS: dict[str, dict[str, str]] = {
    "History": {
        "World War II": "1939-1945 global conflict",
        "Cold War": "1947-1991 superpower standoff",
        "Ancient Rome": "27 BC - 476 AD empire",
        "Medieval Europe": "5th-15th centuries",
        "Renaissance": "14th-17th century revival",
    },
    "Geography": {
        "France": "Western European nation",
        "Japan": "East Asian island nation",
        "Brazil": "South American giant",
        "Egypt": "North African nation",
        "Australia": "Oceanian continent-nation",
    },
}

CUSTOM_ROOM_FACTS_PER_TOPIC: int = int(os.getenv("CUSTOM_ROOM_FACTS_PER_TOPIC", "1000"))
CUSTOM_ROOM_GENERATION_TARGET: int = int(os.getenv("CUSTOM_ROOM_GENERATION_TARGET", "1"))
CUSTOM_ROOM_RECENT_QUESTION_LIMIT: int = int(os.getenv("CUSTOM_ROOM_RECENT_QUESTION_LIMIT", "20"))
CUSTOM_ROOM_SESSION_TTL: int = int(os.getenv("CUSTOM_ROOM_SESSION_TTL", "3600"))
CUSTOM_ROOM_SIMPLE_MODE: bool = _parse_bool(os.getenv("CUSTOM_ROOM_SIMPLE_MODE"), default=False)

# Question-bank source taxonomy. The source column is provenance; these groups
# decide room reuse and admin reporting.
QUESTION_SOURCE_SEED: tuple[str, ...] = ("seed",)
QUESTION_SOURCE_ADMIN: tuple[str, ...] = ("admin", "manual", "import")
QUESTION_SOURCE_CLASSIC_GENERATED: tuple[str, ...] = ("llm", "classic_llm")
QUESTION_SOURCE_CHALLENGE_GENERATED: tuple[str, ...] = ("challenge_llm", "challenge_security_probe")
QUESTION_SOURCE_CUSTOM_GENERATED_PREFIXES: tuple[str, ...] = ("custom_llm", "custom_template", "custom_rag")
QUESTION_SOURCE_GENERATED_EXACT: tuple[str, ...] = (
    *QUESTION_SOURCE_CLASSIC_GENERATED,
    *QUESTION_SOURCE_CHALLENGE_GENERATED,
)

# Level thresholds for user progression.
_LEVEL_THRESHOLDS: list[tuple[int, str]] = [
    (5000, "Master"),
    (1500, "Expert"),
    (500, "Scholar"),
    (100, "Apprentice"),
    (0, "Novice"),
]


def compute_level(points: int) -> str:
    """Return the level name matching the current points total."""
    for threshold, label in _LEVEL_THRESHOLDS:
        if points >= threshold:
            return label
    return "Novice"


def validate_security_config() -> None:
    """Fail fast for insecure auth/runtime settings."""
    normalized_secret = (JWT_SECRET_KEY or "").strip()

    if not normalized_secret:
        raise RuntimeError(
            "CRITICAL: JWT_SECRET_KEY is empty. Set a strong secret via JWT_SECRET_KEY."
        )

    if len(normalized_secret) < JWT_MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"CRITICAL: JWT_SECRET_KEY is too short ({len(normalized_secret)} chars). "
            f"Minimum {JWT_MIN_SECRET_LENGTH} required."
        )

    if ENVIRONMENT.lower() == "production" and normalized_secret == "change-this-dev-secret-change-this-dev-secret":
        raise RuntimeError(
            "CRITICAL: JWT_SECRET_KEY is using the default insecure placeholder in production."
        )

    if ENVIRONMENT.lower() != "production" and normalized_secret == "change-this-dev-secret-change-this-dev-secret":
        logger.warning(
            "JWT_SECRET_KEY is using the default development placeholder. "
            "Set JWT_SECRET_KEY in .env to avoid insecure local tokens."
        )

    if ENVIRONMENT.lower() == "production" and AUTO_CREATE_TABLES:
        raise RuntimeError(
            "CRITICAL: AUTO_CREATE_TABLES is enabled in production! "
            "This is dangerous. Use Alembic migrations instead. Set AUTO_CREATE_TABLES=false."
        )

    if ENVIRONMENT.lower() == "production" and "@" not in REDIS_URL:
        raise RuntimeError(
            "CRITICAL: REDIS_URL has no credentials in production. "
            "Use an authenticated Redis URL or set REDIS_PASSWORD."
        )

    if DEV_BYPASS_AUTH and ENVIRONMENT.lower() == "production":
        raise RuntimeError(
            "CRITICAL: DEV_BYPASS_AUTH is enabled in production! "
            "This allows anyone to impersonate any user. Set DEV_BYPASS_AUTH=false immediately."
        )

    if POINTS_TIME_BONUS_DIVISOR <= 0:
        raise RuntimeError(
            "POINTS_TIME_BONUS_DIVISOR must be > 0 to avoid division by zero"
        )
