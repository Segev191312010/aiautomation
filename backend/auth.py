"""
Authentication scaffold — JWT tokens + demo user.

This is a lightweight auth layer. The demo user auto-authenticates
when no token is provided (for development convenience).
Full login/registration UI comes in Stage 8.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import bcrypt
import aiosqlite
from fastapi import Depends, HTTPException, Request
from jose import JWTError, jwt

from config import cfg
from models import User


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

DEMO_USER_ID = "demo"
DEMO_EMAIL = "demo@local"
DEMO_PASSWORD = "demo"


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def create_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=cfg.JWT_ACCESS_EXPIRE_MINUTES)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, cfg.JWT_SECRET, algorithm=cfg.JWT_ALGORITHM)


def verify_token(token: str) -> str | None:
    """Return user_id if valid, None otherwise."""
    try:
        payload = jwt.decode(token, cfg.JWT_SECRET, algorithms=[cfg.JWT_ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

async def _get_user_row(db: aiosqlite.Connection, user_id: str) -> dict | None:
    async with db.execute(
        "SELECT id, email, password_hash, created_at, settings FROM users WHERE id=?",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "email": row[1],
        "password_hash": row[2],
        "created_at": row[3],
        "settings": row[4],
    }


async def seed_demo_user(db: aiosqlite.Connection) -> None:
    """Create the demo user if it doesn't exist yet."""
    existing = await _get_user_row(db, DEMO_USER_ID)
    if existing:
        return
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO users (id, email, password_hash, created_at, settings) VALUES (?, ?, ?, ?, ?)",
        (
            DEMO_USER_ID,
            DEMO_EMAIL,
            _hash_password(DEMO_PASSWORD),
            now,
            json.dumps({}),
        ),
    )
    await db.commit()


async def get_user(user_id: str) -> User | None:
    async with aiosqlite.connect(cfg.DB_PATH) as db:
        row = await _get_user_row(db, user_id)
    if not row:
        return None
    settings = json.loads(row["settings"]) if row["settings"] else {}
    return User(
        id=row["id"],
        email=row["email"],
        created_at=row["created_at"],
        settings=settings,
    )


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

async def get_current_user(request: Request) -> User:
    """
    Extract user from Authorization header.
    Falls back to demo user if no token is provided.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        user_id = verify_token(token)
        if user_id:
            user = await get_user(user_id)
            if user:
                return user
            raise HTTPException(401, "User not found")
        raise HTTPException(401, "Invalid or expired token")

    # No token → return demo user
    user = await get_user(DEMO_USER_ID)
    if not user:
        raise HTTPException(500, "Demo user not initialized")
    return user
