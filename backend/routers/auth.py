"""
routers/auth.py — Unified authentication router for AdaptIQ.

Covers:
  - POST /api/auth/signup          → Register a new user
  - POST /api/auth/login           → Login with email+password
  - GET  /api/auth/me              → Get current user profile (token-protected)
  - GET  /api/auth/profile         → Alias for /me (returns user fields directly)
  - POST /api/auth/forgot-password → Request OTP for password reset
  - POST /api/auth/reset-password  → Reset password with OTP verification
  - POST /api/auth/bootstrap-admin → Promote user to admin (dev only)

Dependencies exported for other routers:
  - get_db(request)        → yields AsyncSession from app.state
  - get_current_user(...)  → returns (User, issued_at) tuple

Internal helper groups in this module:
    - Password helpers: _hash_password, _verify_password
    - JWT helpers: _create_access_token, _build_user_out
    - OTP helpers: _save_otp, _read_otp, _bump_otp_attempts, _delete_otp
"""

import json
import logging
import os
import uuid
import hmac
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple

import bcrypt
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request, Response
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import Integer, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.email_service import is_non_routable_test_recipient, send_otp_email
from services.security_utils import redact_email, redact_log_value, stable_digest
from config import (
    JWT_SECRET_KEY,
    JWT_ALGORITHM,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ENVIRONMENT,
    POINTS_BASE_AWARD,
    POINTS_TIME_BONUS_DIVISOR,
    POINTS_HINT_PENALTY,
    POINTS_WRONG_PENALTY,
)
from dependencies import limiter
from database.challenge_models import ChallengeAnswer, ChallengeSession
from database.concept_models import ClassicSession
from database.custom_models import CustomSession
from database.pvp_models import PvPMatchAnswer
from database.visual_models import VisualSession
from database.models import User
from database.models import UserResponse

logger = logging.getLogger(__name__)

# Admin bootstrap key from env — empty disables the endpoint
ADMIN_BOOTSTRAP_KEY: str = os.getenv("ADMIN_BOOTSTRAP_KEY", "")

ACCESS_COOKIE_NAME = "adaptiq_access"
CSRF_COOKIE_NAME = "adaptiq_csrf"
UNSAFE_HTTP_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
LOGIN_LOCKOUT_SECONDS = 5 * 60

# In-memory OTP fallback when Redis is unavailable (dev only)
_otp_store: dict[str, dict] = {}


def _db_utc_now() -> datetime:
    """Return UTC time as naive datetime for TIMESTAMP WITHOUT TIME ZONE columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_user_banned_now(user: User, now: Optional[datetime] = None) -> bool:
    """True when user has an active temporary ban window."""
    if not getattr(user, "ban_until", None):
        return False
    effective_now = now or _db_utc_now()
    return bool(user.ban_until and user.ban_until > effective_now)


def _clear_expired_ban(user: User, now: Optional[datetime] = None) -> bool:
    """Clear expired temporary bans; returns True when a reset was applied."""
    effective_now = now or _db_utc_now()
    if not getattr(user, "ban_until", None):
        return False
    if user.ban_until <= effective_now:
        user.ban_until = None
        user.ban_reason = None
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
# PYDANTIC REQUEST / RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════════════


class SignupRequest(BaseModel):
    """Registration payload — email must be unique, password ≥ 8 chars."""
    email: EmailStr
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=8, max_length=72)


class LoginRequest(BaseModel):
    """Login payload — email + plaintext password."""
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    """Forgot-password payload — email to send OTP to."""
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Reset-password payload — email + OTP code + new password."""
    email: EmailStr
    code: str = Field(min_length=4, max_length=8)
    new_password: str = Field(min_length=8, max_length=128)


class ProfileEmailChangeRequest(BaseModel):
    """Request a verification code for a profile email change."""
    new_email: EmailStr


class ProfileEmailChangeConfirmRequest(BaseModel):
    """Confirm a pending profile email change."""
    new_email: EmailStr
    code: str = Field(min_length=4, max_length=8)


class BootstrapAdminRequest(BaseModel):
    """Promote a user to admin using a secret key (dev/setup only)."""
    email: EmailStr
    bootstrap_key: str = Field(min_length=8, max_length=256)


class AuthUserOut(BaseModel):
    """User fields returned in auth responses."""
    id: str
    email: str
    username: str
    points: int = 0
    level: str = "Novice"
    is_active: bool = True
    is_admin: bool = False
    created_at: datetime
    profile_picture: Optional[str] = None


class AuthResponse(BaseModel):
    """Signup/login success response — includes JWT + user profile."""
    access_token: str
    token_type: str = "bearer"
    user: AuthUserOut


class MeOut(BaseModel):
    """GET /me response — user profile + token issued_at timestamp."""
    user: AuthUserOut
    issued_at: datetime


class MessageOut(BaseModel):
    """Generic success message response."""
    message: str


class UpdateProfileRequest(BaseModel):
    """Editable profile fields for the authenticated user."""
    username: Optional[str] = Field(default=None, min_length=3, max_length=100)
    email: Optional[EmailStr] = None
    current_password: Optional[str] = Field(default=None, min_length=1, max_length=128)
    new_password: Optional[str] = Field(default=None, min_length=8, max_length=72)
    profile_picture: Optional[str] = None


