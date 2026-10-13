"""The nightly recorder's day-manager steps, the extended-hours store and GAP_GO's replay."""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.schema_support import migrate_database
from trading.contracts import ShadowRuntimeConfig
from trading.market.ibkr_boundary import (
    BarBatch,
    DailyBarBatch,
    IBKRDailyBar,
    IBKRTradeBar,
    is_regular_session_bar,
)
from trading.research import bars_store, recorder
from trading.research.bars_store import BarsStore, BarsStoreError, ResearchPaths, StoredBar, StoredDay
from trading.research.recorder import (
    MAX_GAP_CANDIDATES,
    RECORDER_MARGIN_SECONDS,
    RECORDER_MAX_CONTRACTS,
    RECORDER_WINDOW_SECONDS,
    WATCHLIST_PLANNING_SIZE,
    Instrument,
    extended_session_bars,
    load_gap_candidates,
    load_qualified_universe,
    next_session_after_close,
    nightly_budget_seconds,
    record_extended,
)
from trading.research.replay import replay_strategy, run_replay_cli
from trading.scheduler.sessions import market_session

NEW_YORK = ZoneInfo("America/New_York")
UTC = timezone.utc
SESSION = date(2026, 10, 12)  # a Monday
PRIOR = date(2026, 10, 9)
NEXT = date(2026, 10, 13)
BAR = timedelta(minutes=5)
EVENING = datetime.combine(SESSION, time(17, 0), tzinfo=NEW_YORK).astimezone(UTC)


def et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=NEW_YORK).astimezone(UTC)


def five(con_id: int, start: datetime, o: str, h: str, low: str, c: str, volume: str = "100") -> IBKRTradeBar:
    return IBKRTradeBar(
        con_id=con_id,
        interval_start_utc=start,
        interval_end_utc=start + BAR,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal(volume),
        rth=is_regular_session_bar(start, start + BAR),
        received_at=start + BAR,
    )


def flat_session(con_id: int, day: date, price: str = "100") -> list[IBKRTradeBar]:
    """A quiet regular session: every bar 0.8 wide around ``price``."""
    level = Decimal(price)
    bars, start = [], et(day, 9, 30)
    while start < et(day, 16, 0):
        bars.append(five(con_id, start, price, str(level + Decimal("0.4")), str(level - Decimal("0.4")), price))
        start += BAR
    return bars


def gap_day(con_id: int, day: date, *, first_open: str = "104") -> list[IBKRTradeBar]:
    """Pre-market from 04:00 (high 104, 66 x 100 lots), a 4% gap, then a 09:40 breakout."""
    bars, start = [], et(day, 4, 0)
    while start < et(day, 9, 30):
        bars.append(five(con_id, start, "103.5", "104", "103.4", "103.8"))
        start += BAR
    regular = [
        (first_open, "104.5", str(min(Decimal(first_open), Decimal("103.5"))), "104.2"),
        ("104.2", "104.4", "103.9", "104.3"),
        ("104.3", "104.9", "104.2", "104.8"),  # first close above the 104.5 trigger
        ("104.9", "105.2", "104.7", "105.1"),  # the entry fills here
        ("105.1", "107.5", "105", "107.2"),  # the 2R target trades
    ]
    for o, h, low, c in regular:
        bars.append(five(con_id, start, o, h, low, c, volume="500"))
        start += BAR
    while start < et(day, 16, 0):
        bars.append(five(con_id, start, "107.2", "107.4", "107", "107.2"))
        start += BAR
    return bars


def stored(bars: list[IBKRTradeBar], *, day: date, con_id: int, symbol: str, use_rth: bool) -> StoredDay:
    return StoredDay(
        session_date=day,
        con_id=con_id,
        symbol=symbol,
        bars=tuple(
            StoredBar(
                interval_start_utc=bar.interval_start_utc,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                rth=bar.rth,
            )
            for bar in bars
        ),
        prior_close=Decimal("100"),
        avg_dollar_volume=Decimal("1000000000"),
        primary_exchange="NASDAQ",
        use_rth=use_rth,
    )


# --- the extended-hours store ---------------------------------------------------------------


