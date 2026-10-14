"""The owner's autopilot switch and its per-session latch.

The switch is an append-only event log (``autotrader_manager_events``), OFF
until an event turns it on. Only two deterministic paths write it: the
``autotraderctl manager on|off`` command and the Telegram command parser's
``/autopilot on|off``. No environment variable, route, config key or AI tool
can (a test pins the assistant's tools).

A managed family latches the switch once per session date
(``autotrader_manager_day_mode``, insert-or-ignore): the first read of the
day decides the day's identity and restarts reuse it. In this phase the latch
changes shadow behaviour only (managed MOMO follows the plan or stays
neutral); real MOMO_HOD_V2 is unchanged either way.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal

from db.core import get_db, transaction
from trading.manager.store import utc_text

SwitchAction = Literal["on", "off"]
SwitchSource = Literal["cli", "telegram"]
LATCH_MANAGED = "managed"
LATCH_V2 = "v2"


@dataclass(frozen=True, slots=True)
class SwitchState:
    state: SwitchAction
    event_id: int | None
    ts: datetime | None = None
    source: str | None = None
    actor: str | None = None
    reason: str | None = None

    @property
    def on(self) -> bool:
        return self.state == "on"


@dataclass(frozen=True, slots=True)
class DayLatch:
    session_date: date
    family: str
    mode: str
    latched_at: datetime
    switch_event_id: int | None
    created: bool

    @property
    def managed(self) -> bool:
        return self.mode == LATCH_MANAGED


_LATEST_EVENT = (
    "SELECT id, ts, action, source, actor, reason FROM autotrader_manager_events "
    "ORDER BY id DESC LIMIT 1"
)


def _state(row: object) -> SwitchState:
    if row is None:
        return SwitchState(state="off", event_id=None)
    event_id, ts, action, source, actor, reason = row  # type: ignore[misc]
    return SwitchState(
        state="on" if action == "on" else "off",
        event_id=int(event_id),
        ts=datetime.fromisoformat(str(ts)).astimezone(timezone.utc),
        source=str(source),
        actor=str(actor),
        reason=str(reason),
    )


async def read_switch() -> SwitchState:
    async with get_db() as db:
        async with db.execute(_LATEST_EVENT) as cursor:
            return _state(await cursor.fetchone())


async def set_switch(
    action: SwitchAction, *, source: SwitchSource, actor: str, reason: str, now: datetime
) -> SwitchState:
    """Append one switch event; the newest event is the switch."""
    if action not in ("on", "off"):
        raise ValueError("the autopilot switch is 'on' or 'off'")
    if source not in ("cli", "telegram"):
        raise ValueError("only the CLI and the Telegram command parser set the switch")
    actor_text = actor.strip()[:128] or "owner"
    reason_text = " ".join(reason.split())[:500] or f"autopilot {action}"
    async with transaction() as db:
        await db.execute(
            "INSERT INTO autotrader_manager_events (ts, action, source, actor, reason) VALUES (?, ?, ?, ?, ?)",
            (utc_text(now), action, source, actor_text, reason_text),
        )
        async with db.execute(_LATEST_EVENT) as cursor:
            return _state(await cursor.fetchone())


async def read_day_mode(session_date: date, family: str) -> DayLatch | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT mode, latched_at, switch_event_id FROM autotrader_manager_day_mode "
            "WHERE session_date = ? AND strategy_family = ?",
            (session_date.isoformat(), family),
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return None
    return DayLatch(
        session_date=session_date,
        family=family,
        mode=str(row[0]),
        latched_at=datetime.fromisoformat(str(row[1])).astimezone(timezone.utc),
        switch_event_id=None if row[2] is None else int(row[2]),
        created=False,
    )


async def latch_day_mode(session_date: date, family: str, *, now: datetime) -> DayLatch:
    """The session's latch for ``family``: written once from the switch, then reused.

    One transaction reads the switch and inserts the latch (insert or
    ignore), so two starts on one day cannot latch different modes, and a
    restart after a crash reads back the latch the first start wrote.
    """
    day = session_date.isoformat()
    async with transaction() as db:
        async with db.execute(_LATEST_EVENT) as cursor:
            switch = _state(await cursor.fetchone())
        mode = LATCH_MANAGED if switch.on else LATCH_V2
        cursor = await db.execute(
            """INSERT INTO autotrader_manager_day_mode
               (session_date, strategy_family, mode, latched_at, switch_event_id)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(session_date, strategy_family) DO NOTHING""",
            (day, family, mode, utc_text(now), switch.event_id),
        )
        created = cursor.rowcount == 1
        async with db.execute(
            "SELECT mode, latched_at, switch_event_id FROM autotrader_manager_day_mode "
            "WHERE session_date = ? AND strategy_family = ?",
            (day, family),
        ) as read:
            row = await read.fetchone()
    assert row is not None
    return DayLatch(
        session_date=session_date,
        family=family,
        mode=str(row[0]),
        latched_at=datetime.fromisoformat(str(row[1])).astimezone(timezone.utc),
        switch_event_id=None if row[2] is None else int(row[2]),
        created=created,
    )


__all__ = [
    "DayLatch",
    "LATCH_MANAGED",
    "LATCH_V2",
    "SwitchState",
    "latch_day_mode",
    "read_day_mode",
    "read_switch",
    "set_switch",
]
