"""Shared helpers for the AI day manager's tests: a migrated database, seeded
screener sweeps, and fake Claude transports. No test reaches the network."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

from tests.schema_support import migrate_database

ET = ZoneInfo("America/New_York")
SESSION = date(2026, 10, 13)


def et(hour: int, minute: int, second: int = 0, day: date = SESSION) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=ET).astimezone(timezone.utc)


def migrated(tmp_path: Path, monkeypatch, name: str = "manager.db") -> Path:
    """A database at the code's schema (16 included), set as the process database."""
    from config import cfg

    path = migrate_database(tmp_path / name)
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    return path


async def migrated_async(tmp_path: Path, monkeypatch, name: str = "manager.db") -> Path:
    """``migrated`` for async tests (no nested event loop)."""
    from config import cfg
    from db.core import init_db

    path = tmp_path / name
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    await init_db()
    return path


def sweep_row(con_id: int, symbol: str, *, hot_rank: int | None = None, score: str = "50", **metrics: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "con_id": con_id,
        "primary_exchange": "NYSE",
        "source": "watchlist",
        "last": metrics.get("last", "25.00"),
        "bid": None,
        "ask": None,
        "prior_close": "24.00",
        "open": "24.50",
        "day_high": "25.10",
        "day_low": "24.40",
        "change_pct": metrics.get("change_pct", "4.17"),
        "gap_pct": metrics.get("gap_pct", "2.08"),
        "volume_shares": "1000000",
        "rvol": metrics.get("rvol", "2.5"),
        "dollar_volume": "25000000",
        "pct_from_high": "0.4",
        "score": score,
        "hot": hot_rank is not None,
        "hot_rank": hot_rank,
    }


def seed_sweep(path: Path, rows: Sequence[Mapping[str, Any]], *, observed_at: datetime, day: date = SESSION) -> None:
    payload = {"schema": "screener.v1", "session_date": day.isoformat(), "observed_at": observed_at.isoformat(), "rows": list(rows)}
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO autotrader_screener_snapshots VALUES (?,?,?,?)",
            (f"sweep-{observed_at.timestamp():.0f}", day.isoformat(), observed_at.isoformat(), json.dumps(payload)),
        )
        for row in rows:
            db.execute(
                "INSERT OR IGNORE INTO autotrader_universe VALUES ('watchlist', ?, 'NYSE', ?, ?, 'NYSE', 'resolved', NULL, ?)",
                (row["symbol"], row["symbol"], row["con_id"], observed_at.isoformat()),
            )


def plan_answer(focus: Sequence[tuple[int, Sequence[str], str]], **steps: float) -> dict[str, Any]:
    running = {"momo": 1.0, "orb": 1.0, "vwap": 1.0, "gap": 1.0, **steps}
    return {
        "regime": "trend_up",
        "strategies": [{"strategy": name, "step": step, "reason": f"{name} step"} for name, step in running.items()],
        "focus": [{"con_id": con_id, "strategies": list(names), "note": note} for con_id, names, note in focus],
    }


class FakePlanTransport:
    """Answers with the queued responses (or raises / sleeps) and records each request."""

    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str]] = []

    async def plan(self, *, system: str, user: str):
        from trading.manager.planner import PlanResponse

        self.requests.append((system, user))
        item = self.responses.pop(0) if self.responses else self.responses_default()
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            await asyncio.sleep(float(item))
            raise AssertionError("the wrapper should have timed out")
        if isinstance(item, PlanResponse):
            return item
        return answered(item)  # type: ignore[arg-type]

    def responses_default(self) -> object:
        return plan_answer([])


def answered(answer: Mapping[str, Any] | None, *, stop_reason: str = "end_turn", cost: str = "0.1234"):
    from trading.manager.planner import PlanResponse

    return PlanResponse(
        answer=answer,
        stop_reason=stop_reason,
        served_model="claude-opus-5-5",
        input_tokens=6500,
        output_tokens=4200,
        cost_usd=Decimal(cost),
    )


async def no_bridge(_day: date):
    return (), None


def quiet_readers() -> dict[str, Any]:
    """Context readers with nothing outside the database (no files, no market)."""

    async def market(_day: date, *, now: datetime):
        return None

    return {
        "bridge_reader": lambda _day: ((), None),
        "replay_reader": lambda: {"available": False},
        "market_reader": market,
    }


def context_builder(**overrides: Any):
    from trading.manager.context import build_context

    readers = {**quiet_readers(), **overrides}

    async def build(session, *, now, slot, running):
        return await build_context(session, now=now, slot=slot, running=running, **readers)

    return build


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


__all__ = [
    "ET",
    "SESSION",
    "FakeClock",
    "FakePlanTransport",
    "answered",
    "context_builder",
    "et",
    "migrated",
    "migrated_async",
    "plan_answer",
    "quiet_readers",
    "seed_sweep",
    "sweep_row",
]