class RoomProgressOut(BaseModel):
    """Per-room progress percentages for the dashboard."""
    classic: int = 0
    challenge: int = 0
    pvp: int = 0
    custom: int = 0
    visual: int = 0


class RoomLocksOut(BaseModel):
    """Per-room lock state for the dashboard."""
    classic: bool = False
    challenge: bool = False
    pvp: bool = False
    custom: bool = False
    visual: bool = False


class UserStatsOut(BaseModel):
    """Dashboard stats payload for the authenticated user."""
    id: str
    points: int
    level: str
    total_questions: int
    global_accuracy: float
    daily_questions: int
    daily_accuracy: float
    learning_time_minutes: int
    daily_points: int
    streak_days: int
    room_progress: RoomProgressOut
    room_locks: RoomLocksOut


class DailyTrendPointOut(BaseModel):
    """One day in the daily activity trend series."""
    date: str
    day: str
    count: int
    correct: int
    points: int


class DailyTrendOut(BaseModel):
    """Daily activity trend payload."""
    days: int
    points: list[DailyTrendPointOut]


# ═══════════════════════════════════════════════════════════════════════════
# PASSWORD HASHING (bcrypt — no passlib dependency)
# ═══════════════════════════════════════════════════════════════════════════


# Hash a plaintext password using bcrypt with a fixed cost factor.
def _hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt (12 rounds).
    Example: _hash_password("mySecret123") → "$2b$12$..."
    """
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


# Verify a plaintext password against a persisted bcrypt hash.
def _verify_password(password: str, password_hash: str) -> bool:
    """Compare plaintext password against bcrypt hash.
    Example: _verify_password("mySecret123", stored_hash) → True/False
    """
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except Exception as exc:
        logger.warning("Password verification error: %s", exc)
        return False


# ═══════════════════════════════════════════════════════════════════════════
# JWT TOKEN HELPERS
# ═══════════════════════════════════════════════════════════════════════════


# Build a short-lived signed access token for API authentication.
def _create_access_token(user_id: str) -> str:
    """Create a signed JWT with sub=user_id, exp=30 min, jti=random.
    Example: _create_access_token("550e8400-...") → "eyJhbGci..."
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def _cookie_secure() -> bool:
    return ENVIRONMENT.lower() == "production"


def _set_auth_cookies(response: Response, token: str) -> str:
    csrf_token = secrets.token_urlsafe(32)
    max_age = int(ACCESS_TOKEN_EXPIRE_MINUTES) * 60
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        token,
        max_age=max_age,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )
    return csrf_token


def _clear_auth_cookies(response: Response) -> None:
    for name in (ACCESS_COOKIE_NAME, CSRF_COOKIE_NAME):
        response.delete_cookie(
            name,
            httponly=(name == ACCESS_COOKIE_NAME),
            secure=_cookie_secure(),
            samesite="lax",
            path="/",
        )


def _require_cookie_csrf(request: Request, csrf_cookie: Optional[str], csrf_header: Optional[str]) -> None:
    if request.method.upper() not in UNSAFE_HTTP_METHODS:
        return
    if not csrf_cookie or not csrf_header:
        raise HTTPException(status_code=403, detail="Missing CSRF token")
    if not hmac.compare_digest(str(csrf_cookie), str(csrf_header)):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


# Convert a database user row into the auth response shape.
def _build_user_out(user: User) -> AuthUserOut:
    """Map SQLAlchemy User row to AuthUserOut pydantic model."""
    return AuthUserOut(
        id=str(user.id),
        email=user.email,
        username=user.username,
        points=user.points or 0,
        level=user.level or "Novice",
        is_active=bool(user.is_active),
        is_admin=bool(getattr(user, "is_admin", False)),
        created_at=user.created_at,
        profile_picture=getattr(user, "profile_picture", None),
    )


# ═══════════════════════════════════════════════════════════════════════════
# SHARED DEPENDENCIES (imported by other routers)
# ═══════════════════════════════════════════════════════════════════════════


# Provide a request-scoped async DB session from app state.
async def get_db(request: Request):
    """Yield an AsyncSession from app.state.db_session_factory.
    Used as a FastAPI dependency: db: AsyncSession = Depends(get_db)
    """
    factory = getattr(request.app.state, "db_session_factory", None)
    if not factory:
        raise HTTPException(status_code=503, detail="Database unavailable")
    async with factory() as session:
        yield session


# Return the configured Redis client when available.
async def get_redis(request: Request):
    """Return the Redis client from app state, or None if unavailable."""
    return getattr(request.app.state, "redis", None)


def _login_failure_key(email: str) -> str:
    return f"auth_login_fail:{stable_digest((email or '').lower().strip())}"


async def _read_login_failure(redis_client, email: str, request: Request) -> dict:
    key = _login_failure_key(email)
    if redis_client is not None:
        try:
            raw = await redis_client.get(key)
            if not raw:
                return {}
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return json.loads(raw)
        except Exception:
            pass
    store = getattr(request.app.state, "_login_failure_store", None)
    if store is None:
        return {}
    data = store.get(key, {})
    if data.get("expires_at", 0) <= time.time():
        store.pop(key, None)
        return {}
    return data


