# 🐛 COMPREHENSIVE BUG AUDIT REPORT
**Date:** 2026-04-14  
**Status:** ANALYSIS ONLY (No changes made)

---

## 🔴 CRITICAL BUGS

### 1. **Authentication: Timezone-Naive datetime in JWT Token Generation**
**File:** `backend/routers/auth.py:377`  
**Severity:** CRITICAL - Production Security Issue  
**Issue:**
```python
user.last_login = datetime.utcnow()  # WRONG - deprecated, timezone-naive
```
**Problem:** 
- `datetime.utcnow()` is deprecated in Python 3.12+
- Creates timezone-naive datetime objects
- Can cause comparison issues when `last_login` is compared with timezone-aware datetimes elsewhere
- JWT token logic on line 153 uses `datetime.now(timezone.utc)` correctly, but login doesn't match

**Fix:**
```python
user.last_login = datetime.now(timezone.utc)  # CORRECT
```

---

### 2. **Dependencies Module: Non-functional Dependency Injection**
**File:** `backend/dependencies.py:21-45`  
**Severity:** CRITICAL - Core Functionality Broken  
**Issue:**
```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    raise HTTPException(500, detail="get_db not properly bound")

async def get_http_client() -> httpx.AsyncClient:
    raise HTTPException(500, detail="HTTP client not available")

async def get_redis():
    raise HTTPException(500, detail="Redis client not available")
```
**Problem:**
- All three dependency functions **always raise exceptions**
- They are never properly bound to app.state
- FastAPI routes depending on these will always fail with 500 error
- The pattern circumvents FastAPI's dependency injection system
- Routers use `from routers.auth import get_db` instead, but this bypasses dependencies.py entirely

**Fix:**
```python
async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    factory = getattr(request.app.state, "db_session_factory", None)
    if not factory:
        raise HTTPException(500, detail="Database unavailable")
    async with factory() as session:
        yield session
```

---

### 3. **Session Management: Memory Leak in Lock Storage**
**File:** `backend/services/session.py:226-244`  
**Severity:** HIGH - Memory Leak  
**Issue:**
```python
@asynccontextmanager
async def session_lock(self, session_id: str):
    """Acquire lock for session (prevent concurrent answer processing)."""
    if session_id not in _locks:
        _locks[session_id] = asyncio.Lock()
    
    lock = _locks[session_id]
    await lock.acquire()
    try:
        yield
    finally:
        lock.release()
```
**Problem:**
- Locks are **never deleted** from `_locks` dict after use
- Every unique session_id creates a permanent lock entry in memory
- With high user volume, `_locks` dict grows unbounded → memory leak
- Session locks accumulate indefinitely

**Fix:**
```python
@asynccontextmanager
async def session_lock(self, session_id: str):
    if session_id not in _locks:
        _locks[session_id] = asyncio.Lock()
    
    lock = _locks[session_id]
    await lock.acquire()
    try:
        yield
    finally:
        lock.release()
        # Clean up lock if no longer needed (optional, but prevents unbounded growth)
        # _locks.pop(session_id, None)
```

---

## 🟠 HIGH-PRIORITY BUGS

### 4. **Config Validation: JWT Secret Length Not Enforced**
**File:** `backend/config.py:56-61`  
**Severity:** HIGH - Security  
**Issue:**
```python
JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "change-this-dev-secret-change-this-dev-secret")
JWT_MIN_SECRET_LENGTH: int = int(os.getenv("JWT_MIN_SECRET_LENGTH", "32"))
```
And validation only happens in `validate_security_config()` which is called, but the check is:
```python
# (No check for JWT_SECRET_KEY length against JWT_MIN_SECRET_LENGTH!)
```
**Problem:**
- `JWT_MIN_SECRET_LENGTH` is defined but **never actually validated**
- Default JWT secret is `"change-this-dev-secret-change-this-dev-secret"` which is 45 chars, but no validation in production
- A weak JWT secret can be brute-forced
- `validate_security_config()` doesn't check JWT secret length

**Fix:**
```python
def validate_security_config() -> None:
    """Fail fast for insecure auth settings."""
    if len(JWT_SECRET_KEY) < JWT_MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"CRITICAL: JWT_SECRET_KEY is {len(JWT_SECRET_KEY)} chars but must be "
            f"at least {JWT_MIN_SECRET_LENGTH} chars for {JWT_ALGORITHM} security"
        )
    # ... rest of validation
```

---

