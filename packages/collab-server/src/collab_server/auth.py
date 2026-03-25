"""JWT-based authentication for the collaboration server.

Supports:
  - API key authentication (for CI/CD pipelines and MCP servers)
  - JWT bearer tokens (for VSCode extension users)
  - Role-based access: viewer / contributor / admin

Design doc reference: Phase 4 — 私有知识库企业部署方案
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

JWT_SECRET   = os.getenv("COLLAB_JWT_SECRET", secrets.token_hex(32))
JWT_ALGO     = "HS256"
JWT_EXPIRE_H = int(os.getenv("COLLAB_JWT_EXPIRE_HOURS", "168"))   # 7 days default

ROLES = {"viewer", "contributor", "admin"}

_bearer = HTTPBearer(auto_error=False)


def _require_jose():
    try:
        from jose import JWTError, jwt
        return jwt, JWTError
    except ImportError:
        raise RuntimeError("python-jose required: pip install python-jose[cryptography]")


def create_token(user_id: str, role: str = "contributor") -> str:
    """Issue a JWT for the given user."""
    jwt, _ = _require_jose()
    now = datetime.now(timezone.utc)
    payload = {
        "sub":  user_id,
        "role": role,
        "iat":  now,
        "exp":  now + timedelta(hours=JWT_EXPIRE_H),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT. Raises HTTPException on failure."""
    jwt, JWTError = _require_jose()
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        return payload
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        )


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hash of an API key for storage."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


# ── FastAPI dependency ────────────────────────────────────────────────────────

# In-memory API key store: hashed_key → {user_id, role}
# In production, back this with a database
_API_KEYS: dict[str, dict] = {}

# Seed from environment for single-node deployments
_env_key = os.getenv("COLLAB_API_KEY")
if _env_key:
    _API_KEYS[hash_api_key(_env_key)] = {"user_id": "env-user", "role": "admin"}


def register_api_key(raw_key: str, user_id: str, role: str = "contributor") -> None:
    """Register an API key (call at startup or via admin endpoint)."""
    _API_KEYS[hash_api_key(raw_key)] = {"user_id": user_id, "role": role}


def verify_api_key(raw_key: str) -> dict | None:
    """Verify a raw API key and return its user info, or None if invalid."""
    key_hash = hash_api_key(raw_key)
    return _API_KEYS.get(key_hash)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """FastAPI dependency: validates Bearer token (JWT or API key).

    Returns:
        {"user_id": str, "role": str}
    """
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authorization header required")

    token = credentials.credentials

    # Try API key first (constant-time compare)
    key_hash = hash_api_key(token)
    if key_hash in _API_KEYS:
        return _API_KEYS[key_hash]

    # Fall back to JWT
    return decode_token(token)


def require_role(minimum_role: str):
    """Dependency factory: require at least the given role."""
    role_order = {"viewer": 0, "contributor": 1, "admin": 2}

    async def _check(user: dict = Depends(get_current_user)) -> dict:
        user_role = user.get("role", "viewer")
        if role_order.get(user_role, 0) < role_order.get(minimum_role, 99):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{minimum_role}' required, got '{user_role}'",
            )
        return user

    return _check
