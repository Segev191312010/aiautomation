"""The shared five-minute bar cache (build only, flag off).

Golden: MOMO_HOD_V2's recorded 2026-10-12 day (tests/test_momo_v2.py
fixtures) decides byte-identically with the cache off (the default), on as
the first child (the writer) and on as a child behind it (a reader); a
reader's freshness refusals are the ones a direct fetch would get; a failing
cache is a direct fetch announced once; two children racing on one key make
one request and read the same bars; a synthetic five-child day counts the
requests the cache saves.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_momo_v2 import (
    BF_B,
    FIGS,
    PRIOR,
    SESSION,
    _decision_projection,
    _et,
    _proposal,
    _setup_projection,
)
from tests.test_shadow_service import TUESDAY, et, runtime_config
from trading.contracts import (
    AccountSnapshot,
    AIReview,
    Candidate,
    CompletedBar,
    QuoteSnapshot,
    RiskPolicy,
    RuleSetup,
    Run,
    ScannerRun,
    fingerprint_payload,
)
from trading.market import bar_cache
from trading.market.bar_cache import (
    BAR_CACHE_TABLE,
    BAR_CACHE_UNAVAILABLE,
    CLAIM_STALE_AFTER,
    BarCache,
    CachedBarsActor,
    bar_from_payload,
    bar_to_payload,
    cacheable_bars,
    completed_bar_end,
)
from trading.market.ibkr_boundary import (
    FAULT_STALE,
    BarBatch,
    IBKRContractDetails,
    IBKRTradeBar,
    LiquidityProfile,
    MarketDataQualityError,
    PaperIBKRMarketData,
)
from trading.operations import shadow_service
from trading.operations.shadow_service import (
    MANAGED_BAR_MAX_AGE,
    MIN_POLL_SEPARATION,
    STRATEGIES,
    STRATEGY_POLL_OFFSETS,
    WATCHLIST_BAR_MAX_AGE,
    bar_cache_active,
    poll_schedule,
    strategy_entry_window,
    uses_bar_cache,
)
from trading.orchestration import RepositoryBackedShadowRunner, ShadowPipeline
from trading.orchestration.session_runner import (
    BAR_INTERVAL,
    SessionReport,
    ShadowSessionRunner,
    ensure_strategy,
    momo_strategy_spec,
)
from trading.rules.momo import MomoConfig, MomoStrategy
from trading.scheduler.sessions import market_session
from trading.storage.manager_schema import (
    MANAGER_PHASE3_DRAFT_DDL,
    MANAGER_PHASE3_DRAFT_TABLES,
)
from trading.storage.migrations import MIGRATIONS
from trading.storage.repository import list_decisions

LOGGER = "autotrader.shadow.bar_cache"
CON_ID = 4931
TICK = Decimal("0.01")


# --------------------------------------------------------------------------
# Helpers: a scratch cache database, raw bars, the recorded day as TWS replies
# --------------------------------------------------------------------------


def _scratch_cache(path: Path) -> str:
    """A scratch database holding only the draft cache table."""
    connection = sqlite3.connect(path)
    for statement in MANAGER_PHASE3_DRAFT_DDL:
        connection.execute(statement)
    connection.commit()
    connection.close()
    return str(path)


def _rows(path: str) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(
            f"SELECT con_id, bar_end_utc, state, writer, bar_count FROM {BAR_CACHE_TABLE}"
            " ORDER BY con_id, bar_end_utc"
        ).fetchall()
    finally:
        connection.close()


def _raw(bar: CompletedBar, received_at: datetime) -> IBKRTradeBar:
    return IBKRTradeBar(
        con_id=bar.con_id,
        interval_start_utc=bar.interval_start_utc,
        interval_end_utc=bar.interval_end_utc,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        rth=bar.rth,
        received_at=received_at,
    )


def _quiet_bar(con_id: int, end: datetime, received_at: datetime, **flags: Any) -> IBKRTradeBar:
    return IBKRTradeBar(
        con_id=con_id,
        interval_start_utc=end - BAR_INTERVAL,
        interval_end_utc=end,
        open=Decimal("100"),
        high=Decimal("100.5"),
        low=Decimal("99.5"),
        close=Decimal("100.25"),
        volume=Decimal("1000"),
        rth=True,
        received_at=received_at,
        **flags,
    )


def _quiet_batch(con_id: int, bar_end: datetime, received_at: datetime, *, count: int = 3) -> BarBatch:
    """``count`` completed quiet bars ending at ``bar_end`` plus the forming one."""
    ends = [bar_end - BAR_INTERVAL * k for k in reversed(range(count))]
    bars = [_quiet_bar(con_id, end, received_at) for end in ends]
    bars.append(_quiet_bar(con_id, bar_end + BAR_INTERVAL, received_at))
    return BarBatch(bars=tuple(bars))


class ReplayActor:
    """TWS history replies for BF B and FIGS from the recorded day.

    Each reply holds the bars completed at the clock, ``latency`` late, like
    the live actor; quotes are the fixture's.
    """

    def __init__(self, clock: dict[str, datetime], *, latency: timedelta = timedelta(0)) -> None:
        self.clock = clock
        self.latency = latency
        self.requests: list[tuple[int, datetime]] = []
        self.by_con_id = {replay.con_id: replay for replay in (BF_B, FIGS)}

    async def completed_trade_bars(self, con_id: int) -> BarBatch:
        self.requests.append((con_id, self.clock["now"]))
        self.clock["now"] += self.latency
        received = self.clock["now"]
        replay = self.by_con_id[con_id]
        return BarBatch(bars=tuple(_raw(bar, received) for bar in replay.bars_until(received)))

    async def contract_details(self, con_id: int) -> IBKRContractDetails:
        replay = self.by_con_id[con_id]
        return IBKRContractDetails(
            con_id=con_id,
            symbol=replay.symbol,
            currency="USD",
            security_type="STK",
            stock_type="COMMON",
            primary_exchange="NYSE",
            min_tick=TICK,
        )

    async def quote_snapshot(self, con_id: int, *, min_tick: Decimal) -> QuoteSnapshot:
        assert min_tick == TICK
        return self.by_con_id[con_id].quote(self.clock["now"])

    async def supports_scanner(self, request: Any) -> bool:
        raise AssertionError("MOMO takes the hot list, not a scan")

    async def scan(self, request: Any) -> Any:
        raise AssertionError("MOMO takes the hot list, not a scan")

    async def extended_trade_bars(self, con_id: int) -> BarBatch:
        raise AssertionError("MOMO never asks for extended-hours bars")

    async def daily_trade_bars(self, con_id: int) -> Any:
        raise AssertionError("the hot list seeds liquidity")


def _cached(
    actor: Any,
    *,
    writer: str,
    clock: dict[str, datetime],
    db_path: str,
    sleep: Any = None,
) -> CachedBarsActor:
    now = lambda: clock["now"]  # noqa: E731 - the shared simulated clock
    cache = BarCache(writer=writer, clock=now, db_path=db_path, **({"sleep": sleep} if sleep else {}))
    return CachedBarsActor(actor, cache=cache, session_date=SESSION, clock=now)


# --------------------------------------------------------------------------
# 1. Pins: the flag, the registry, the claim window, the draft table
# --------------------------------------------------------------------------


def test_the_cache_is_off_and_every_registry_entry_opts_out():
    assert bar_cache.BAR_CACHE_ENABLED is False
    assert shadow_service.STRATEGY_USES_BAR_CACHE == {name: False for name in STRATEGIES}
    for name in STRATEGIES:
        assert not uses_bar_cache(name)
        assert not bar_cache_active(name)
    assert not uses_bar_cache("unknown")
    # A claim is abandoned once IBKR would accept the same request again.
    assert CLAIM_STALE_AFTER == MIN_POLL_SEPARATION == timedelta(seconds=15)


def test_the_flag_alone_activates_nothing_and_the_opt_in_alone_neither(monkeypatch):
    monkeypatch.setitem(shadow_service.STRATEGY_USES_BAR_CACHE, "orb-v2", True)
    assert uses_bar_cache("orb-v2") and not bar_cache_active("orb-v2")
    monkeypatch.setattr(bar_cache, "BAR_CACHE_ENABLED", True)
    assert bar_cache_active("orb-v2")
    assert [name for name in STRATEGIES if bar_cache_active(name)] == ["orb-v2"]


def test_the_draft_table_is_registered_by_no_migration():
    for migration in MIGRATIONS:
        assert BAR_CACHE_TABLE not in "\n".join(migration.schema_payload), migration.version
    assert MANAGER_PHASE3_DRAFT_TABLES == (BAR_CACHE_TABLE,)
    assert all(BAR_CACHE_TABLE in statement for statement in MANAGER_PHASE3_DRAFT_DDL)


async def test_a_migrated_database_has_no_cache_table(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "migrated.db"))
    await init_db()
    connection = sqlite3.connect(cfg.DB_PATH)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()
    assert BAR_CACHE_TABLE not in tables
    assert "autotrader_market_bars" in tables


def test_the_draft_table_checks_its_rows(tmp_path):
    path = _scratch_cache(tmp_path / "ddl.db")
    connection = sqlite3.connect(path)
    end = "2026-10-12T14:05:00+00:00"
    claim = (CON_ID, "5m", end, "2026-10-12", "fetching", "momo", "2026-10-12T14:05:15+00:00")
    insert = (
        f"INSERT INTO {BAR_CACHE_TABLE}"
        " (con_id, bar_size, bar_end_utc, session_date, state, writer, claimed_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    try:
        objects = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE '%bar_cache%' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert objects == {BAR_CACHE_TABLE, "idx_autotrader_bar_cache_session"}
        connection.execute(insert, claim)
        for bad in (
            (0, "5m", end, "2026-10-12", "fetching", "momo", claim[6]),
            (CON_ID, "1m", end, "2026-10-12", "fetching", "momo", claim[6]),
            (CON_ID, "5m", "2026-10-12 14:05:00", "2026-10-12", "fetching", "momo", claim[6]),
            (CON_ID, "5m", end, "2026-10-12T00:00", "fetching", "momo", claim[6]),
            (CON_ID, "5m", end, "2026-10-12", "pending", "momo", claim[6]),
            (CON_ID, "5m", end, "2026-10-12", "fetching", "", claim[6]),
            (CON_ID, "5m", end, "2026-10-12", "ready", "momo", claim[6]),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(insert, bad)
        # The same key is one row: a second claim is refused.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, claim)
        # Ready needs its bars, their count and the fetch time, together.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"UPDATE {BAR_CACHE_TABLE} SET state='ready', bars_json='[]', bar_count=0",
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"UPDATE {BAR_CACHE_TABLE} SET state='ready', bars_json='{{}}', bar_count=1,"
                " fetched_at='2026-10-12T14:05:16+00:00'",
            )
        connection.execute(
            f"UPDATE {BAR_CACHE_TABLE} SET state='ready', bars_json='[{{}}]', bar_count=1,"
            " fetched_at='2026-10-12T14:05:16+00:00'",
        )
        assert connection.execute(f"SELECT state, bar_count FROM {BAR_CACHE_TABLE}").fetchall() == [
            ("ready", 1)
        ]
    finally:
        connection.close()


# --------------------------------------------------------------------------
# 2. Keys, payloads and what is worth caching
# --------------------------------------------------------------------------


def test_the_key_bar_is_the_newest_interval_closed_at_the_clock():
    close = _et(SESSION, 10, 5)
    assert completed_bar_end(close) == close
    assert completed_bar_end(close + timedelta(seconds=15)) == close
    assert completed_bar_end(close + timedelta(seconds=299, microseconds=999)) == close
    assert completed_bar_end(close + BAR_INTERVAL) == close + BAR_INTERVAL
    with pytest.raises(ValueError, match="timezone-aware"):
        completed_bar_end(datetime(2026, 10, 12, 10, 5))


def test_a_raw_bar_survives_the_json_round_trip_with_its_decimals_intact():
    received = _et(SESSION, 11, 50, 16)
    bar = _raw(BF_B.bar_ending(BF_B.signal_end), received)
    payload = json.loads(json.dumps(bar_to_payload(bar)))
    assert bar_from_payload(payload) == bar
    assert payload["close"] == str(bar.close) and payload["volume"] == str(bar.volume)
    assert payload["received_at"] == received.isoformat()


@pytest.mark.parametrize(
    "batch",
    [
        BarBatch(bars=(), connected=False),
        BarBatch(bars=(), pacing_failed=True),
        BarBatch(bars=(), error="TWS error 162: pacing violation"),
        BarBatch(bars=(), competing_session=True),
    ],
    ids=["disconnected", "pacing", "error", "competing_login"],
)
def test_an_unhealthy_batch_is_never_cached(batch):
    end = _et(SESSION, 10, 5)
    good = _quiet_batch(CON_ID, end, end + timedelta(seconds=15))
    unhealthy = BarBatch(
        bars=good.bars,
        connected=batch.connected,
        pacing_failed=batch.pacing_failed,
        error=batch.error,
        competing_session=batch.competing_session,
    )
    assert cacheable_bars(unhealthy, con_id=CON_ID, bar_end=end) is None
    assert cacheable_bars(good, con_id=CON_ID, bar_end=end) == good.bars[:-1]


def test_only_a_batch_holding_the_key_bar_of_complete_live_bars_is_cached():
    end = _et(SESSION, 10, 5)
    received = end + timedelta(seconds=15)
    # IBKR's history lags (no bar ending 10:05 yet): nothing to cache for that key.
    not_yet = BarBatch(bars=_quiet_batch(CON_ID, end - BAR_INTERVAL, received).bars[:-1])
    assert cacheable_bars(not_yet, con_id=CON_ID, bar_end=end) is None
    assert cacheable_bars(not_yet, con_id=CON_ID, bar_end=end - BAR_INTERVAL) == not_yet.bars
    for flags in ({"delayed": True}, {"frozen": True}, {"complete": False}, {"bar_type": "MIDPOINT"}):
        tainted = BarBatch(
            bars=(*_quiet_batch(CON_ID, end, received).bars[:-2], _quiet_bar(CON_ID, end, received, **flags))
        )
        assert cacheable_bars(tainted, con_id=CON_ID, bar_end=end) is None
    other = BarBatch(bars=(*_quiet_batch(CON_ID, end, received).bars[:-1], _quiet_bar(CON_ID + 1, end, received)))
    assert cacheable_bars(other, con_id=CON_ID, bar_end=end) is None
    # A tainted forming bar is dropped, not judged: it is never evidence.
    forming = BarBatch(
        bars=(*_quiet_batch(CON_ID, end, received).bars[:-1], _quiet_bar(CON_ID, end + BAR_INTERVAL, received, complete=False))
    )
    assert cacheable_bars(forming, con_id=CON_ID, bar_end=end) == forming.bars[:-1]


async def test_a_malformed_key_is_a_programming_error_not_a_cache_outage(tmp_path):
    cache = BarCache(writer="orb-v2", db_path=_scratch_cache(tmp_path / "keys.db"))
    end = _et(SESSION, 10, 5)

    async def fetch() -> BarBatch:
        raise AssertionError("never fetched")

    with pytest.raises(ValueError, match="positive"):
        await cache.get_or_fetch(0, "5m", SESSION, end, fetch)
    with pytest.raises(ValueError, match="5m"):
        await cache.get_or_fetch(CON_ID, "1m", SESSION, end, fetch)
    with pytest.raises(ValueError, match="five-minute boundary"):
        await cache.get_or_fetch(CON_ID, "5m", SESSION, end + timedelta(seconds=15), fetch)
    with pytest.raises(ValueError, match="session_date"):
        await cache.get_or_fetch(CON_ID, "5m", PRIOR, end, fetch)
    with pytest.raises(ValueError, match="writer"):
        BarCache(writer=" ")
    assert cache.available


# --------------------------------------------------------------------------
# 3. Miss, hit, release; the fallback; the race
# --------------------------------------------------------------------------


async def test_the_first_child_writes_the_children_behind_it_read(tmp_path):
    path = _scratch_cache(tmp_path / "cache.db")
    close = _et(SESSION, 10, 5)
    clock = {"now": close + STRATEGY_POLL_OFFSETS["momo"]}
    now = lambda: clock["now"]  # noqa: E731
    fetched: list[str] = []

    def fetcher(name: str, batch: BarBatch):
        async def fetch() -> BarBatch:
            fetched.append(name)
            return batch

        return fetch

    original = _quiet_batch(CON_ID, close, clock["now"])
    momo = BarCache(writer="momo", clock=now, db_path=path)
    first = await momo.get_or_fetch(CON_ID, "5m", SESSION, close, fetcher("momo", original))
    assert first is original  # the writer's own reply, forming bar and all
    for name in ("orb-v2", "gap-v2", "vwap-v2", "momo-ap"):
        clock["now"] = close + STRATEGY_POLL_OFFSETS[name]
        reader = BarCache(writer=name, clock=now, db_path=path)
        served = await reader.get_or_fetch(CON_ID, "5m", SESSION, close, fetcher(name, original))
        assert served.bars == original.bars[:-1]
        assert (served.connected, served.pacing_failed, served.error, served.competing_session) == (
            True, False, None, False
        )
    assert fetched == ["momo"]
    assert _rows(path) == [(CON_ID, close.isoformat(), "ready", "momo", 3)]

    # The next bar is a new key: a miss again, written by whoever comes first.
    later = close + BAR_INTERVAL
    clock["now"] = later + STRATEGY_POLL_OFFSETS["orb-v2"]
    orb = BarCache(writer="orb-v2", clock=now, db_path=path)
    await orb.get_or_fetch(CON_ID, "5m", SESSION, later, fetcher("orb-v2", _quiet_batch(CON_ID, later, clock["now"])))
    assert fetched == ["momo", "orb-v2"]
    assert [row[2:4] for row in _rows(path)] == [("ready", "momo"), ("ready", "orb-v2")]


async def test_a_batch_without_the_key_bar_is_returned_but_not_cached(tmp_path):
    path = _scratch_cache(tmp_path / "cache.db")
    close = _et(SESSION, 10, 5)
    clock = {"now": close + timedelta(seconds=15)}
    replies = [
        # IBKR's history lags: no bar ending at the close yet.
        BarBatch(bars=_quiet_batch(CON_ID, close - BAR_INTERVAL, clock["now"]).bars[:-1]),
        _quiet_batch(CON_ID, close, clock["now"]),
    ]
    fetches = 0

    async def fetch() -> BarBatch:
        nonlocal fetches
        fetches += 1
        return replies[min(fetches, len(replies)) - 1]

    cache = BarCache(writer="momo", clock=lambda: clock["now"], db_path=path)
    first = await cache.get_or_fetch(CON_ID, "5m", SESSION, close, fetch)
    assert first is replies[0] and _rows(path) == []
    second = await cache.get_or_fetch(CON_ID, "5m", SESSION, close, fetch)
    assert second is replies[1] and [row[2] for row in _rows(path)] == ["ready"]
    third = await cache.get_or_fetch(CON_ID, "5m", SESSION, close, fetch)
    assert third.bars == replies[1].bars[:-1] and fetches == 2


async def test_a_failed_request_releases_the_claim_for_the_next_child(tmp_path):
    path = _scratch_cache(tmp_path / "cache.db")
    close = _et(SESSION, 10, 5)
    clock = {"now": close + timedelta(seconds=15)}

    async def timeout() -> BarBatch:
        raise TimeoutError("TWS historical-data request timed out")

    momo = BarCache(writer="momo", clock=lambda: clock["now"], db_path=path)
    with pytest.raises(TimeoutError):
        await momo.get_or_fetch(CON_ID, "5m", SESSION, close, timeout)
    assert _rows(path) == [] and momo.available

    async def reply() -> BarBatch:
        return _quiet_batch(CON_ID, close, clock["now"])

    orb = BarCache(writer="orb-v2", clock=lambda: clock["now"], db_path=path)
    await orb.get_or_fetch(CON_ID, "5m", SESSION, close, reply)
    assert [row[2:4] for row in _rows(path)] == [("ready", "orb-v2")]


async def test_a_missing_table_is_a_direct_fetch_announced_once(tmp_path, caplog):
    # Tonight's live schema: migrated to v16, no cache table.
    path = tmp_path / "no-table.db"
    sqlite3.connect(path).close()
    close = _et(SESSION, 10, 5)
    clock = {"now": close + timedelta(seconds=75)}
    fetches = 0
    batch = _quiet_batch(CON_ID, close, clock["now"])

    async def fetch() -> BarBatch:
        nonlocal fetches
        fetches += 1
        return batch

    cache = BarCache(writer="orb-v2", clock=lambda: clock["now"], db_path=str(path))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        for _ in range(3):
            assert await cache.get_or_fetch(CON_ID, "5m", SESSION, close, fetch) is batch
    assert fetches == 3 and not cache.available
    announced = [record for record in caplog.records if BAR_CACHE_UNAVAILABLE in record.getMessage()]
    assert len(announced) == 1
    assert "no such table" in announced[0].getMessage() and announced[0].levelno == logging.WARNING


async def test_a_write_failure_keeps_the_fetched_batch_and_latches_the_fallback(tmp_path, caplog, monkeypatch):
    path = _scratch_cache(tmp_path / "cache.db")
    close = _et(SESSION, 10, 5)
    clock = {"now": close + timedelta(seconds=15)}
    fetches = 0
    batch = _quiet_batch(CON_ID, close, clock["now"])

    async def fetch() -> BarBatch:
        nonlocal fetches
        fetches += 1
        return batch

    cache = BarCache(writer="momo", clock=lambda: clock["now"], db_path=path)

    async def disk_full(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(cache, "_fill", disk_full)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert await cache.get_or_fetch(CON_ID, "5m", SESSION, close, fetch) is batch
        assert await cache.get_or_fetch(CON_ID, "5m", SESSION, close, fetch) is batch
    assert fetches == 2 and not cache.available
    assert [BAR_CACHE_UNAVAILABLE in record.getMessage() for record in caplog.records] == [True]
    # The orphaned claim is taken over by the next child once it is stale.
    clock["now"] += CLAIM_STALE_AFTER
    other = BarCache(writer="orb-v2", clock=lambda: clock["now"], db_path=path)
    served = await other.get_or_fetch(CON_ID, "5m", SESSION, close, fetch)
    assert served is batch and fetches == 3
    assert [row[2:4] for row in _rows(path)] == [("ready", "orb-v2")]


async def test_a_cache_failure_inside_the_facade_changes_nothing_the_caller_sees(tmp_path, caplog):
    path = tmp_path / "no-table.db"
    sqlite3.connect(path).close()
    clock = {"now": _et(SESSION, 11, 50, 15)}
    actor = ReplayActor(clock, latency=timedelta(seconds=0.7))
    direct = PaperIBKRMarketData(actor=actor, host="127.0.0.1", bar_max_age=WATCHLIST_BAR_MAX_AGE)
    expected = await direct.completed_bars(con_id=BF_B.con_id, now=clock["now"])
    clock["now"] = _et(SESSION, 11, 50, 15)
    cached = PaperIBKRMarketData(
        actor=_cached(actor, writer="momo", clock=clock, db_path=str(path)),
        host="127.0.0.1",
        bar_max_age=WATCHLIST_BAR_MAX_AGE,
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert await cached.completed_bars(con_id=BF_B.con_id, now=clock["now"]) == expected
    assert len(actor.requests) == 2
    assert sum(BAR_CACHE_UNAVAILABLE in record.getMessage() for record in caplog.records) == 1


async def test_two_children_racing_on_one_key_make_one_request_and_read_the_same_bars(tmp_path):
    path = _scratch_cache(tmp_path / "race.db")
    now = datetime.now(timezone.utc)
    close = completed_bar_end(now)
    session_date = close.astimezone(bar_cache._NEW_YORK).date()
    started = asyncio.Event()
    release = asyncio.Event()
    fetched: list[str] = []
    batch = _quiet_batch(CON_ID, close, now)

    async def slow_fetch() -> BarBatch:
        started.set()
        await release.wait()
        fetched.append("momo")
        return batch

    async def never() -> BarBatch:
        fetched.append("orb-v2")
        return _quiet_batch(CON_ID, close, now, count=1)

    momo = BarCache(writer="momo", db_path=path)
    orb = BarCache(writer="orb-v2", db_path=path)
    first = asyncio.create_task(momo.get_or_fetch(CON_ID, "5m", session_date, close, slow_fetch))
    await asyncio.wait_for(started.wait(), timeout=5)
    second = asyncio.create_task(orb.get_or_fetch(CON_ID, "5m", session_date, close, never))
    await asyncio.sleep(0.6)
    # The second child is waiting on the first's claim, not asking TWS.
    assert not second.done() and fetched == []
    release.set()
    first_batch, second_batch = await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
    assert fetched == ["momo"]
    assert first_batch is batch
    assert second_batch.bars == batch.bars[:-1]
    assert [row[2:4] for row in _rows(path)] == [("ready", "momo")]


async def test_a_claim_older_than_the_pacing_window_is_taken_over(tmp_path):
    path = _scratch_cache(tmp_path / "stale.db")
    close = _et(SESSION, 10, 5)
    claimed_at = close + timedelta(seconds=15)
    connection = sqlite3.connect(path)
    connection.execute(
        f"INSERT INTO {BAR_CACHE_TABLE}"
        " (con_id, bar_size, bar_end_utc, session_date, state, writer, claimed_at)"
        " VALUES (?, '5m', ?, ?, 'fetching', 'momo', ?)",
        (CON_ID, close.isoformat(), SESSION.isoformat(), claimed_at.isoformat()),
    )
    connection.commit()
    connection.close()
    clock = {"now": claimed_at + timedelta(seconds=1)}
    naps: list[float] = []

    async def sleep(seconds: float) -> None:
        naps.append(seconds)
        clock["now"] += timedelta(seconds=seconds)

    fetches = 0

    async def fetch() -> BarBatch:
        nonlocal fetches
        fetches += 1
        return _quiet_batch(CON_ID, close, clock["now"])

    reader = BarCache(writer="orb-v2", clock=lambda: clock["now"], sleep=sleep, db_path=path)
    served = await reader.get_or_fetch(CON_ID, "5m", SESSION, close, fetch)
    # It waited out the 15 s window (14 s remained, 70 naps), then fetched for itself.
    assert fetches == 1 and served.bars[-1].interval_end_utc == close + BAR_INTERVAL
    assert len(naps) == 70 and clock["now"] - claimed_at == CLAIM_STALE_AFTER
    assert [row[2:4] for row in _rows(path)] == [("ready", "orb-v2")]


async def test_rows_of_earlier_sessions_are_pruned_on_first_use(tmp_path):
    path = _scratch_cache(tmp_path / "prune.db")
    old_close = _et(PRIOR, 10, 5)
    close = _et(SESSION, 10, 5)
    clock = {"now": old_close + timedelta(seconds=15)}
    now = lambda: clock["now"]  # noqa: E731

    async def fetch() -> BarBatch:
        return _quiet_batch(CON_ID, completed_bar_end(now()), now())

    await BarCache(writer="momo", clock=now, db_path=path).get_or_fetch(CON_ID, "5m", PRIOR, old_close, fetch)
    assert [row[1] for row in _rows(path)] == [old_close.isoformat()]
    clock["now"] = close + timedelta(seconds=15)
    await BarCache(writer="momo", clock=now, db_path=path).get_or_fetch(CON_ID, "5m", SESSION, close, fetch)
    assert [row[1] for row in _rows(path)] == [close.isoformat()]


# --------------------------------------------------------------------------
# 4. Freshness: a reader's refusals are a direct fetch's refusals
# --------------------------------------------------------------------------


async def test_a_cached_bar_is_served_or_refused_under_the_readers_own_limit(tmp_path):
    path = _scratch_cache(tmp_path / "fresh.db")
    close = BF_B.signal_end  # the 11:50 ET close
    clock = {"now": close + STRATEGY_POLL_OFFSETS["momo"]}
    actor = ReplayActor(clock, latency=timedelta(seconds=0.7))
    writer = PaperIBKRMarketData(
        actor=_cached(actor, writer="momo", clock=clock, db_path=path),
        host="127.0.0.1",
        bar_max_age=WATCHLIST_BAR_MAX_AGE,
    )
    written = await writer.completed_bars(con_id=BF_B.con_id, now=clock["now"])
    assert written[-1].interval_end_utc == close and len(actor.requests) == 1

    def markets(name: str, limit: timedelta) -> tuple[PaperIBKRMarketData, PaperIBKRMarketData]:
        cached = PaperIBKRMarketData(
            actor=_cached(actor, writer=name, clock=clock, db_path=path),
            host="127.0.0.1",
            bar_max_age=limit,
        )
        direct = PaperIBKRMarketData(actor=actor, host="127.0.0.1", bar_max_age=limit)
        return cached, direct

    # Every child behind MOMO, in its own slot under its own limit, reads the
    # bars a direct request would have returned.
    for name, universe in (("orb-v2", "scanner"), ("gap-v2", "gappers"), ("vwap-v2", "watchlist"), ("momo-ap", "watchlist")):
        schedule = poll_schedule(name, universe)
        cached, direct = markets(name, schedule.bar_max_age)
        clock["now"] = close + schedule.offset
        requests = len(actor.requests)
        from_cache = await cached.completed_bars(con_id=BF_B.con_id, now=clock["now"])
        assert len(actor.requests) == requests
        clock["now"] = close + schedule.offset
        assert await direct.completed_bars(con_id=BF_B.con_id, now=clock["now"]) == from_cache == written
        assert len(actor.requests) == requests + 1

    # 255 s after the close the bar is over the hot list's 240 s limit: refused
    # from the cache exactly as from TWS, with the same fault...
    for market in markets("vwap-v2", WATCHLIST_BAR_MAX_AGE):
        clock["now"] = close + timedelta(seconds=255)
        with pytest.raises(MarketDataQualityError, match="stale market data") as refused:
            await market.completed_bars(con_id=BF_B.con_id, now=clock["now"])
        assert refused.value.fault == FAULT_STALE
    # ...while managed MOMO's 280 s limit still admits it from both.
    for market in markets("momo-ap", MANAGED_BAR_MAX_AGE):
        clock["now"] = close + timedelta(seconds=255)
        assert await market.completed_bars(con_id=BF_B.con_id, now=clock["now"]) == written


# --------------------------------------------------------------------------
# 5. Golden: MOMO_HOD_V2's recorded day, cache off, on as writer, on as reader
# --------------------------------------------------------------------------


async def _momo_day(tmp_path, monkeypatch, *, label: str, cache_db: str | None):
    """One MOMO_HOD_V2 session of the recorded day through the real data boundary."""
    from config import cfg
    from db.core import init_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / f"golden-{label}.db"))
    await init_db()
    strategy = await ensure_strategy(owner_id="owner-a", spec=momo_strategy_spec())
    schedule = poll_schedule("momo", "watchlist")
    clock = {"now": _et(SESSION, 9, 50)}
    now = lambda: clock["now"]  # noqa: E731

    async def sleep(seconds: float) -> None:
        clock["now"] += timedelta(seconds=seconds)

    actor = ReplayActor(clock)
    data_actor: Any = actor
    if cache_db is not None:
        data_actor = _cached(actor, writer=label, clock=clock, db_path=cache_db, sleep=sleep)
    market = PaperIBKRMarketData(actor=data_actor, host="127.0.0.1", bar_max_age=schedule.bar_max_age)
    setups: list[RuleSetup] = []

    class Provider:
        async def propose_trade(self, *, setup: RuleSetup) -> AIReview:
            setups.append(setup)
            return _proposal(setup)

    async def read_account() -> AccountSnapshot:
        return AccountSnapshot(
            paper_account_id="DU1234567",
            client_id=20,
            observed_at=clock["now"],
            net_liquidation=Decimal("665000"),
            available_funds=Decimal("665000"),
            realized_day_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
        )

    async def hot_list(run: Run):
        parameters: dict[str, object] = {"source": "screener_hotlist", "top": 35}
        scanner = ScannerRun(
            id=f"{run.id}-hotlist",
            run_id=run.id,
            parameters=parameters,
            parameters_fingerprint=fingerprint_payload(parameters),
            status="completed",
            started_at=clock["now"],
        )
        candidates = tuple(
            replay.candidate(scanner.id, rank) for rank, replay in enumerate((BF_B, FIGS), 1)
        )
        for candidate in candidates:
            await market.load_contract(candidate.con_id)
        liquidity = {
            replay.con_id: LiquidityProfile(
                con_id=replay.con_id,
                reference_price=replay.reference_price,
                average_dollar_volume=replay.average_dollar_volume,
                sessions_used=20,
                last_session_date=PRIOR,
            )
            for replay in (BF_B, FIGS)
        }
        return scanner, candidates, liquidity

    runner = ShadowSessionRunner(
        market=market,
        read_account=read_account,
        runner=RepositoryBackedShadowRunner(
            pipeline=ShadowPipeline(proposal_provider=Provider(), strategy=MomoStrategy()),
            clock=now,
            lease_ttl=timedelta(hours=6),
            lease_renew_interval=timedelta(hours=1),
        ),
        clock=now,
        poll_schedule=schedule,
        sleep=sleep,
    )
    report = await runner.run(
        session=market_session(SESSION),
        strategy=strategy,
        policy=RiskPolicy(),
        source_sha="abcdef1",
        policy_fingerprints={"risk": RiskPolicy().fingerprint(), "momo_hod": MomoConfig().fingerprint()},
        run_id=f"golden-{label}",
        candidate_source=hot_list,
        window_start=strategy_entry_window("momo", SESSION)[0],
    )
    symbols = {
        replay.candidate(f"golden-{label}-hotlist").id: replay.symbol for replay in (BF_B, FIGS)
    }
    stored = await list_decisions(owner_id="owner-a", run_id=report.run_id)
    decisions = sorted(_decision_projection(item, symbols) for item in stored)
    return SimpleNamespace(
        report=report,
        actor=actor,
        decisions=decisions,
        setups=[_setup_projection(setup) for setup in setups],
        cache=None if cache_db is None else data_actor.cache,
    )


async def test_golden_momo_v2_decides_the_recorded_day_identically_with_the_cache_off_and_on(
    tmp_path, monkeypatch
):
    cache_db = _scratch_cache(tmp_path / "cache.db")
    off = await _momo_day(tmp_path, monkeypatch, label="off", cache_db=None)
    writer = await _momo_day(tmp_path, monkeypatch, label="writer", cache_db=cache_db)
    reader = await _momo_day(tmp_path, monkeypatch, label="reader", cache_db=cache_db)

    # V2's known day: BF B bought at 11:50 with the wide stop, FIGS skipped.
    assert off.decisions == [
        ("BF B", "shadow_accepted", "PROPOSE", True, (), (3726, "26.77", "26.51", "27.29")),
        ("FIGS", "rule_rejected", None, None, (), (None, None, None, None)),
    ]
    assert [setup[0] for setup in off.setups] == ["BF B"] and off.setups[0][6] == BF_B.signal_end
    assert (off.report.polls, off.report.data_errors) == (60, 0)
    for run in (writer, reader):
        assert run.decisions == off.decisions
        assert run.setups == off.setups
        assert (run.report.polls, run.report.data_errors) == (off.report.polls, off.report.data_errors)
        assert dict(run.report.outcomes) == dict(off.report.outcomes)
        assert run.cache.available
    # The writer asks TWS exactly what a direct run asks; the reader asks nothing.
    assert writer.actor.requests == off.actor.requests
    assert off.actor.requests[0] == (BF_B.con_id, _et(SESSION, 10, 0, 15))
    assert reader.actor.requests == []
    rows = _rows(cache_db)
    assert len(rows) == len(off.actor.requests) and {row[2:4] for row in rows} == {("ready", "writer")}


# --------------------------------------------------------------------------
# 6. Benchmark: a synthetic five-child day
# --------------------------------------------------------------------------

HOT_LIST = tuple(range(1001, 1036))  # 35 hot-list symbols
SCANNER = tuple(range(1016, 1066))  # 50 scanner rows, 20 of them on the hot list
GAPPERS = tuple(range(1030, 1045))  # 15 gappers, 6 on the hot list, all scanned
CHILDREN = (
    ("momo", "watchlist", HOT_LIST),
    ("orb-v2", "scanner", SCANNER),
    ("gap-v2", "gappers", GAPPERS),
    ("vwap-v2", "watchlist", HOT_LIST),
    ("momo-ap", "watchlist", HOT_LIST),
)
REQUESTS_PER_BAR = sum(len(con_ids) for _, _, con_ids in CHILDREN)  # 170
DISTINCT_PER_BAR = len(set().union(*(con_ids for _, _, con_ids in CHILDREN)))  # 65


class QuietActor:
    """Today's quiet bars through the forming one, for any conId."""

    def __init__(self, clock: dict[str, datetime]) -> None:
        self.clock = clock
        self.requests = 0

    async def completed_trade_bars(self, con_id: int) -> BarBatch:
        self.requests += 1
        now = self.clock["now"]
        bars = []
        end = _et(SESSION, 9, 35)
        while end <= now + BAR_INTERVAL:
            bars.append(_quiet_bar(con_id, end, now))
            end += BAR_INTERVAL
        return BarBatch(bars=tuple(bars))