### 5. **Schema: Deprecated datetime.utcnow() in Default Factory**
**File:** `backend/schemas.py:214`  
**Severity:** HIGH - Deprecation Warning  
**Issue:**
```python
class ErrorResponse(BaseModel):
    detail: str
    error_code: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)  # DEPRECATED
```
**Problem:**
- `datetime.utcnow()` is deprecated in Python 3.12+
- Will raise DeprecationWarning or error in future Python versions
- Creates timezone-naive datetime

**Fix:**
```python
from datetime import datetime, timezone
timestamp: datetime = Field(
    default_factory=lambda: datetime.now(timezone.utc)
)
```

---

### 6. **IRT Gradient Calculation: Comment Misleading**
**File:** `backend/database/irt.py:38-48`  
**Severity:** MEDIUM - Logic Correctness  
**Issue:**
```python
def update_theta(theta: float, beta: float, correct: bool) -> float:
    """
    Online MLE update for user ability θ using gradient ascent on log-likelihood.
    
    ∂ logL/∂θ = (correct - P(correct)) * P(correct) * (1 - P(correct)) / P(correct)
              = correct - P(correct)   [simplified for 1PL]
    """
    p = irt_probability(theta, beta)
    gradient = (1 if correct else 0) - p
    new_theta = theta + LEARN_RATE * gradient
```
**Problem:**
- The mathematical comment is confusing/potentially wrong
- The formula shows `(correct - P) * P * (1-P) / P` but then simplifies to `correct - P`
- The simplification is actually correct (P cancels), but the intermediate form is misleading
- For non-1PL models, this would be incorrect

**Note:** The actual code is functionally correct for 1PL, but documentation is misleading. Low priority.

---

## 🟡 MEDIUM-PRIORITY ISSUES

### 7. **Admin Server: Missing Error Handling for File Read**
**File:** `admin_server.py:33-34`  
**Severity:** MEDIUM - Robustness  
**Issue:**
```python
def do_GET(self):
    if self.path == "/" or self.path == "/admin_dashboard.html":
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        with open(DASHBOARD_FILE, "rb") as f:
            self.wfile.write(f.read())
```
**Problem:**
- No error handling if `DASHBOARD_FILE` doesn't exist
- Will crash with FileNotFoundError instead of returning 404
- No try-except around file operations

**Fix:**
```python
def do_GET(self):
    if self.path == "/" or self.path == "/admin_dashboard.html":
        try:
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            with open(DASHBOARD_FILE, "rb") as f:
                self.wfile.write(f.read())
        except FileNotFoundError:
            self.send_error(404, "Admin dashboard not found")
        except Exception as e:
            self.send_error(500, f"Internal error: {e}")
    else:
        self.send_error(404, "File not found")
```

---

### 8. **Database Pool Configuration: Potential Connection Exhaustion**
**File:** `backend/main.py:148-154`  
**Severity:** MEDIUM - Performance/Stability  
**Issue:**
```python
_engine = create_async_engine(
    DATABASE_URL,
    echo=(ENVIRONMENT == "development"),
    pool_size=10,
    max_overflow=20,
)
```
**Problem:**
- `pool_size=10` with `max_overflow=20` means max 30 concurrent connections
- High-traffic scenarios (100s of concurrent requests) could exhaust pool
- No pool_pre_ping to validate connections before reuse
- No pool_recycle to handle database connection timeouts
- Connections left in pool longer than database timeout tolerance

**Fix:**
```python
_engine = create_async_engine(
    DATABASE_URL,
    echo=(ENVIRONMENT == "development"),
    pool_size=20,  # Increase for production
    max_overflow=30,
    pool_pre_ping=True,  # Validate connections before reuse
    pool_recycle=3600,  # Recycle connections after 1 hour
    connect_args={"timeout": 10},
)
```

---

### 9. **Custom Router: Unused Import Side Effects**
**File:** `backend/routers/custom.py:15-16`  
**Severity:** LOW - Code Quality  
**Issue:**
```python
from sqlalchemy import func, or_, select
# 'or_' is imported but never used in the file
```
**Problem:**
- Dead import clutters code
- Not a bug, but violates DRY principle

**Fix:** Remove unused import `or_`

---

### 10. **Logging Configuration: Race Condition in log_message**
**File:** `admin_server.py:38-40`  
**Severity:** LOW - Thread Safety  
**Issue:**
```python
def log_message(self, format, *args):
    """Custom logging."""
    print(f"[{self.client_address[0]}] {format % args}")
```
**Problem:**
- Uses `print()` which can have race conditions with multiple threads
- Should use proper logging module instead

