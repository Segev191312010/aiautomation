"""
User settings — stored as a JSON blob in the users.settings column.
Uses deep merge so partial updates don't wipe unrelated keys.
"""
from __future__ import annotations

import json
import copy
import aiosqlite
from config import cfg

DEFAULT_SETTINGS: dict = {
    "theme": "dark",
    "default_symbol": "SPY",
    "default_bar_size": "1D",
    "bot_interval": 60,
    "watchlist": ["BTC-USD", "ETH-USD", "AAPL", "TSLA", "SPY", "QQQ", "NVDA"],
}


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge *overlay* into a copy of *base*."""
    result = copy.deepcopy(base)
    for key, val in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


async def get_settings(user_id: str) -> dict:
    """Return merged defaults + saved settings for a user."""
    async with aiosqlite.connect(cfg.DB_PATH) as db:
        async with db.execute(
            "SELECT settings FROM users WHERE id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
    saved = json.loads(row[0]) if row and row[0] else {}
    return _deep_merge(DEFAULT_SETTINGS, saved)


async def update_settings(user_id: str, partial: dict) -> dict:
    """Deep-merge partial update into existing settings and persist."""
    current = await get_settings(user_id)
    merged = _deep_merge(current, partial)
    async with aiosqlite.connect(cfg.DB_PATH) as db:
        await db.execute(
            "UPDATE users SET settings=? WHERE id=?",
            (json.dumps(merged), user_id),
        )
        await db.commit()
    return merged