async def _write_login_failure(redis_client, email: str, request: Request, data: dict) -> None:
    key = _login_failure_key(email)
    ttl = max(LOGIN_FAILURE_WINDOW_SECONDS, LOGIN_LOCKOUT_SECONDS)
    if redis_client is not None:
        try:
            await redis_client.set(key, json.dumps(data), ex=ttl)
            return
        except Exception:
            pass
    store = getattr(request.app.state, "_login_failure_store", None)
    if store is None:
        store = {}
        setattr(request.app.state, "_login_failure_store", store)
    data["expires_at"] = time.time() + ttl
    store[key] = data


async def _clear_login_failure(redis_client, email: str, request: Request) -> None:
    key = _login_failure_key(email)
    if redis_client is not None:
        try:
            await redis_client.delete(key)
            return
        except Exception:
            pass
    store = getattr(request.app.state, "_login_failure_store", None)
    if store is not None:
        store.pop(key, None)


async def _enforce_login_throttle(redis_client, email: str, request: Request) -> None:
    data = await _read_login_failure(redis_client, email, request)
    locked_until = float(data.get("locked_until") or 0)
    if locked_until > time.time():
        raise HTTPException(status_code=429, detail="Too many failed login attempts. Try again later.")


async def _record_login_failure(redis_client, email: str, request: Request) -> None:
    data = await _read_login_failure(redis_client, email, request)
    count = int(data.get("count") or 0) + 1
    data = {"count": count}
    if count >= LOGIN_FAILURE_LIMIT:
        data["locked_until"] = time.time() + LOGIN_LOCKOUT_SECONDS
    await _write_login_failure(redis_client, email, request, data)


# Authenticate the bearer token and load the corresponding active user.
async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    adaptiq_access: Optional[str] = Cookie(default=None),
    adaptiq_csrf: Optional[str] = Cookie(default=None),
    x_csrf_token: Optional[str] = Header(default=None, alias="X-CSRF-Token"),
    db: AsyncSession = Depends(get_db),
) -> Tuple[User, datetime]:
    """Extract and validate current user from Bearer token.

    Returns a (User, issued_at) tuple for compatibility with all routers.
    Raises 401 on missing/invalid token or inactive user.

    Example header: Authorization: Bearer eyJhbGci...
    """
    token_source = "bearer"
    cookie_token = adaptiq_access if isinstance(adaptiq_access, str) else None
    csrf_cookie = adaptiq_csrf if isinstance(adaptiq_csrf, str) else None
    csrf_header = x_csrf_token if isinstance(x_csrf_token, str) else None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()
    elif cookie_token:
        token_source = "cookie"
        token = cookie_token.strip()
        _require_cookie_csrf(request, csrf_cookie, csrf_header)
    else:
        raise HTTPException(status_code=401, detail="Missing authentication credentials")

    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        issued_at = datetime.fromtimestamp(
            int(payload.get("iat", 0)), tz=timezone.utc
        )
    except (JWTError, ValueError) as exc:
        logger.warning("Token decode failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid token")

    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    try:
        user_uuid = uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user = await db.get(User, user_uuid)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    now = _db_utc_now()
    if _clear_expired_ban(user, now):
        await db.commit()
    elif _is_user_banned_now(user, now):
        reason = (user.ban_reason or "No reason provided").strip()
        raise HTTPException(
            status_code=403,
            detail=f"User account is banned until {user.ban_until.isoformat()} ({reason})",
        )

    if not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    logger.debug("Authenticated user=%s source=%s", str(user.id)[:8], token_source)
    return user, issued_at


# ═══════════════════════════════════════════════════════════════════════════
# OTP HELPERS (Redis with in-memory fallback)
# ═══════════════════════════════════════════════════════════════════════════


# Compute a consecutive-day activity streak ending today.
def _compute_streak_days(day_counts: dict[date, int], today: date) -> int:
    streak = 0
    cursor = today
    while day_counts.get(cursor, 0) > 0:
        streak += 1
        cursor = cursor - timedelta(days=1)
    return streak