def test_extended_days_are_a_separate_series_that_fails_closed_across(tmp_path):
    store = BarsStore(ResearchPaths(tmp_path / "research"))
    regular = stored(flat_session(7, SESSION), day=SESSION, con_id=7, symbol="GAPR", use_rth=True)
    extended = stored(gap_day(7, SESSION), day=SESSION, con_id=7, symbol="GAPR", use_rth=False)

    store.write_day(regular)
    path = store.write_day(extended)

    assert path == tmp_path / "research" / "extended" / "2026-10-12" / "7.json"
    assert json.loads(path.read_text())["request"]["use_rth"] == 0
    assert store.read_day(SESSION, 7, extended=True) == extended
    assert store.read_day(SESSION, 7) == regular
    assert store.list_sessions() == (SESSION,) and store.list_sessions(extended=True) == (SESSION,)
    assert sum(1 for bar in extended.bars if not bar.rth) == 66
    # A file in the wrong series is refused, never replayed.
    (tmp_path / "research" / "bars" / "2026-10-12" / "8.json").write_bytes(path.read_bytes().replace(b'"con_id":7', b'"con_id":8'))
    with pytest.raises(BarsStoreError, match="wrong series"):
        store.read_day(SESSION, 8)
    with pytest.raises(BarsStoreError, match="04:00"):
        StoredDay(
            session_date=SESSION, con_id=7, symbol="GAPR", use_rth=False,
            bars=(StoredBar(interval_start_utc=et(SESSION, 3, 55), open=Decimal("1"), high=Decimal("1"),
                            low=Decimal("1"), close=Decimal("1"), volume=Decimal("0"), rth=False),),
        )


def test_extended_session_bars_keep_04_00_through_the_close_and_completed_bars_only():
    raw = [
        five(7, et(SESSION, 3, 55), "1", "1", "1", "1"),
        *gap_day(7, SESSION),
        five(7, et(SESSION, 16, 0), "107", "107", "107", "107"),  # after-hours
        five(7, et(PRIOR, 15, 55), "100", "100", "100", "100"),  # another day
    ]

    kept = extended_session_bars(raw, session_date=SESSION, now=EVENING)
    partial = extended_session_bars(raw, session_date=SESSION, now=et(SESSION, 9, 33))

    assert kept[0].interval_start_utc == et(SESSION, 4, 0)
    assert kept[-1].interval_end_utc == et(SESSION, 16, 0)
    assert len(kept) == 66 + 78
    assert partial[-1].interval_end_utc == et(SESSION, 9, 30)


class ExtendedActor:
    def __init__(self, bars: dict[int, list[IBKRTradeBar]]) -> None:
        self.bars = bars
        self.calls: list[int] = []

    async def extended_trade_bars(self, con_id: int) -> BarBatch:
        self.calls.append(con_id)
        return BarBatch(bars=tuple(self.bars.get(con_id, ())))


@pytest.mark.asyncio
async def test_record_extended_stores_each_gap_candidate_once_with_the_regular_days_context(tmp_path):
    store = BarsStore(ResearchPaths(tmp_path / "research"))
    store.write_day(stored(flat_session(7, SESSION), day=SESSION, con_id=7, symbol="GAPR", use_rth=True))
    actor = ExtendedActor({7: gap_day(7, SESSION), 8: []})
    candidates = [Instrument(7, "GAPR", None), Instrument(8, "EMPTY", "NYSE")]

    report = await record_extended(
        actor, session_date=SESSION, instruments=candidates, pace_seconds=0, store=store, clock=lambda: EVENING
    )

    assert (report.written, report.already_stored, report.failures) == (1, 0, (("EMPTY", "no extended-hours bars"),))
    day = store.read_day(SESSION, 7, extended=True)
    assert (day.prior_close, day.avg_dollar_volume, day.primary_exchange) == (
        Decimal("100"), Decimal("1000000000"), "NASDAQ",
    )
    again = await record_extended(
        actor, session_date=SESSION, instruments=candidates[:1], pace_seconds=0, store=store, clock=lambda: EVENING
    )
    assert again.already_stored == 1 and actor.calls == [7, 8]

    # IBKR's all-hours request covers only the current day: a later night skips.
    next_day = await record_extended(
        actor, session_date=SESSION, instruments=candidates, pace_seconds=0, store=store,
        clock=lambda: EVENING + timedelta(days=1),
    )
    during = await record_extended(
        actor, session_date=SESSION, instruments=candidates, pace_seconds=0, store=store,
        clock=lambda: et(SESSION, 15, 0),
    )
    assert (next_day.skipped, during.skipped) == ("not_the_session_day", "session_not_closed")


# --- the nightly universe and gap candidates ------------------------------------------------------


