"""Shared FastAPI limiter and dependency notes."""
from slowapi import Limiter
from slowapi.util import get_remote_address

# Global rate limiter used by routers through `@limiter.limit(...)`.
limiter = Limiter(key_func=get_remote_address)


# DB, Redis, current-user, and HTTP-client dependencies live in routers/auth.py
# and read initialized resources from app.state.