def _response_points_delta(*, answered_correct: bool, time_taken: int, used_hint: bool) -> int:
    """Compute dashboard points for one response using Classic Room scoring rules."""
    if answered_correct:
        remaining_seconds = max(0, 30 - int(time_taken or 0))
        delta = int(POINTS_BASE_AWARD) + int(remaining_seconds // int(POINTS_TIME_BONUS_DIVISOR))
    else:
        delta = -int(POINTS_WRONG_PENALTY)

    if used_hint:
        delta -= int(POINTS_HINT_PENALTY)

    return int(delta)


# Compute progress shares from per-room activity counts.
def _compute_room_progress(
    classic_count: int,
    challenge_count: int,
    custom_count: int,
    pvp_count: int,
) -> RoomProgressOut:
    total = classic_count + challenge_count + custom_count + pvp_count
    if total <= 0:
        return RoomProgressOut()

    return RoomProgressOut(
        classic=int(round((classic_count / total) * 100)),
        challenge=int(round((challenge_count / total) * 100)),
        custom=int(round((custom_count / total) * 100)),
        pvp=int(round((pvp_count / total) * 100)),
        visual=0,
    )


# Persist a reset OTP code with a short expiration window.
async def _save_otp(redis_client, email: str, code: str) -> None:
    """Store a 6-digit OTP for password reset (TTL 5 min)."""
    await _save_otp_for_purpose(redis_client, email, code, purpose="reset")


async def _save_otp_for_purpose(
    redis_client,
    key_id: str,
    code: str,
    *,
    purpose: str,
    extra: Optional[dict] = None,
) -> None:
    """Store an OTP payload under a purpose-specific namespace."""
    key = f"otp:{purpose}:{key_id}"
    payload = {"code": code, "attempts": 0, **(extra or {})}
    if redis_client is not None:
        await redis_client.set(key, json.dumps(payload), ex=300)
    else:
        _otp_store[key] = {
            **payload,
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=300),
        }
    logger.info("OTP stored purpose=%s target=%s", purpose, redact_email(str(key_id).rsplit(":", 1)[-1]))


# Load the current OTP payload for an email if still valid.
async def _read_otp(redis_client, email: str) -> Optional[dict]:
    """Read stored OTP, return None if expired or missing."""
    return await _read_otp_for_purpose(redis_client, email, purpose="reset")


async def _read_otp_for_purpose(redis_client, key_id: str, *, purpose: str) -> Optional[dict]:
    """Read stored purpose-specific OTP, return None if expired or missing."""
    key = f"otp:{purpose}:{key_id}"
    if redis_client is not None:
        data = await redis_client.get(key)
        return json.loads(data) if data else None

    cached = _otp_store.get(key)
    if not cached:
        return None
    if datetime.now(timezone.utc) > cached["expires_at"]:
        _otp_store.pop(key, None)
        return None
    return {k: v for k, v in cached.items() if k != "expires_at"}


# Increment the failed-attempt counter for a reset OTP.
async def _bump_otp_attempts(redis_client, email: str, current: dict) -> None:
    """Increment OTP attempt counter (locks out after 3 failed tries)."""
    await _bump_otp_attempts_for_purpose(redis_client, email, current, purpose="reset")


async def _bump_otp_attempts_for_purpose(redis_client, key_id: str, current: dict, *, purpose: str) -> None:
    """Increment a purpose-specific OTP attempt counter."""
    key = f"otp:{purpose}:{key_id}"
    next_payload = {
        **current,
        "attempts": int(current.get("attempts", 0)) + 1,
    }
    if redis_client is not None:
        await redis_client.set(key, json.dumps(next_payload), ex=300)
    else:
        cached = _otp_store.get(key)
        if cached:
            cached["attempts"] = next_payload["attempts"]


# Delete an OTP after success or lockout.
async def _delete_otp(redis_client, email: str) -> None:
    """Remove OTP after successful use or max attempts."""
    await _delete_otp_for_purpose(redis_client, email, purpose="reset")


async def _delete_otp_for_purpose(redis_client, key_id: str, *, purpose: str) -> None:
    """Remove a purpose-specific OTP after successful use or max attempts."""
    key = f"otp:{purpose}:{key_id}"
    if redis_client is not None:
        await redis_client.delete(key)
    _otp_store.pop(key, None)


# ═══════════════════════════════════════════════════════════════════════════
# ROUTER
# ═══════════════════════════════════════════════════════════════════════════

auth_router = APIRouter(prefix="/api/auth", tags=["Auth"])