async def synthetic_day(cache_db: str | None, *, bars: int, first_close: datetime = _et(SESSION, 10, 0)) -> dict[str, int]:
    """Five children polling ``bars`` closes in their slots; requests per child."""
    clock = {"now": first_close}
    now = lambda: clock["now"]  # noqa: E731

    async def sleep(seconds: float) -> None:
        clock["now"] += timedelta(seconds=seconds)

    children = []
    for name, universe, con_ids in CHILDREN:
        actor = QuietActor(clock)
        data_actor: Any = actor
        if cache_db is not None:
            data_actor = _cached(actor, writer=name, clock=clock, db_path=cache_db, sleep=sleep)
        schedule = poll_schedule(name, universe)
        market = PaperIBKRMarketData(actor=data_actor, host="127.0.0.1", bar_max_age=schedule.bar_max_age)
        children.append((name, schedule, con_ids, actor, market))
    children.sort(key=lambda child: child[1].offset)
    close = first_close
    for _ in range(bars):
        for name, schedule, con_ids, actor, market in children:
            clock["now"] = close + schedule.offset
            for con_id in con_ids:
                served = await market.completed_bars(con_id=con_id, now=clock["now"])
                assert served[-1].interval_end_utc == close, name
        close += BAR_INTERVAL
    return {name: actor.requests for name, _, _, actor, _ in children}