@pytest.fixture
def runtime_db(tmp_path) -> Path:
    path = migrate_database(tmp_path / "autotrader.db")
    with closing(sqlite3.connect(path)) as db:
        stamp = EVENING.isoformat()
        db.executemany(
            "INSERT INTO autotrader_universe VALUES (?, ?, 'NASDAQ', ?, ?, 'NASDAQ', 'resolved', NULL, ?)",
            [("watchlist", "WAT", "WAT", 1, stamp), ("watchlist", "ZED", "ZED", 2, stamp)],
        )
        daily = [
            (SESSION, 1, "WAT", 1), (SESSION, 2, "ZED", 1), (SESSION, 600, "DAYB", 1),
            (SESSION, 601, "DAYA", 1), (SESSION, 700, "SCAN", 1), (SESSION, 602, "DAYX", 0),
            (NEXT, 1, "WAT", 1),  # tonight's next-session qualification
        ]
        db.executemany(
            """INSERT INTO autotrader_universe_daily VALUES
               (?, ?, ?, '50', '1000000', '50000000', NULL, ?, ?, ?)""",
            [
                (day.isoformat(), con_id, symbol, qualified, None if qualified else "stock_type_ETF", stamp)
                for day, con_id, symbol, qualified in daily
            ],
        )
        db.executemany(
            """INSERT INTO autotrader_manager_day_universe
               (session_date, con_id, symbol, primary_exchange, first_seen, last_seen, sources_json,
                best_rank, qualified, reason) VALUES (?, ?, ?, 'NYSE', ?, ?, '["HOT_BY_VOLUME"]', ?, ?, ?)""",
            [
                (SESSION.isoformat(), 600, "DAYB", stamp, stamp, 2, 1, None),
                (SESSION.isoformat(), 601, "DAYA", stamp, stamp, 5, 1, None),
                (SESSION.isoformat(), 602, "DAYX", stamp, stamp, 1, 0, "stock_type_etf"),
                # Admitted by the day universe alone: no autotrader_universe_daily row.
                (SESSION.isoformat(), 603, "DAYC", stamp, stamp, 3, 1, None),
            ],
        )
        db.commit()
    return path


def test_the_recorder_takes_the_closed_sessions_universe_in_priority_order_and_caps_it(runtime_db):
    session_date, instruments = load_qualified_universe(runtime_db, now=EVENING)

    assert session_date == SESSION  # not tonight's NEXT qualification
    assert [(item.symbol, item.primary_exchange) for item in instruments] == [
        ("WAT", "NASDAQ"), ("ZED", "NASDAQ"),  # the watchlist first
        # the day universe by scan rank, whether or not the hot-35 path qualified it too
        ("DAYB", "NYSE"), ("DAYC", "NYSE"), ("DAYA", "NYSE"),
        ("SCAN", None),  # other qualified scan names last
    ]
    _date, capped = load_qualified_universe(runtime_db, now=EVENING, max_contracts=3)
    assert [item.symbol for item in capped] == ["WAT", "ZED", "DAYB"]
    # Without a clock the newest qualified session wins, as before.
    assert load_qualified_universe(runtime_db)[0] == NEXT


def test_one_night_fits_the_recorders_window():
    from trading.operations import supervisor

    start, end = supervisor.RECORDER_WINDOW
    window = (datetime.combine(SESSION, end) - datetime.combine(SESSION, start)).total_seconds()
    assert RECORDER_WINDOW_SECONDS == window
    worst = nightly_budget_seconds(RECORDER_MAX_CONTRACTS, MAX_GAP_CANDIDATES, WATCHLIST_PLANNING_SIZE)
    assert worst + RECORDER_MARGIN_SECONDS <= RECORDER_WINDOW_SECONDS
    # The cap leaves room for the whole day universe beside a full watchlist.
    from trading.market.day_universe import DAY_UNIVERSE_MAX

    assert RECORDER_MAX_CONTRACTS >= 400 + DAY_UNIVERSE_MAX


def _snapshot(db: sqlite3.Connection, observed: datetime, rows: list[dict], day_rows: list[dict]) -> None:
    payload = {"schema": "screener.v1", "rows": rows, "day_universe": {"rows": day_rows}}
    db.execute(
        "INSERT INTO autotrader_screener_snapshots VALUES (?, ?, ?, ?)",
        (observed.isoformat(), SESSION.isoformat(), observed.isoformat(timespec="microseconds"), json.dumps(payload)),
    )