@auth_router.post("/signup", response_model=AuthResponse)
@limiter.limit("20/minute")
# Register a brand-new user and return an access token.
async def signup(
    request: Request,
    response: Response,
    payload: SignupRequest,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user account.

    1. Checks email/username uniqueness
    2. Hashes the password with bcrypt
    3. Creates the user row
    4. Returns JWT + user profile
    """
    logger.info("Signup attempt: email=%s username=%s", redact_email(payload.email), redact_log_value(payload.username))

    # Check email uniqueness
    existing_email = await db.scalar(
        select(User).where(User.email == payload.email.lower().strip())
    )
    if existing_email:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Check username uniqueness
    existing_username = await db.scalar(
        select(User).where(User.username == payload.username.strip())
    )
    if existing_username:
        raise HTTPException(status_code=409, detail="Username already taken")

    # Create user
    user = User(
        email=payload.email.lower().strip(),
        username=payload.username.strip(),
        password_hash=_hash_password(payload.password),
        points=0,
        level="Novice",
        created_at=_db_utc_now(),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        details = str(getattr(exc, "orig", exc)).lower()
        if "email" in details:
            raise HTTPException(status_code=409, detail="Email already registered")
        if "username" in details:
            raise HTTPException(status_code=409, detail="Username already taken")
        raise HTTPException(status_code=409, detail="User registration failed due to duplicate data")
    await db.refresh(user)

    token = _create_access_token(str(user.id))
    _set_auth_cookies(response, token)
    logger.info("User registered: id=%s email=%s", str(user.id)[:8], redact_email(user.email))

    return AuthResponse(access_token=token, user=_build_user_out(user))


@auth_router.post("/login", response_model=AuthResponse)
@limiter.limit("10/minute")
# Authenticate existing credentials and return an access token.
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis),
):
    """Authenticate user with email + password.

    Returns JWT + user profile on success, 401 on failure.
    Updates last_login timestamp.
    """
    logger.info("Login attempt: email=%s", redact_email(payload.email))
    login_email = payload.email.lower().strip()
    await _enforce_login_throttle(redis_client, login_email, request)

    user = await db.scalar(
        select(User).where(User.email == login_email)
    )
    if not user:
        await _record_login_failure(redis_client, login_email, request)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not _verify_password(payload.password, user.password_hash):
        logger.warning("Failed login for email=%s", redact_email(payload.email))
        await _record_login_failure(redis_client, login_email, request)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is disabled")

    now = _db_utc_now()
    if _clear_expired_ban(user, now):
        logger.info("Auto-cleared expired ban for user=%s", str(user.id)[:8])
    elif _is_user_banned_now(user, now):
        reason = (user.ban_reason or "No reason provided").strip()
        raise HTTPException(
            status_code=403,
            detail=f"User account is banned until {user.ban_until.isoformat()} ({reason})",
        )

    user.last_login = _db_utc_now()
    await db.commit()
    await _clear_login_failure(redis_client, login_email, request)

    token = _create_access_token(str(user.id))
    _set_auth_cookies(response, token)
    logger.info("User logged in: id=%s email=%s", str(user.id)[:8], redact_email(user.email))

    return AuthResponse(access_token=token, user=_build_user_out(user))


@auth_router.post("/logout", response_model=MessageOut)
async def logout(response: Response):
    """Clear browser auth cookies."""
    _clear_auth_cookies(response)
    return MessageOut(message="Logged out")


@auth_router.get("/me", response_model=MeOut)
# Return the authenticated user profile plus token issue time.
async def me(response: Response, current=Depends(get_current_user)):
    """Get current authenticated user profile + token issued_at.

    Requires: Authorization: Bearer <token>
    Returns: {user: {...}, issued_at: "2026-..."}
    """
    user, issued_at = current
    logger.debug("Profile requested: user=%s", str(user.id)[:8])
    
    # Re-issue cookies to extend the active session
    token = _create_access_token(str(user.id))
    _set_auth_cookies(response, token)
    
    return MeOut(user=_build_user_out(user), issued_at=issued_at)


@auth_router.get("/profile")
# Return the authenticated user profile without wrapper metadata.
async def profile(response: Response, current=Depends(get_current_user)):
    """Alias for /me — returns user fields directly (no wrapper).

    Requires: Authorization: Bearer <token>
    Returns: {id, email, username, points, level, ...}
    """
    user, _ = current
    
    # Re-issue cookies to extend the active session
    token = _create_access_token(str(user.id))
    _set_auth_cookies(response, token)
    
    return _build_user_out(user)


def _normalize_email(value: str) -> str:
    return str(value or "").lower().strip()


def _email_change_key(user_id: uuid.UUID | str, new_email: str) -> str:
    return f"{user_id}:{_normalize_email(new_email)}"


async def _ensure_email_change_allowed(db: AsyncSession, user: User, new_email: str) -> str:
    normalized = _normalize_email(new_email)
    if normalized == _normalize_email(user.email):
        raise HTTPException(status_code=400, detail="New email must be different from the current email")
    if is_non_routable_test_recipient(normalized):
        raise HTTPException(status_code=400, detail="Email domain cannot receive verification codes")

    existing_email = await db.scalar(
        select(User).where(User.email == normalized, User.id != user.id)
    )
    if existing_email:
        raise HTTPException(status_code=400, detail="Email already registered")
    return normalized


@auth_router.post("/profile/email-change/request", response_model=MessageOut)
@limiter.limit("5/minute")
async def request_profile_email_change(
    request: Request,
    payload: ProfileEmailChangeRequest,
    current=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis),
):
    """Send a verification code to the requested new profile email."""
    user, _issued_at = current
    new_email = await _ensure_email_change_allowed(db, user, str(payload.new_email))
    code = f"{secrets.randbelow(1_000_000):06d}"
    key_id = _email_change_key(user.id, new_email)

    await _save_otp_for_purpose(
        redis_client,
        key_id,
        code,
        purpose="email_change",
        extra={"user_id": str(user.id), "new_email": new_email},
    )
    await send_otp_email(recipient=new_email, otp_code=code, purpose="email change")

    logger.info(
        "Profile email-change code requested: user=%s new_email=%s",
        str(user.id)[:8],
        redact_email(new_email),
    )
    return MessageOut(message="Verification code sent to the new email address")


@auth_router.post("/profile/email-change/confirm", response_model=AuthUserOut)
@limiter.limit("10/minute")
async def confirm_profile_email_change(
    request: Request,
    payload: ProfileEmailChangeConfirmRequest,
    current=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis),
):
    """Confirm a pending profile email change and update the user's email."""
    user, _issued_at = current
    new_email = _normalize_email(str(payload.new_email))
    key_id = _email_change_key(user.id, new_email)
    otp_payload = await _read_otp_for_purpose(redis_client, key_id, purpose="email_change")

    if not otp_payload:
        raise HTTPException(status_code=400, detail="Verification code expired or not found")
    if str(otp_payload.get("user_id") or "") != str(user.id) or _normalize_email(otp_payload.get("new_email") or "") != new_email:
        await _delete_otp_for_purpose(redis_client, key_id, purpose="email_change")
        raise HTTPException(status_code=400, detail="Verification code does not match this email change")

    attempts = int(otp_payload.get("attempts", 0))
    if attempts >= 3:
        await _delete_otp_for_purpose(redis_client, key_id, purpose="email_change")
        raise HTTPException(status_code=400, detail="Verification code max attempts exceeded")

    if str(otp_payload.get("code") or "").strip() != payload.code.strip():
        await _bump_otp_attempts_for_purpose(redis_client, key_id, otp_payload, purpose="email_change")
        raise HTTPException(status_code=400, detail="Invalid verification code")

    new_email = await _ensure_email_change_allowed(db, user, new_email)
    user.email = new_email
    await db.commit()
    await db.refresh(user)
    await _delete_otp_for_purpose(redis_client, key_id, purpose="email_change")

    logger.info("Profile email changed: user=%s email=%s", str(user.id)[:8], redact_email(new_email))
    return _build_user_out(user)


@auth_router.patch("/profile", response_model=AuthUserOut)
@limiter.limit("20/minute")
# Update editable profile fields for the authenticated user.
async def update_profile(
    request: Request,
    payload: UpdateProfileRequest,
    current=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update current user's profile data (username/email/password).

    Rules:
      - Email changes must go through /profile/email-change/request + confirm.
      - Password change requires current_password + new_password.
    """
    user, _issued_at = current
    changed = False

    next_username = payload.username.strip() if payload.username is not None else None
    next_email = payload.email.lower().strip() if payload.email is not None else None

    if payload.new_password and not payload.current_password:
        raise HTTPException(status_code=400, detail="Current password is required to set a new password")
    if payload.current_password and not payload.new_password:
        raise HTTPException(status_code=400, detail="New password is required when current password is provided")

    if next_username is not None and next_username != user.username:
        if len(next_username) < 3:
            raise HTTPException(status_code=400, detail="Username must be at least 3 characters")
        existing_username = await db.scalar(
            select(User).where(User.username == next_username, User.id != user.id)
        )
        if existing_username:
            raise HTTPException(status_code=400, detail="Username already taken")
        user.username = next_username
        changed = True

    if next_email is not None and next_email != _normalize_email(user.email):
        raise HTTPException(
            status_code=400,
            detail="Email changes require verification. Use the profile email-change request and confirm endpoints.",
        )

    if payload.new_password and payload.current_password:
        if not _verify_password(payload.current_password, user.password_hash):
            raise HTTPException(status_code=401, detail="Current password is incorrect")
        user.password_hash = _hash_password(payload.new_password)
        changed = True

    if payload.profile_picture is not None:
        user.profile_picture = payload.profile_picture
        changed = True

    if changed:
        await db.commit()
        await db.refresh(user)
        logger.info("Profile updated for user=%s", str(user.id)[:8])

    return _build_user_out(user)


async def _safe_stats_scalar(db: AsyncSession, stmt, *, label: str, default: int = 0) -> int:
    """Run a dashboard-stats scalar aggregate defensively.

    Dashboard stats fan out across several room-specific tables (Challenge, PvP,
    Visual). If one of those tables/columns is missing or only partly migrated,
    degrade that single metric to ``default`` instead of failing the entire
    ``/stats`` response with a 500 (which blanks the user dashboard). A failed
    statement can leave the async session in an aborted transaction, so roll
    back before returning so later queries still run.
    """
    try:
        return int(await db.scalar(stmt) or default)
    except Exception as exc:
        logger.warning("stats aggregate unavailable (%s): %s", label, exc)
        try:
            await db.rollback()
        except Exception:
            pass
        return default


@auth_router.get("/stats", response_model=UserStatsOut)
@limiter.limit("120/minute")
# Return dynamic dashboard stats for the authenticated user.
async def stats(
    request: Request,
    current=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user, _issued_at = current
    user_id = user.id

    now = _db_utc_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    total_questions = await db.scalar(
        select(func.count())
        .select_from(UserResponse)
        .where(UserResponse.user_id == user_id)
    ) or 0
    correct_questions = await db.scalar(
        select(func.count())
        .select_from(UserResponse)
        .where(
            UserResponse.user_id == user_id,
            UserResponse.answered_correct == True,
        )
    ) or 0

    daily_questions = await db.scalar(
        select(func.count())
        .select_from(UserResponse)
        .where(
            UserResponse.user_id == user_id,
            UserResponse.created_at >= today_start,
        )
    ) or 0
    daily_correct = await db.scalar(
        select(func.count())
        .select_from(UserResponse)
        .where(
            UserResponse.user_id == user_id,
            UserResponse.created_at >= today_start,
            UserResponse.answered_correct == True,
        )
    ) or 0
    # Dashboard learning time must reflect every playable room.
    # Classic, Custom and Visual rooms write to UserResponse. Challenge and PvP
    # keep their own answer tables, so include them here instead of showing 0
    # after those rooms are played.
    response_seconds_today = await db.scalar(
        select(func.coalesce(func.sum(UserResponse.time_taken), 0))
        .where(
            UserResponse.user_id == user_id,
            UserResponse.created_at >= today_start,
        )
    ) or 0
    challenge_seconds_today = await _safe_stats_scalar(
        db,
        select(func.coalesce(func.sum(ChallengeAnswer.time_taken), 0))
        .select_from(ChallengeAnswer)
        .join(ChallengeSession, ChallengeSession.id == ChallengeAnswer.session_id)
        .where(
            ChallengeSession.user_id == user_id,
            ChallengeAnswer.created_at >= today_start,
        ),
        label="challenge_seconds_today",
    )
    pvp_seconds_today = await _safe_stats_scalar(
        db,
        select(func.coalesce(func.sum(PvPMatchAnswer.time_taken), 0))
        .where(
            PvPMatchAnswer.user_id == user_id,
            PvPMatchAnswer.answered_at >= today_start,
        ),
        label="pvp_seconds_today",
    )
    visual_ms_today = await _safe_stats_scalar(
        db,
        select(func.coalesce(func.sum(VisualSession.total_time_ms), 0))
        .where(
            VisualSession.user_id == user_id,
            VisualSession.started_at >= today_start,
        ),
        label="visual_ms_today",
    )
    total_seconds_today = (
        float(response_seconds_today or 0)
        + float(challenge_seconds_today or 0)
        + float(pvp_seconds_today or 0)
        + (float(visual_ms_today or 0) / 1000.0)
    )

    global_accuracy = (
        round((int(correct_questions) / int(total_questions)) * 100, 1)
        if int(total_questions) > 0
        else 0.0
    )
    daily_accuracy = (
        round((int(daily_correct) / int(daily_questions)) * 100, 1)
        if int(daily_questions) > 0
        else 0.0
    )
    learning_time_minutes = int(round(float(total_seconds_today) / 60.0))
    daily_point_rows = await db.execute(
        select(
            UserResponse.answered_correct,
            UserResponse.time_taken,
            UserResponse.used_hint,
        )
        .where(
            UserResponse.user_id == user_id,
            UserResponse.created_at >= today_start,
        )
    )
    daily_points = 0
    for answered_correct, time_taken, used_hint in daily_point_rows.all():
        daily_points += _response_points_delta(
            answered_correct=bool(answered_correct),
            time_taken=int(time_taken or 0),
            used_hint=bool(used_hint),
        )

    streak_window_start = today_start - timedelta(days=365)
    streak_rows = await db.execute(
        select(
            func.date(UserResponse.created_at).label("day"),
            func.count(UserResponse.id).label("count"),
        )
        .where(
            UserResponse.user_id == user_id,
            UserResponse.created_at >= streak_window_start,
        )
        .group_by(func.date(UserResponse.created_at))
    )
    day_counts: dict[date, int] = {}
    for day_value, count in streak_rows.all():
        if day_value is None:
            continue
        if isinstance(day_value, datetime):
            key = day_value.date()
        else:
            key = day_value
        day_counts[key] = int(count or 0)
    streak_days = _compute_streak_days(day_counts, today_start.date())

    classic_sessions = await _safe_stats_scalar(
        db,
        select(func.count())
        .select_from(ClassicSession)
        .where(ClassicSession.user_id == user_id),
        label="classic_sessions",
    )
    challenge_sessions = await _safe_stats_scalar(
        db,
        select(func.count())
        .select_from(ChallengeSession)
        .where(ChallengeSession.user_id == user_id),
        label="challenge_sessions",
    )
    custom_sessions = await _safe_stats_scalar(
        db,
        select(func.count())
        .select_from(CustomSession)
        .where(CustomSession.user_id == user_id),
        label="custom_sessions",
    )

    from database.pvp_models import PvPMatch

    pvp_matches = await _safe_stats_scalar(
        db,
        select(func.count())
        .select_from(PvPMatch)
        .where(
            or_(
                PvPMatch.user1_id == user_id,
                PvPMatch.user2_id == user_id,
            )
        ),
        label="pvp_matches",
    )

    room_progress = _compute_room_progress(
        int(classic_sessions),
        int(challenge_sessions),
        int(custom_sessions),
        int(pvp_matches),
    )
    room_locks = RoomLocksOut(
        classic=False,
        challenge=int(classic_sessions) < 1,
        custom=int(classic_sessions) < 1,
        pvp=int(challenge_sessions) < 1,
        visual=False,
    )

    return UserStatsOut(
        id=str(user.id),
        points=int(user.points or 0),
        level=str(user.level or "Novice"),
        total_questions=int(total_questions),
        global_accuracy=global_accuracy,
        daily_questions=int(daily_questions),
        daily_accuracy=daily_accuracy,
        learning_time_minutes=learning_time_minutes,
        daily_points=daily_points,
        streak_days=streak_days,
        room_progress=room_progress,
        room_locks=room_locks,
    )


@auth_router.get("/stats/daily-trend", response_model=DailyTrendOut)
@limiter.limit("120/minute")
# Return a day-by-day activity series for chart rendering.
async def stats_daily_trend(
    request: Request,
    days: int = Query(default=7, ge=1, le=90),
    current=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user, _issued_at = current

    safe_days = int(max(1, min(days, 90)))
    now = _db_utc_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    range_start = today_start - timedelta(days=safe_days - 1)

    rows = await db.execute(
        select(
            func.date(UserResponse.created_at).label("day"),
            func.count(UserResponse.id).label("count"),
            func.coalesce(func.sum(cast(UserResponse.answered_correct, Integer)), 0).label("correct"),
        )
        .where(
            UserResponse.user_id == user.id,
            UserResponse.created_at >= range_start,
        )
        .group_by(func.date(UserResponse.created_at))
    )

    day_map: dict[date, tuple[int, int]] = {}
    for day_value, count, correct in rows.all():
        if day_value is None:
            continue
        if isinstance(day_value, datetime):
            key = day_value.date()
        else:
            key = day_value
        day_map[key] = (int(count or 0), int(correct or 0))

    points: list[DailyTrendPointOut] = []
    for offset in range(safe_days):
        current_day = (range_start + timedelta(days=offset)).date()
        count, correct = day_map.get(current_day, (0, 0))
        points.append(
            DailyTrendPointOut(
                date=current_day.isoformat(),
                day=current_day.strftime("%a"),
                count=count,
                correct=correct,
                points=correct * 10,
            )
        )

    return DailyTrendOut(days=safe_days, points=points)


@auth_router.post("/forgot-password", response_model=MessageOut)
@limiter.limit("5/minute")
# Start password reset flow by generating an OTP for known users.
async def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis),
):
    """Request a password reset OTP.

    Always returns success (prevents email enumeration).
    In dev mode, OTP code is printed to console.
    """
    logger.info("Forgot-password request: email=%s", redact_email(payload.email))

    user = await db.scalar(
        select(User).where(User.email == payload.email.lower().strip())
    )
    if user:
        code = f"{secrets.randbelow(1_000_000):06d}"
        await _save_otp(redis_client, user.email, code)
        # Send OTP via email (falls back to console when SMTP is not configured)
        await send_otp_email(recipient=user.email, otp_code=code)

    return MessageOut(message="If the account exists, a reset code has been sent")


@auth_router.post("/reset-password", response_model=MessageOut)
@limiter.limit("10/minute")
# Verify OTP and replace the stored password hash.
async def reset_password(
    request: Request,
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis),
):
    """Reset password using OTP code.

    Validates OTP, checks max attempts (3), then updates password hash.
    """
    email = payload.email.lower().strip()
    logger.info("Reset-password attempt: email=%s", redact_email(email))

    otp_payload = await _read_otp(redis_client, email)
    if not otp_payload:
        raise HTTPException(status_code=400, detail="OTP expired or not found")

    attempts = int(otp_payload.get("attempts", 0))
    if attempts >= 3:
        await _delete_otp(redis_client, email)
        raise HTTPException(status_code=400, detail="OTP max attempts exceeded")

    if otp_payload.get("code") != payload.code.strip():
        await _bump_otp_attempts(redis_client, email, otp_payload)
        raise HTTPException(status_code=400, detail="Invalid OTP code")

    user = await db.scalar(select(User).where(User.email == email))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash = _hash_password(payload.new_password)
    await db.commit()
    await _delete_otp(redis_client, email)

    logger.info("Password reset successful: email=%s", redact_email(email))
    return MessageOut(message="Password reset successful")


@auth_router.post("/bootstrap-admin", response_model=MessageOut)
@limiter.limit("3/minute")
# Promote an existing user to admin using the bootstrap secret.
async def bootstrap_admin(
    request: Request,
    payload: BootstrapAdminRequest,
    db: AsyncSession = Depends(get_db),
):
    """Promote a user to admin using a secret bootstrap key.

    Only works if ADMIN_BOOTSTRAP_KEY env var is set.
    Used during initial setup — disable in production.
    """
    if ENVIRONMENT.lower() == "production":
        raise HTTPException(status_code=403, detail="Admin bootstrap is disabled in production")

    if not ADMIN_BOOTSTRAP_KEY:
        raise HTTPException(status_code=403, detail="Admin bootstrap is disabled")

    if not hmac.compare_digest(payload.bootstrap_key, ADMIN_BOOTSTRAP_KEY):
        logger.warning("Invalid bootstrap key attempt for email=%s", redact_email(payload.email))
        raise HTTPException(status_code=403, detail="Invalid bootstrap key")

    user = await db.scalar(
        select(User).where(User.email == payload.email.lower().strip())
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_admin = True
    await db.commit()

    logger.info("User promoted to admin: email=%s", redact_email(user.email))
    return MessageOut(message=f"User {user.email} promoted to admin")