async def test_benchmark_a_five_child_day_sends_a_third_of_the_history_requests(tmp_path):
    bars = 3
    off = await synthetic_day(None, bars=bars)
    on = await synthetic_day(_scratch_cache(tmp_path / "bench.db"), bars=bars)
    assert sum(off.values()) == bars * REQUESTS_PER_BAR == bars * 170
    assert sum(on.values()) == bars * DISTINCT_PER_BAR == bars * 65
    # MOMO polls first and still asks for every hot-list symbol; ORB_V2 for the
    # 30 scanner rows off the hot list; the rest read what those two fetched.
    assert on == {"momo": bars * 35, "orb-v2": bars * 30, "gap-v2": 0, "vwap-v2": 0, "momo-ap": 0}
    reduction = 1 - sum(on.values()) / sum(off.values())
    assert 0.60 <= reduction <= 0.70
    print(f"\nbar cache benchmark: {bars} bars, off={sum(off.values())} on={sum(on.values())} "
          f"reduction={reduction:.1%} per child off={off} on={on}")


# --------------------------------------------------------------------------
# 7. The composition root wraps the actor only when both gates are open
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "opted", "wrapped"),
    [(False, False, False), (True, False, False), (False, True, False), (True, True, True)],
)
async def test_run_shadow_session_wraps_the_actor_only_with_the_flag_on_and_the_entry_opted_in(
    monkeypatch, flag, opted, wrapped
):
    captured: dict[str, dict[str, object]] = {}

    def record(name: str):
        def factory(*_args: object, **kwargs: object) -> SimpleNamespace:
            captured[name] = kwargs
            return SimpleNamespace(**kwargs)

        return factory

    class FakeActor:
        connected = True

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def connect(self) -> None:
            pass

        async def disconnect(self) -> None:
            pass

        async def preflight(self, *, required_data_lines: int) -> SimpleNamespace:
            return SimpleNamespace(ready=True, reasons=())

        async def account_snapshot(self) -> None:
            raise AssertionError("the fake runner never reads the account")

    class FakeRunner:
        def __init__(self, **kwargs: object) -> None:
            captured["runner"] = kwargs

        async def run(self, **kwargs: object) -> SessionReport:
            return SessionReport(run_id=str(kwargs["run_id"]), session_date=TUESDAY)

    async def strategy(**_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(strategy_id="f" * 32)

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(shadow_service, "ensure_strategy", strategy)
    monkeypatch.setattr(shadow_service, "read_anthropic_api_key", lambda: "sk-test")
    monkeypatch.setattr(shadow_service, "build_anthropic_client", lambda **_kwargs: object())
    monkeypatch.setattr(shadow_service, "AnthropicMessagesTransport", record("transport"))
    monkeypatch.setattr(
        shadow_service,
        "StructuredAIReviewProvider",
        lambda **_kwargs: SimpleNamespace(usage=SimpleNamespace(calls=0, cost_usd=Decimal("0"))),
    )
    monkeypatch.setattr(shadow_service, "TWSPaperActor", FakeActor)
    monkeypatch.setattr(shadow_service, "PaperIBKRMarketData", record("market"))
    monkeypatch.setattr(shadow_service, "ShadowPipeline", record("pipeline"))
    monkeypatch.setattr(shadow_service, "RepositoryBackedShadowRunner", record("repository"))
    monkeypatch.setattr(shadow_service, "ShadowSessionRunner", FakeRunner)
    monkeypatch.setattr(shadow_service, "source_sha", lambda: "abc1234")
    monkeypatch.setattr(bar_cache, "BAR_CACHE_ENABLED", flag)
    monkeypatch.setitem(shadow_service.STRATEGY_USES_BAR_CACHE, "orb-v2", opted)

    clock = lambda: et(TUESDAY, 9, 0)  # noqa: E731
    await shadow_service.run_shadow_session(
        config=runtime_config(),
        owner_id="demo",
        session=market_session(TUESDAY),
        clock=clock,
        sleep=no_sleep,
        strategy_name="orb-v2",
        universe="scanner",
    )

    actor = captured["market"]["actor"]
    if wrapped:
        assert isinstance(actor, CachedBarsActor)
        assert isinstance(actor.actor, FakeActor)
        assert actor.cache.writer == "orb-v2" and actor.cache.available
    else:
        assert isinstance(actor, FakeActor)
    assert captured["market"]["bar_max_age"] == poll_schedule("orb-v2", "scanner").bar_max_age


async def test_the_wrapper_delegates_every_other_request_untouched(tmp_path):
    path = _scratch_cache(tmp_path / "delegate.db")
    clock = {"now": _et(SESSION, 11, 50, 15)}
    actor = ReplayActor(clock)
    wrapped = _cached(actor, writer="momo", clock=clock, db_path=path)
    assert await wrapped.contract_details(BF_B.con_id) == await actor.contract_details(BF_B.con_id)
    assert await wrapped.quote_snapshot(BF_B.con_id, min_tick=TICK) == BF_B.quote(clock["now"])
    for request in (wrapped.extended_trade_bars, wrapped.daily_trade_bars):
        with pytest.raises(AssertionError):
            await request(BF_B.con_id)
    with pytest.raises(AssertionError):
        await wrapped.scan(None)
    with pytest.raises(AssertionError):
        await wrapped.supports_scanner(None)
    assert actor.requests == []
