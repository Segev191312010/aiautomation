"""Screener presets CRUD and built-in preset seeding."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
import aiosqlite
from models import ScreenerPreset, ScanFilter, FilterValue
from db.core import get_db

log = logging.getLogger(__name__)

async def get_screener_presets(user_id: str = "demo") -> list[ScreenerPreset]:
    async with get_db() as db:
        async with db.execute(
            "SELECT data FROM screener_presets WHERE user_id=? OR built_in=1 ORDER BY built_in DESC, created_at",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [ScreenerPreset.model_validate(json.loads(r[0])) for r in rows]


async def save_screener_preset(preset: ScreenerPreset) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO screener_presets (id, user_id, name, data, built_in, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (preset.id, preset.user_id, preset.name, preset.model_dump_json(),
             1 if preset.built_in else 0, preset.created_at),
        )
        await db.commit()


async def delete_screener_preset(preset_id: str, user_id: str = "demo") -> bool:
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM screener_presets WHERE id=? AND user_id=? AND built_in=0",
            (preset_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Seed built-in screener presets
# ---------------------------------------------------------------------------

_BUILT_IN_PRESETS = [
    {
        "name": "RSI Oversold",
        "filters": [
            {
                "indicator": "RSI",
                "params": {"length": 14},
                "operator": "LT",
                "value": {"type": "number", "number": 30},
            }
        ],
    },
    {
        "name": "Golden Cross",
        "filters": [
            {
                "indicator": "SMA",
                "params": {"length": 50},
                "operator": "CROSSES_ABOVE",
                "value": {"type": "indicator", "indicator": "SMA", "params": {"length": 200}},
            }
        ],
    },
    {
        "name": "Volume Breakout",
        "filters": [
            {
                "indicator": "VOLUME",
                "params": {},
                "operator": "GT",
                "value": {"type": "indicator", "indicator": "VOLUME", "params": {"length": 20}, "multiplier": 2.0},
            }
        ],
    },
    {
        "name": "RSI Overbought",
        "filters": [
            {
                "indicator": "RSI",
                "params": {"length": 14},
                "operator": "GT",
                "value": {"type": "number", "number": 70},
            }
        ],
    },
    {
        "name": "Momentum Surge",
        "filters": [
            {
                "indicator": "RSI",
                "params": {"length": 14},
                "operator": "GT",
                "value": {"type": "number", "number": 60},
            },
            {
                "indicator": "EMA",
                "params": {"length": 20},
                "operator": "GT",
                "value": {"type": "indicator", "indicator": "EMA", "params": {"length": 50}},
            },
            {
                "indicator": "VOLUME",
                "params": {},
                "operator": "GT",
                "value": {"type": "indicator", "indicator": "VOLUME", "params": {"length": 20}, "multiplier": 1.5},
            },
        ],
    },
    {
        "name": "Bollinger Squeeze",
        "filters": [
            {
                "indicator": "ATR",
                "params": {"length": 14},
                "operator": "LT",
                "value": {"type": "number", "number": 2.0},
            },
            {
                "indicator": "RSI",
                "params": {"length": 14},
                "operator": "GTE",
                "value": {"type": "number", "number": 45},
            },
            {
                "indicator": "RSI",
                "params": {"length": 14},
                "operator": "LTE",
                "value": {"type": "number", "number": 60},
            },
        ],
    },
    {
        "name": "Trend Pullback",
        "filters": [
            {
                "indicator": "SMA",
                "params": {"length": 50},
                "operator": "GT",
                "value": {"type": "indicator", "indicator": "SMA", "params": {"length": 200}},
            },
            {
                "indicator": "RSI",
                "params": {"length": 14},
                "operator": "LT",
                "value": {"type": "number", "number": 45},
            },
            {
                "indicator": "PRICE",
                "params": {},
                "operator": "GT",
                "value": {"type": "indicator", "indicator": "SMA", "params": {"length": 200}},
            },
        ],
    },
    {
        "name": "Death Cross",
        "filters": [
            {
                "indicator": "SMA",
                "params": {"length": 50},
                "operator": "CROSSES_BELOW",
                "value": {"type": "indicator", "indicator": "SMA", "params": {"length": 200}},
            }
        ],
    },
    {
        "name": "High Relative Volume",
        "filters": [
            {
                "indicator": "VOLUME",
                "params": {},
                "operator": "GT",
                "value": {"type": "indicator", "indicator": "VOLUME", "params": {"length": 20}, "multiplier": 3.0},
            },
            {
                "indicator": "CHANGE_PCT",
                "params": {},
                "operator": "GT",
                "value": {"type": "number", "number": 2.0},
            },
        ],
    },
    {
        "name": "MACD Bullish Cross",
        "filters": [
            {
                "indicator": "MACD",
                "params": {"fast": 12, "slow": 26, "signal": 9},
                "operator": "CROSSES_ABOVE",
                "value": {"type": "number", "number": 0},
            },
            {
                "indicator": "PRICE",
                "params": {},
                "operator": "GT",
                "value": {"type": "indicator", "indicator": "SMA", "params": {"length": 200}},
            },
        ],
    },
    {
        "name": "Oversold Bounce",
        "filters": [
            {
                "indicator": "RSI",
                "params": {"length": 14},
                "operator": "GT",
                "value": {"type": "number", "number": 30},
            },
            {
                "indicator": "RSI",
                "params": {"length": 14},
                "operator": "LT",
                "value": {"type": "number", "number": 45},
            },
            {
                "indicator": "CHANGE_PCT",
                "params": {},
                "operator": "GT",
                "value": {"type": "number", "number": 0},
            },
        ],
    },
    {
        "name": "52W High Breakout",
        "filters": [
            {
                "indicator": "PRICE",
                "params": {},
                "operator": "GT",
                "value": {"type": "indicator", "indicator": "SMA", "params": {"length": 50}},
            },
            {
                "indicator": "SMA",
                "params": {"length": 20},
                "operator": "GT",
                "value": {"type": "indicator", "indicator": "SMA", "params": {"length": 50}},
            },
            {
                "indicator": "VOLUME",
                "params": {},
                "operator": "GT",
                "value": {"type": "indicator", "indicator": "VOLUME", "params": {"length": 20}, "multiplier": 1.5},
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Backtests CRUD
# ---------------------------------------------------------------------------

async def _seed_screener_presets(db: aiosqlite.Connection) -> None:
    # Get existing built-in names to upsert only what changed
    async with db.execute("SELECT name FROM screener_presets WHERE built_in=1") as cur:
        existing_names = {row[0] for row in await cur.fetchall()}

    target_names = {raw["name"] for raw in _BUILT_IN_PRESETS}

    # Remove built-ins no longer in the list
    for stale_name in existing_names - target_names:
        await db.execute(
            "DELETE FROM screener_presets WHERE name=? AND built_in=1",
            (stale_name,),
        )

    # Upsert each current built-in by name
    for raw in _BUILT_IN_PRESETS:
        if raw["name"] in existing_names:
            continue  # already seeded
        preset = ScreenerPreset(
            name=raw["name"],
            filters=[ScanFilter.model_validate(f) for f in raw["filters"]],
            built_in=True,
            user_id="demo",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        await db.execute(
            "INSERT INTO screener_presets (id, user_id, name, data, built_in, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (preset.id, preset.user_id, preset.name, preset.model_dump_json(), preset.created_at),
        )
    await db.commit()
