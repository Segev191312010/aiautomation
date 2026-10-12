"""Screener snapshot retention: compaction, expiry, VACUUM guards and the screener's daily job."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from trading.contracts import ShadowRuntimeConfig
from trading.market import retention
from trading.market.context import MarketContext, build_market_context
from trading.market.retention import (
    COMPACTED_KEY,
    COMPACTION_SCHEMA,
    HEADER_KEYS,
    RETENTION_STATE_FILENAME,
    RetentionReport,
    compact_payload,
    compact_snapshots,
    in_trading_day_quiet_window,
    is_compacted,
    next_retention_at,
    retention_cutoffs,
    retention_day,
    vacuum_block_reason,
)
from trading.market.screener import ScreenerPayload, with_market_context
from trading.market.snapshots import MarketSnapshot
from trading.market.tws_actor import TWSRequestError
from trading.operations import screener_service as service

ET = ZoneInfo("America/New_York")
MONDAY = date(2026, 10, 12)
SUNDAY = date(2026, 10, 11)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


@pytest.fixture
async def database(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db

    path = tmp_path / "retention.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    await init_db()
    return path


def screener_row(index: int, *, hot_rank: int | None) -> dict[str, Any]:
    change = Decimal("4.00") - Decimal(index) / Decimal(100)
    return {
        "symbol": f"S{index:03d}",
        "con_id": 10_000 + index,
        "primary_exchange": "NASDAQ",
        "source": "watchlist",
        "last": "101.25",
        "bid": "101.20",
        "ask": "101.30",
        "prior_close": "100.00",
        "open": "100.50",
        "day_high": "102.00",
        "day_low": "99.80",
        "change_pct": format(change, "f"),
        "gap_pct": "0.50",
        "volume_shares": "1250000",
        "rvol": "1.80",
        "dollar_volume": "126562500.00",
        "pct_from_high": "-0.74",
        "score": format(Decimal("0.9000") - Decimal(index) / Decimal(10_000), "f"),
        "hot": hot_rank is not None,
        "hot_rank": hot_rank,
    }


def sweep(
    day: date,
    observed: datetime,
    *,
    quoted: int = 40,
    hot: int = 5,
    with_market: bool = True,
) -> ScreenerPayload:
    """A screener.v1 sweep that passes the production payload contract."""
    payload = ScreenerPayload.model_validate(
        {
            "schema": "screener.v1",
            "session_date": day.isoformat(),
            "observed_at": observed.astimezone(timezone.utc).isoformat(timespec="microseconds"),
            "sweep_seconds": 58.4,
            "universe_size": quoted + 7,
            "qualified": quoted + 2,
            "quoted": quoted,
            "errors": 2,
            "rows": [
                screener_row(index, hot_rank=index + 1 if index < hot else None)
                for index in range(quoted)
            ],
        }
    )
    if not with_market:
        return payload
    context = build_market_context(
        session_date=day,
        observed_at=observed,
        etf_snapshots={},
        watchlist_rows=[row.model_dump() for row in payload.rows],
    )
    return with_market_context(payload, context)


async def insert(snapshot_id: str, day: date, observed: datetime, text: str) -> None:
    from db.core import transaction

    async with transaction() as db:
        await db.execute(
            "INSERT INTO autotrader_screener_snapshots VALUES (?, ?, ?, ?)",
            (
                snapshot_id,
                day.isoformat(),
                observed.astimezone(timezone.utc).isoformat(timespec="microseconds"),
                text,
            ),
        )


async def insert_sweep(snapshot_id: str, day: date, observed: datetime, **options: Any) -> str:
    text = sweep(day, observed, **options).to_json()
    await insert(snapshot_id, day, observed, text)
    return text


def stored(database: Path) -> dict[str, str]:
    with sqlite3.connect(database) as db:
        return dict(db.execute("SELECT id, payload_json FROM autotrader_screener_snapshots"))


def pragma(database: Path, name: str) -> int:
    with sqlite3.connect(database) as db:
        return int(db.execute(f"PRAGMA {name}").fetchone()[0])


def fixed(moment: datetime) -> Callable[[], datetime]:
    return lambda: moment


# --- the compacted document -------------------------------------------------------------


def test_compaction_keeps_the_header_hot_rows_and_market_verbatim() -> None:
    observed = at(date(2026, 10, 1), 10, 30)
    original = json.loads(sweep(date(2026, 10, 1), observed, quoted=40, hot=5).to_json())

    compacted = compact_payload(original, compacted_at=at(MONDAY, 16, 10))

    assert compacted is not None
    assert list(compacted) == [*HEADER_KEYS, "rows", "market", COMPACTED_KEY]
    assert {key: compacted[key] for key in HEADER_KEYS} == {key: original[key] for key in HEADER_KEYS}
    assert compacted["rows"] == original["rows"][:5]
    assert all(row["hot"] is True for row in compacted["rows"])
    assert compacted["market"] == original["market"]
    assert compacted[COMPACTED_KEY] == {
        "schema": COMPACTION_SCHEMA,
        "rows_kept": 5,
        "rows_dropped": 35,
        "compacted_at": "2026-10-12T20:10:00.000000+00:00",
    }
    # The counts describe the sweep, not the kept rows, so strict readers refuse
    # a compacted snapshot instead of mistaking it for a full sweep...
    assert compacted["quoted"] == 40
    with pytest.raises(ValidationError):
        ScreenerPayload.model_validate(compacted)
    # ...while its market context stays a valid market_context.v1 document.
    assert MarketContext.from_payload(compacted["market"]).session_date == date(2026, 10, 1)
    assert is_compacted(compacted) and not is_compacted(original)


def test_only_full_screener_v1_sweeps_are_compacted() -> None:
    now = at(MONDAY, 16, 10)
    plain = json.loads(sweep(date(2026, 10, 1), at(date(2026, 10, 1), 9, 0), with_market=False).to_json())

    without_market = compact_payload(plain, compacted_at=now)
    assert without_market is not None and "market" not in without_market

    assert compact_payload(without_market, compacted_at=now) is None
    assert compact_payload({**plain, "schema": "screener.v2"}, compacted_at=now) is None
    assert compact_payload({**plain, "rows": "garbage"}, compacted_at=now) is None
    with pytest.raises(ValueError):
        compact_payload(plain, compacted_at=datetime(2026, 10, 12, 16, 10))


# --- schedule and guards ------------------------------------------------------------------


def test_the_daily_job_falls_due_at_1605_new_york_time() -> None:
    assert retention_day(at(MONDAY, 16, 4)) == SUNDAY
    assert retention_day(at(MONDAY, 16, 5)) == MONDAY
    assert next_retention_at(at(MONDAY, 7, 55)) == at(MONDAY, 16, 5)
    assert next_retention_at(at(MONDAY, 16, 5)) == at(date(2026, 10, 13), 16, 5)
    # Across the November DST change the job stays at 16:05 local time.
    after_dst = next_retention_at(at(date(2026, 10, 31), 17, 0))
    assert after_dst == at(date(2026, 11, 1), 16, 5)
    assert after_dst.utcoffset() == timedelta(hours=-5)
    assert retention_cutoffs(at(MONDAY, 16, 10)) == (date(2026, 10, 2), date(2026, 6, 14))
    with pytest.raises(ValueError):
        retention_day(datetime(2026, 10, 12, 16, 10))


def test_vacuum_runs_on_sunday_at_most_once_a_week() -> None:
    sunday = at(SUNDAY, 16, 10)
    assert vacuum_block_reason(sunday, None) is None
    assert vacuum_block_reason(sunday, SUNDAY - timedelta(days=7)) is None
    assert vacuum_block_reason(sunday, SUNDAY) == "already_this_week"
    assert vacuum_block_reason(sunday, SUNDAY - timedelta(days=6)) == "already_this_week"
    assert vacuum_block_reason(at(MONDAY, 20, 0), None) == "not_vacuum_day"
    assert vacuum_block_reason(at(date(2026, 10, 10), 20, 0), None) == "not_vacuum_day"


def test_vacuum_never_runs_in_0755_1615_on_a_trading_day(monkeypatch) -> None:
    # Pin the guard itself: allow Mondays and Thursdays, then probe the hours.
    monkeypatch.setattr(retention, "VACUUM_WEEKDAY", MONDAY.weekday())
    assert vacuum_block_reason(at(MONDAY, 7, 54), None) is None
    assert vacuum_block_reason(at(MONDAY, 7, 55), None) == "trading_hours"
    assert vacuum_block_reason(at(MONDAY, 12, 0), None) == "trading_hours"
    assert vacuum_block_reason(at(MONDAY, 16, 14), None) == "trading_hours"
    assert vacuum_block_reason(at(MONDAY, 16, 15), None) is None
    thanksgiving = date(2026, 11, 26)
    monkeypatch.setattr(retention, "VACUUM_WEEKDAY", thanksgiving.weekday())
    assert in_trading_day_quiet_window(at(thanksgiving, 10, 0)) is False
    assert vacuum_block_reason(at(thanksgiving, 10, 0), None) is None
    assert retention.TRADING_DAY_QUIET_WINDOW == (
        datetime.strptime("07:55", "%H:%M").time(),
        datetime.strptime("16:15", "%H:%M").time(),
    )


def test_an_unreadable_calendar_counts_as_a_trading_day(monkeypatch) -> None:
    def broken(_: date) -> None:
        raise RuntimeError("exchange calendar unavailable")

    monkeypatch.setattr(retention, "market_session", broken)
    assert in_trading_day_quiet_window(at(SUNDAY, 10, 0)) is True
    assert vacuum_block_reason(at(SUNDAY, 10, 0), None) == "trading_hours"
    assert vacuum_block_reason(at(SUNDAY, 17, 0), None) is None


# --- the job on a seeded database ------------------------------------------------------------


@pytest.mark.asyncio
async def test_retention_compacts_after_10_days_and_deletes_after_120(database) -> None:
    now = at(MONDAY, 16, 10)
    keep = {
        "today": await insert_sweep("today", MONDAY, at(MONDAY, 10, 0)),
        "day-10": await insert_sweep("day-10", date(2026, 10, 2), at(date(2026, 10, 2), 15, 0)),
    }
    old_day = date(2026, 10, 1)
    originals = {
        f"day-11-{index}": await insert_sweep(
            f"day-11-{index}", old_day, at(old_day, 9 + index, 0), with_market=index != 2
        )
        for index in range(3)
    }
    await insert_sweep("day-120", date(2026, 6, 14), at(date(2026, 6, 14), 11, 0))
    expired = date(2026, 6, 13)
    await insert_sweep("day-121-full", expired, at(expired, 11, 0))
    earlier = compact_payload(
        json.loads(sweep(expired, at(expired, 10, 0)).to_json()), compacted_at=at(date(2026, 6, 25), 16, 5)
    )
    await insert("day-121-compacted", expired, at(expired, 10, 0), json.dumps(earlier))
    unknown = json.dumps({"schema": "screener.v0", "session_date": expired.isoformat(), "rows": []})
    await insert("day-121-unknown", expired, at(expired, 12, 0), unknown)

    report = await compact_snapshots(now, clock=fixed(now))

    assert isinstance(report, RetentionReport)
    assert (report.compact_before, report.delete_before) == (date(2026, 10, 2), date(2026, 6, 14))
    assert report.sessions_examined == 3
    assert (report.compacted, report.skipped, report.deleted) == (5, 1, 2)
    assert report.rows_dropped == 5 * 35
    assert report.bytes_after < report.bytes_before
    assert report.complete is True
    assert (report.vacuumed, report.vacuum_skipped) == (False, "not_vacuum_day")
    assert "compacted 5 snapshots" in report.summary()

    rows = stored(database)
    # Ten days and younger are byte-for-byte untouched.
    for snapshot_id, text in keep.items():
        assert rows[snapshot_id] == text
    for snapshot_id, text in originals.items():
        before, after = json.loads(text), json.loads(rows[snapshot_id])
        assert after == compact_payload(before, compacted_at=now)
        assert ("market" in after) is ("market" in before)
    assert is_compacted(json.loads(rows["day-120"]))
    # Older than 120 days: compacted snapshots are gone, an unrecognised one is kept.
    assert {"day-121-full", "day-121-compacted"}.isdisjoint(rows)
    assert rows["day-121-unknown"] == unknown

    state_path = database.parent / RETENTION_STATE_FILENAME
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_vacuum_on"] is None
    assert state["last_run_at"] == "2026-10-12T20:10:00.000000+00:00"
    assert state_path.stat().st_mode & 0o777 == 0o600

    again = await compact_snapshots(now + timedelta(hours=1), clock=fixed(now))
    assert (again.compacted, again.deleted, again.skipped) == (0, 0, 1)
    assert stored(database) == rows


@pytest.mark.asyncio
async def test_a_deadline_stops_compaction_oldest_first_and_the_next_run_resumes(
    database, monkeypatch
) -> None:
    monkeypatch.setattr(retention, "COMPACT_BATCH_SIZE", 2)
    old_day = date(2026, 9, 30)
    for index in range(5):
        await insert_sweep(f"s{index}", old_day, at(old_day, 10, index))
    now = at(MONDAY, 7, 55)
    ticks = iter(now + timedelta(minutes=minute) for minute in range(100))

    # The first batch starts before the deadline; the clock has passed it by the second.
    partial = await compact_snapshots(
        now, clock=lambda: next(ticks), deadline=now + timedelta(seconds=30)
    )

    assert (partial.compacted, partial.complete) == (2, False)
    assert partial.vacuum_skipped == "compaction_incomplete"
    assert "stopped at its deadline" in partial.summary()
    compacted = {key for key, text in stored(database).items() if is_compacted(json.loads(text))}
    assert compacted == {"s0", "s1"}

    resumed = await compact_snapshots(now, clock=fixed(now))
    assert (resumed.compacted, resumed.complete) == (3, True)
    assert all(is_compacted(json.loads(text)) for text in stored(database).values())


@pytest.mark.asyncio
async def test_sunday_vacuum_reclaims_space_once_a_week(database) -> None:
    old_day = date(2026, 9, 20)
    for index in range(12):
        await insert_sweep(f"big-{index}", old_day, at(old_day, 10, index), quoted=400, hot=10)
    pages_before = pragma(database, "page_count")
    sunday = at(SUNDAY, 16, 10)

    report = await compact_snapshots(sunday, clock=fixed(sunday))

    assert report.compacted == 12
    assert (report.vacuumed, report.vacuum_skipped) == (True, None)
    assert pragma(database, "freelist_count") == 0
    assert pragma(database, "page_count") < pages_before / 2
    state = json.loads((database.parent / RETENTION_STATE_FILENAME).read_text(encoding="utf-8"))
    assert state["last_vacuum_on"] == "2026-10-11"
    assert "vacuumed" in report.summary()

    later = at(SUNDAY, 21, 0)
    same_week = await compact_snapshots(later, clock=fixed(later))
    assert (same_week.vacuumed, same_week.vacuum_skipped) == (False, "already_this_week")

    next_sunday = at(date(2026, 10, 18), 16, 10)
    week_after = await compact_snapshots(next_sunday, clock=fixed(next_sunday))
    # The weekly guard passes again; there is just nothing worth reclaiming.
    assert week_after.vacuum_skipped == "not_worthwhile"


@pytest.mark.asyncio
async def test_vacuum_is_rechecked_on_the_live_clock(database) -> None:
    old_day = date(2026, 9, 20)
    for index in range(6):
        await insert_sweep(f"big-{index}", old_day, at(old_day, 10, index), quoted=400)
    sunday_night = at(SUNDAY, 23, 59)

    # Compaction ran past midnight: on Monday VACUUM must not start.
    report = await compact_snapshots(sunday_night, clock=fixed(at(MONDAY, 0, 1)))

    assert report.compacted == 6
    assert report.vacuum_skipped == "not_vacuum_day"
    state = json.loads((database.parent / RETENTION_STATE_FILENAME).read_text(encoding="utf-8"))
    assert state["last_vacuum_on"] is None


@pytest.mark.asyncio
async def test_vacuum_needs_disk_headroom(database, monkeypatch) -> None:
    old_day = date(2026, 9, 20)
    for index in range(6):
        await insert_sweep(f"big-{index}", old_day, at(old_day, 10, index), quoted=400)

    class Usage:
        free = 0

    monkeypatch.setattr(retention.shutil, "disk_usage", lambda path: Usage())
    sunday = at(SUNDAY, 16, 10)
    report = await compact_snapshots(sunday, clock=fixed(sunday))

    assert report.vacuum_skipped == "insufficient_disk"
    assert pragma(database, "freelist_count") > 0


@pytest.mark.asyncio
async def test_an_unreadable_state_blocks_vacuum_and_is_replaced(database) -> None:
    state_path = database.parent / RETENTION_STATE_FILENAME
    state_path.write_text("{not json", encoding="utf-8")
    sunday = at(SUNDAY, 16, 10)

    report = await compact_snapshots(sunday, clock=fixed(sunday))

    assert report.vacuum_skipped == "state_unreadable"
    repaired = json.loads(state_path.read_text(encoding="utf-8"))
    assert repaired["schema"] == "screener_retention_state.v1"
    assert repaired["last_vacuum_on"] is None


@pytest.mark.asyncio
async def test_vacuum_can_be_left_out(database) -> None:
    sunday = at(SUNDAY, 16, 10)
    report = await compact_snapshots(sunday, clock=fixed(sunday), vacuum=False)
    assert (report.vacuumed, report.vacuum_skipped) == (False, "not_requested")


# --- the screener's daily job ---------------------------------------------------------------


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start.astimezone(timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return self.now.timestamp()

    async def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class QuietActor:
    """A TWS double with an empty universe: sweeps succeed with no rows."""

    def __init__(self) -> None:
        self.connected = False
        self.last_error: str | None = None

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def contract_details_for_symbol(self, symbol: str, primary_exchange: str | None) -> Any:
        raise TWSRequestError("no contract details in this test")

    async def daily_trade_bars(self, con_id: int) -> Any:
        raise TWSRequestError("no history in this test")

    async def market_snapshots(
        self, con_ids: Any, *, timeout_seconds: float = 3.0
    ) -> list[MarketSnapshot]:
        return []

    async def screener_scan(self, scan_code: str) -> tuple[Any, ...]:
        return ()


def runtime_config() -> ShadowRuntimeConfig:
    return ShadowRuntimeConfig(
        paper_account_id="DU1234567",
        data_client_id=17,
        order_client_id=18,
        model_id="claude-opus-5-5",
        prompt_id="orb-review-v1",
        calendar_id="XNYS",
        calendar_version="4.11.2",
        commission_policy_id="ibkr-fixed",
        commission_policy_version=1,
    )


def recorder(calls: list[tuple[datetime, datetime | None]]) -> Any:
    """A stand-in for compact_snapshots that records when the screener ran it."""

    async def fake(
        now: datetime, *, clock: Any = None, deadline: datetime | None = None, vacuum: bool = True
    ) -> RetentionReport:
        calls.append((now, deadline))
        compact_before, delete_before = retention_cutoffs(now)
        return RetentionReport(
            ran_at=now,
            compact_before=compact_before,
            delete_before=delete_before,
            sessions_examined=0,
            compacted=0,
            skipped=0,
            rows_dropped=0,
            bytes_before=0,
            bytes_after=0,
            deleted=0,
            complete=True,
            vacuumed=False,
            vacuum_skipped="not_vacuum_day",
        )

    return fake


async def run_loop(start: datetime, *, max_sweeps: int = 1) -> FakeClock:
    clock = FakeClock(start)
    await service.run_screener(
        runtime_config=runtime_config(),
        actor_factory=lambda config: QuietActor(),
        clock=clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        max_sweeps=max_sweeps,
        include_scans=False,
    )
    return clock


@pytest.mark.asyncio
async def test_screener_runs_retention_once_a_day_after_1605_and_never_in_the_window(
    database, monkeypatch
) -> None:
    calls: list[tuple[datetime, datetime | None]] = []
    monkeypatch.setattr(service, "compact_snapshots", recorder(calls))

    clock = await run_loop(at(MONDAY, 16, 1), max_sweeps=2)

    tuesday_open = at(date(2026, 10, 13), 8, 0)
    # 16:01 owes Sunday's job (a fresh process catches up), 16:05 owes Monday's;
    # both may run until the next window opens. Nothing runs during the window.
    assert calls == [(at(MONDAY, 16, 1), tuesday_open), (at(MONDAY, 16, 5), tuesday_open)]
    assert clock.now >= tuesday_open


@pytest.mark.asyncio
async def test_a_supervised_screener_catches_up_before_the_window_opens(database, monkeypatch) -> None:
    calls: list[tuple[datetime, datetime | None]] = []
    monkeypatch.setattr(service, "compact_snapshots", recorder(calls))

    await run_loop(at(date(2026, 10, 13), 7, 55))

    assert calls == [(at(date(2026, 10, 13), 7, 55), at(date(2026, 10, 13), 8, 0))]


@pytest.mark.asyncio
async def test_a_failing_retention_job_never_stops_the_screener(database, monkeypatch, caplog) -> None:
    async def broken(*args: Any, **kwargs: Any) -> RetentionReport:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(service, "compact_snapshots", broken)
    caplog.set_level("ERROR", logger="autotrader.shadow.screener")

    clock = await run_loop(at(MONDAY, 17, 0))

    assert clock.now >= at(date(2026, 10, 13), 8, 0)
    assert "screener snapshot retention failed" in caplog.text


@pytest.mark.asyncio
async def test_the_screener_job_compacts_old_sessions_in_the_database(database) -> None:
    old_day = date(2026, 9, 25)
    original = await insert_sweep("old", old_day, at(old_day, 11, 0))

    await run_loop(at(MONDAY, 16, 30))

    compacted = json.loads(stored(database)["old"])
    assert compacted == compact_payload(json.loads(original), compacted_at=at(MONDAY, 16, 30))
    assert os.path.exists(database.parent / RETENTION_STATE_FILENAME)
