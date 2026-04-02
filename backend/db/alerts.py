"""Alerts CRUD — alert configuration and firing history."""
from __future__ import annotations
import json
import logging
from models import Alert, AlertHistory
from db.core import get_db

log = logging.getLogger(__name__)

async def get_alerts(user_id: str = "demo", enabled_only: bool = False) -> list[Alert]:
    """Fetch alerts for a specific user."""
    async with get_db() as db:
        if enabled_only:
            sql = "SELECT data FROM alerts WHERE user_id=? AND enabled=1"
        else:
            sql = "SELECT data FROM alerts WHERE user_id=?"
        async with db.execute(sql, (user_id,)) as cursor:
            rows = await cursor.fetchall()
    return [Alert.model_validate(json.loads(r[0])) for r in rows]


async def get_enabled_alerts_all() -> list[Alert]:
    """Fetch all enabled alerts across all users (for alert engine)."""
    async with get_db() as db:
        async with db.execute("SELECT data FROM alerts WHERE enabled=1") as cursor:
            rows = await cursor.fetchall()
    return [Alert.model_validate(json.loads(r[0])) for r in rows]


async def get_alert(alert_id: str, user_id: str = "demo") -> Alert | None:
    """Fetch a single alert by id, scoped to user."""
    async with get_db() as db:
        async with db.execute(
            "SELECT data FROM alerts WHERE id=? AND user_id=?", (alert_id, user_id)
        ) as cur:
            row = await cur.fetchone()
    return Alert.model_validate(json.loads(row[0])) if row else None


async def save_alert(alert: Alert, user_id: str = "demo") -> None:
    """Insert or replace an alert."""
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO alerts (id, user_id, symbol, enabled, data) VALUES (?, ?, ?, ?, ?)",
            (alert.id, user_id, alert.symbol.upper(), 1 if alert.enabled else 0,
             alert.model_dump_json()),
        )
        await db.commit()


async def delete_alert(alert_id: str, user_id: str = "demo") -> bool:
    """Delete an alert. Returns True if a row was removed."""
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM alerts WHERE id=? AND user_id=?", (alert_id, user_id)
        )
        await db.commit()
        return cur.rowcount > 0


async def get_alert_history(
    user_id: str = "demo", limit: int = 100, alert_id: str | None = None
) -> list[AlertHistory]:
    """Fetch alert history entries, newest first."""
    async with get_db() as db:
        if alert_id:
            sql = (
                "SELECT data FROM alert_history "
                "WHERE user_id=? AND alert_id=? ORDER BY fired_at DESC LIMIT ?"
            )
            params: tuple = (user_id, alert_id, limit)
        else:
            sql = (
                "SELECT data FROM alert_history "
                "WHERE user_id=? ORDER BY fired_at DESC LIMIT ?"
            )
            params = (user_id, limit)
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
    return [AlertHistory.model_validate(json.loads(r[0])) for r in rows]


async def save_alert_history(entry: AlertHistory, user_id: str = "demo") -> None:
    """Persist a fired-alert history entry."""
    async with get_db() as db:
        await db.execute(
            "INSERT INTO alert_history (id, alert_id, user_id, symbol, fired_at, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (entry.id, entry.alert_id, user_id, entry.symbol,
             entry.fired_at, entry.model_dump_json()),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Open positions CRUD (exit tracker)
# ---------------------------------------------------------------------------
