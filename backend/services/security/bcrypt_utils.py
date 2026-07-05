"""
bcrypt_utils.py — Password hashing utilities using bcrypt directly.

Provides hash_password() and verify_password() used by auth router.
Bypasses passlib to avoid compatibility issues with newer Python versions.
"""
import logging
import bcrypt

logger = logging.getLogger(__name__)


# Create a bcrypt hash for plaintext password storage.
def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt (12 rounds).

    Args:
        password: Plaintext string to hash

    Returns:
        Bcrypt hash string (e.g. "$2b$12$...")
    """
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')


# Verify plaintext password against a stored bcrypt hash string.
def verify_password(password: str, password_hash: str) -> bool:
    """Compare plaintext password against a stored bcrypt hash.

    Args:
        password: Plaintext password to verify
        password_hash: Stored bcrypt hash string

    Returns:
        True if password matches, False otherwise
    """
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except Exception as exc:
        logger.warning("Password verification error: %s", exc)
        return False