def test_gap_candidates_are_watchlist_scan_and_day_universe_gappers_of_the_first_hour(runtime_db):
    def row(con_id: int, symbol: str, gap: str | None) -> dict:
        return {"con_id": con_id, "symbol": symbol, "primary_exchange": "NASDAQ", "gap_pct": gap}

    with closing(sqlite3.connect(runtime_db)) as db:
        _snapshot(db, et(SESSION, 9, 0), [row(9, "EARLY", "9")], [])  # before the open
        _snapshot(db, et(SESSION, 9, 31), [row(1, "WAT", "3.10"), row(700, "SCAN", "2.99")], [row(600, "DAYB", "5.5")])
        _snapshot(db, et(SESSION, 10, 15), [row(1, "WAT", "3.40"), row(2, "ZED", None)], [row(600, "DAYB", "5.5")])
        _snapshot(db, et(SESSION, 11, 0), [row(3, "LATE", "8")], [])  # after the first hour
        db.commit()

    candidates = load_gap_candidates(runtime_db, SESSION)

    assert [(item.symbol, item.con_id) for item in candidates] == [("DAYB", 600), ("WAT", 1)]
    assert load_gap_candidates(runtime_db, SESSION, limit=1) == (candidates[0],)
    assert load_gap_candidates(runtime_db, date(2026, 10, 10)) == ()  # a Saturday


def test_the_next_session_follows_the_calendar():
    assert next_session_after_close(EVENING) == NEXT
    friday_evening = datetime.combine(date(2026, 10, 16), time(17, 30), tzinfo=NEW_YORK)
    assert next_session_after_close(friday_evening) == date(2026, 10, 19)
    # Thanksgiving: Wednesday evening qualifies the Friday half day.
    assert next_session_after_close(datetime.combine(date(2026, 11, 25), time(17, 0), tzinfo=NEW_YORK)) == date(2026, 11, 27)
    session = market_session(date(2026, 11, 27))
    assert session is not None and session.half_day
    # Before the open, the session to qualify is today's.
    assert next_session_after_close(datetime.combine(NEXT, time(7, 0), tzinfo=NEW_YORK)) == NEXT


# --- the whole night, wired as `research record` runs it ------------------------------------------


class NightActor:
    """The research actor's surface: RTH history, daily history, extended bars."""

    instances: list["NightActor"] = []

    def __init__(self, config: Any) -> None:
        self.managed_accounts = frozenset({"DU1234567"})
        self.requests: list[tuple[str, int]] = []
        NightActor.instances.append(self)

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def intraday_trade_bars(self, con_id: int, *, duration: str) -> BarBatch:
        self.requests.append(("intraday", con_id))
        return BarBatch(bars=tuple(flat_session(con_id, SESSION)))

    async def daily_trade_bars(self, con_id: int) -> DailyBarBatch:
        self.requests.append(("daily", con_id))
        days = [SESSION - timedelta(days=offset) for offset in range(30, -1, -1)]
        return DailyBarBatch(
            bars=tuple(
                IBKRDailyBar(con_id=con_id, session_date=day, close=Decimal("50"), volume=Decimal("100000"),
                             high=Decimal("51"), low=Decimal("49"))
                for day in days
                if market_session(day) is not None
            )
        )

    async def extended_trade_bars(self, con_id: int) -> BarBatch:
        self.requests.append(("extended", con_id))
        return BarBatch(bars=tuple(gap_day(con_id, SESSION)))