**Fix:**
```python
import logging
logger = logging.getLogger(__name__)

def log_message(self, format, *args):
    """Custom logging."""
    logger.info(f"[{self.client_address[0]}] {format % args}")
```

---

## 🟢 LOW-PRIORITY ISSUES / DOCUMENTATION GAPS

### 11. **Documentation Mismatch: Password Reset OTP Not Actually Sent**
**File:** `backend/routers/auth.py:409-432`  
**Severity:** LOW - Documentation/Behavior  
**Issue:**
```python
@auth_router.post("/forgot-password", response_model=MessageOut)
async def forgot_password(
    payload: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis),
):
    """Request a password reset OTP.
    
    Always returns success (prevents email enumeration).
    In dev mode, OTP code is printed to console.
    """
    # ...
    if user:
        code = f"{secrets.randbelow(1_000_000):06d}"
        await _save_otp(redis_client, user.email, code)
        # In production, send email here
        if ENVIRONMENT.lower() != "production":
            logger.info("[DEV OTP] Generated reset code for %s", user.email)
```
**Problem:**
- Comment says "In production, send email here" but there's NO email sending code
- In production, OTP is saved but **never delivered to user**
- Password reset flow is broken in production unless email service added

**Note:** Update documentation to reflect current state:
```python
# TODO: Implement email delivery service for production OTP delivery
# Currently: OTP only sent to logs (dev) / Redis
```

---

### 12. **Config Documentation: Contradictory Level Threshold**
**File:** `backend/config.py:127-133`  
**Severity:** LOW - Documentation  
**Issue:**
```python
_LEVEL_THRESHOLDS: list[tuple[int, str]] = [
    (5000, "Master"),
    (1500, "Expert"),
    (500,  "Scholar"),
    (100,  "Apprentice"),
    (0,    "Novice"),
]
```
**Problem:**
- Comment says "Level thresholds for user progression" but the thresholds are **descending**
- Logic is correct (threshold matched top-to-bottom), but naming/documentation is confusing

**Fix:** Add clearer comment:
```python
# Level thresholds for user progression (evaluate top-to-bottom)
# User with 2000 points → Expert (≥1500), not Scholar
_LEVEL_THRESHOLDS: list[tuple[int, str]] = [
    (5000, "Master"),    # 5000+ points
    (1500, "Expert"),    # 1500-4999 points
    (500,  "Scholar"),   # 500-1499 points
    (100,  "Apprentice"),  # 100-499 points
    (0,    "Novice"),    # 0-99 points
]
```

---

## 📋 SUMMARY TABLE

| # | Issue | Severity | File | Line | Type |
|---|-------|----------|------|------|------|
| 1 | Deprecated `datetime.utcnow()` in login | CRITICAL | auth.py | 377 | Deprecation |
| 2 | Non-functional dependencies module | CRITICAL | dependencies.py | 21-45 | Design Bug |
| 3 | Memory leak in session locks | HIGH | session.py | 226-244 | Memory Leak |
| 4 | JWT secret length not validated | HIGH | config.py | 56-61 | Security |
| 5 | Deprecated utcnow in schema | HIGH | schemas.py | 214 | Deprecation |
| 6 | Misleading IRT math comments | MEDIUM | irt.py | 38-48 | Documentation |
| 7 | Missing file error handling | MEDIUM | admin_server.py | 33-34 | Robustness |
| 8 | Database pool exhaustion risk | MEDIUM | main.py | 148-154 | Performance |
| 9 | Unused import | LOW | custom.py | 15-16 | Code Quality |
| 10 | Thread-unsafe logging | LOW | admin_server.py | 38-40 | Thread Safety |
| 11 | OTP not sent in production | LOW | auth.py | 409-432 | Feature Gap |
| 12 | Confusing level threshold docs | LOW | config.py | 127-133 | Documentation |

---

## 🔧 RECOMMENDED ACTION PLAN

**IMMEDIATE (Production-blocking):**
1. Fix Issue #1 - Update datetime usage
2. Fix Issue #2 - Restore working dependency injection
3. Fix Issue #4 - Add JWT secret validation

**SOON (High-priority):**
4. Fix Issue #3 - Prevent memory leak in locks
5. Fix Issue #5 - Update deprecated datetime in schema
6. Fix Issue #8 - Improve database pool configuration

**LATER (Code quality):**
7. Fix Issues #7, #9, #10, #11, #12 - Various improvements

---

**Report Generated:** 2026-04-14 23:59 UTC
