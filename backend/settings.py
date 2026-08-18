"""
User settings — stored as a JSON blob in the users.settings column.
Uses deep merge so partial updates don't wipe unrelated keys.
"""
from __future__ import annotations

import json
import copy
import logging
from typing import Literal

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from pydantic_core import PydanticCustomError

from config import cfg

log = logging.getLogger(__name__)


class NotificationPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sound_enabled: StrictBool = True
    sound: Literal["ding", "chime", "alarm", "cash_register"] = "chime"
    volume: float = Field(default=0.6, ge=0.0, le=1.0)
    muted: StrictBool = False
    browser_push: StrictBool = False
    in_app: StrictBool = True


class NotificationPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sound_enabled: StrictBool | None = None
    sound: Literal["ding", "chime", "alarm", "cash_register"] | None = None
    volume: float | None = Field(default=None, ge=0.0, le=1.0)
    muted: StrictBool | None = None
    browser_push: StrictBool | None = None
    in_app: StrictBool | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_values(cls, value: object) -> object:
        if isinstance(value, dict) and any(item is None for item in value.values()):
            raise PydanticCustomError(
                "notification_preference_null",
                "notification preference values cannot be null",
            )
        return value

DEFAULT_SETTINGS: dict = {
    "theme": "dark",
    "default_symbol": "SPY",
    "default_bar_size": "1D",
    "bot_interval": 60,
    "watchlist": ["BTC-USD", "ETH-USD", "AAPL", "TSLA", "SPY", "QQQ", "NVDA"],
    "notifications": NotificationPreferences().model_dump(),
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
    try:
        saved = json.loads(row[0]) if row and row[0] else {}
    except (TypeError, json.JSONDecodeError):
        log.warning("Invalid settings JSON for user %s; using defaults", user_id)
        saved = {}
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


async def get_notification_preferences(user_id: str) -> NotificationPreferences:
    settings = await get_settings(user_id)
    try:
        return NotificationPreferences.model_validate(settings.get("notifications", {}))
    except ValueError:
        log.warning("Invalid notification preferences for user %s; disabling push", user_id)
        return NotificationPreferences()


async def update_notification_preferences(
    user_id: str, partial: NotificationPreferencesUpdate,
) -> NotificationPreferences:
    current = await get_notification_preferences(user_id)
    merged = current.model_copy(update=partial.model_dump(exclude_unset=True))
    validated = NotificationPreferences.model_validate(merged.model_dump())
    await update_settings(user_id, {"notifications": validated.model_dump()})
    return validated