@pytest.mark.asyncio
async def test_the_nightly_run_records_extends_and_qualifies_the_next_session(runtime_db, tmp_path, monkeypatch, capsys):
    from config import cfg
    import trading.operations.settings as settings

    root = tmp_path / "research"
    monkeypatch.setattr(bars_store.ResearchPaths, "default", classmethod(lambda cls: cls(root=root)))
    monkeypatch.setattr(cfg, "DB_PATH", str(runtime_db))
    monkeypatch.setattr(
        settings,
        "load_shadow_runtime_config",
        lambda: ShadowRuntimeConfig(
            paper_account_id="DU1234567", data_client_id=17, order_client_id=18,
            model_id="claude-opus-5-5", prompt_id="orb-review-v1", calendar_id="XNYS",
            calendar_version="2026.1", commission_policy_id="ibkr-fixed", commission_policy_version=1,
        ),
    )
    monkeypatch.setattr(recorder, "ResearchTWSActor", NightActor)
    monkeypatch.setattr(recorder, "DEFAULT_PACE_SECONDS", 0.0)
    monkeypatch.setattr(recorder, "QUALIFY_PACE_SECONDS", 0.0)
    with closing(sqlite3.connect(runtime_db)) as db:
        db.execute("DELETE FROM autotrader_universe_daily WHERE session_date = ?", (NEXT.isoformat(),))
        _snapshot(db, et(SESSION, 9, 40), [], [{"con_id": 600, "symbol": "DAYB", "primary_exchange": "NYSE", "gap_pct": "4.0"}])
        db.commit()
    NightActor.instances.clear()

    assert await recorder.run_backfill_cli(1, clock=lambda: EVENING) == 0

    (actor,) = NightActor.instances
    kinds = [kind for kind, _con_id in actor.requests]
    # RTH first (two requests each), then the gap candidate, then tomorrow's watchlist.
    assert kinds == ["intraday", "daily"] * 6 + ["extended"] + ["daily"] * 2
    store = BarsStore(ResearchPaths(root))
    assert set(store.list_con_ids(SESSION)) == {1, 2, 600, 601, 603, 700}
    assert store.list_con_ids(SESSION, extended=True) == (600,)
    with closing(sqlite3.connect(runtime_db)) as db:
        qualified = db.execute(
            "SELECT con_id, qualified, daily_atr FROM autotrader_universe_daily WHERE session_date = ? ORDER BY con_id",
            (NEXT.isoformat(),),
        ).fetchall()
    assert qualified == [(1, 1, "2.0000"), (2, 1, "2.0000")]
    out = capsys.readouterr().out
    assert "Extended-hours bars 2026-10-12: 1 gap candidates" in out
    assert "Next session 2026-10-13 qualified tonight: 2 of 2 instruments" in out


# --- GAP_GO's replay ----------------------------------------------------------------------------


def _gap_store(root: Path) -> BarsStore:
    store = BarsStore(ResearchPaths(root / "research"))
    for con_id, symbol, first_open in ((7, "GAPR", "104"), (8, "SMALL", "101")):
        store.write_day(stored(flat_session(con_id, PRIOR), day=PRIOR, con_id=con_id, symbol=symbol, use_rth=True))
        store.write_day(
            stored(gap_day(con_id, SESSION, first_open=first_open), day=SESSION, con_id=con_id, symbol=symbol, use_rth=False)
        )
    return store


def test_gap_go_replays_a_recorded_gap_and_go_like_the_live_rules(tmp_path):
    store = _gap_store(tmp_path)

    result = replay_strategy("gap", store)

    assert result.sessions == (SESSION,) and result.symbol_days == 2
    (trade,) = result.trades
    assert (trade.symbol, trade.strategy, trade.exit_reason) == ("GAPR", "gap_go", "target")
    assert trade.signal_at == et(SESSION, 9, 45)  # the 09:40 bar's close broke 104.5
    assert trade.entry == Decimal("104.80") and trade.r_multiple == Decimal("2.0000")
    assert result.outcomes == {"rejected:gap_too_small": 1, "trade:target": 1}
    assert result.metadata["rules_version"] == "GAP_GO_V1"


def test_gap_go_needs_the_prior_regular_session_for_its_atr(tmp_path):
    store = BarsStore(ResearchPaths(tmp_path / "research"))
    store.write_day(stored(gap_day(7, SESSION), day=SESSION, con_id=7, symbol="GAPR", use_rth=False))

    result = replay_strategy("gap", store)

    assert result.outcomes == {"skipped:prior_session_missing": 1} and result.trades == ()


def test_the_replay_cli_replays_gap_once_extended_days_exist(tmp_path, monkeypatch, capsys):
    root = tmp_path / "research"
    monkeypatch.setattr(bars_store.ResearchPaths, "default", classmethod(lambda cls: cls(root=root)))
    store = _gap_store(tmp_path)
    for con_id, symbol in ((7, "GAPR"), (8, "SMALL")):
        store.write_day(stored(flat_session(con_id, SESSION), day=SESSION, con_id=con_id, symbol=symbol, use_rth=True))

    assert run_replay_cli(["gap"]) == 0

    gap = json.loads((root / "reports" / "2026-10-12" / "replay-gap.json").read_text())
    assert gap["status"] == "replayed" and len(gap["trades"]) == 1
    assert gap["metadata"]["bars"].startswith("extended hours")
    assert "GAP (GAP_GO_V1)" in capsys.readouterr().out
