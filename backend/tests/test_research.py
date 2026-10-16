"""Research loop: bar store, read-only IBKR backfill, deterministic replay, R stats.

The replay tests run the real IBKR PAPER bars of 2026-10-09 and 2026-10-12
(tests/fixtures/momo, captured read-only from TWS) through ORB_V1,
MOMO_HOD_V2 and VWAP_PB_V1. The 2026-10-12 capture ends with the 15:15-15:20
ET bar, so a trade still open then is marked at that close (``data_end``),
never at an invented 15:45 price. GAP_GO needs pre-market bars the store
does not hold, so it is reported as not replayable rather than replayed.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import stat
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from trading.backtest.simulator import OHLCBar, simulate_bracket_trade
from trading.contracts import CompletedBar, TradeIntent
from trading.market.ibkr_boundary import (
    BarBatch,
    DailyBarBatch,
    IBKRDailyBar,
    IBKRTradeBar,
    liquidity_profile,
)
from trading.market.tws_actor import (
    TWSActorConfig,
    TWSConnectionError,
    TWSRequestError,
)
from trading.research import bars_store
from trading.research.bars_store import (
    BarsStore,
    BarsStoreError,
    ResearchPaths,
    StoredBar,
    StoredDay,
)
from trading.research.recorder import (
    Instrument,
    ResearchTWSActor,
    backfill,
    completed_sessions,
    daily_context,
    load_qualified_universe,
    record_today,
    request_days,
    run_backfill_cli,
    validate_duration,
)
from trading.research.replay import (
    ENTRY_TTL,
    EXTENDED_BAR_STRATEGIES,
    REGISTERED_STRATEGIES,
    RULES_VERSIONS,
    STRATEGY_FACTORIES,
    UNREPLAYABLE,
    UnreplayableStrategyError,
    flatten_at,
    no_extended_bars,
    replay_strategy,
    result_payload,
    run_replay_cli,
    simulate_bracket,
    text_report,
    write_reports,
)
from trading.research.stats import format_strategy, stats_payload, summarize
from trading.rules.momo import (
    EXTENDED_ABOVE_VWAP,
    MOMO_STRATEGY_KIND,
    MomoStrategy,
    xnys_session_close,
)
from trading.rules.orb import NO_SIGNAL_YET, QUOTE_REQUIRED
from trading.rules.strategy import ORBStrategy
from trading.rules.vwap_pb import (
    VWAP_PB_STRATEGY_KIND,
    VwapPbConfig,
    VwapPbStrategy,
    evaluate_vwap_pb,
)
from trading.scheduler.sessions import market_session

NEW_YORK = ZoneInfo("America/New_York")
UTC = timezone.utc
CAPTURES = Path(__file__).parent / "fixtures" / "momo"
SESSION = date(2026, 10, 12)
PRIOR = date(2026, 10, 9)
BAR = timedelta(minutes=5)
VOLUME_MULTIPLIER = Decimal("100")
PACING_TEXT = (
    "TWS error 162: Historical Market Data Service error message:"
    "Historical data request pacing violation"
)
NO_DATA_TEXT = (
    "TWS error 162: Historical Market Data Service error message:"
    "HMDS query returned no data: XYZ@SMART Trades"
)
COMPETING_TEXT = (
    "TWS error 162: Historical Market Data Service error message:"
    "Trading TWS session is connected from a different IP address"
)


def _et(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=NEW_YORK).astimezone(UTC)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# --- real captures ------------------------------------------------------------


@dataclass(frozen=True)
class _Capture:
    symbol: str
    con_id: int
    primary_exchange: str
    bars: tuple[IBKRTradeBar, ...]
    daily: DailyBarBatch


def _capture(name: str) -> _Capture:
    raw = json.loads((CAPTURES / f"{name}_2026-10-12.json").read_text())
    con_id = raw["con_id"]
    bars = tuple(
        IBKRTradeBar(
            con_id=con_id,
            interval_start_utc=datetime.fromisoformat(item["interval_start_utc"]),
            interval_end_utc=datetime.fromisoformat(item["interval_end_utc"]),
            open=Decimal(item["open"]),
            high=Decimal(item["high"]),
            low=Decimal(item["low"]),
            close=Decimal(item["close"]),
            volume=Decimal(item["volume"]),
            rth=True,
        )
        for item in raw["bars"]
    )
    daily = DailyBarBatch(
        bars=tuple(
            IBKRDailyBar(
                con_id=con_id,
                session_date=date.fromisoformat(item["session_date"]),
                close=Decimal(item["close"]),
                volume=Decimal(item["volume"]),
            )
            for item in raw["daily_bars"]
        )
    )
    return _Capture(raw["symbol"], con_id, raw["primary_exchange"], bars, daily)


BF_B = _capture("bf_b")
FIGS = _capture("figs")


def _stored_day(capture: _Capture, session_date: date, **overrides: Any) -> StoredDay:
    prior_close, average = daily_context(capture.daily, session_date)
    values: dict[str, Any] = {
        "session_date": session_date,
        "con_id": capture.con_id,
        "symbol": capture.symbol,
        "bars": tuple(
            StoredBar(
                interval_start_utc=bar.interval_start_utc,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            )
            for bar in capture.bars
            if bar.interval_start_utc.astimezone(NEW_YORK).date() == session_date
        ),
        "prior_close": prior_close,
        "avg_dollar_volume": average,
        "primary_exchange": capture.primary_exchange,
    }
    values.update(overrides)
    return StoredDay(**values)


def _capture_store(root: Path, captures: tuple[_Capture, ...] = (BF_B, FIGS)) -> BarsStore:
    store = BarsStore(ResearchPaths(root / "research"))
    for capture in captures:
        for session_date in (PRIOR, SESSION):
            store.write_day(_stored_day(capture, session_date))
    return store


# --- bar store ----------------------------------------------------------------


def test_store_round_trip_keeps_exact_decimals_raw_volume_and_identity(tmp_path):
    store = BarsStore(ResearchPaths(tmp_path / "research"))
    day = _stored_day(BF_B, SESSION, fetched_at=datetime(2026, 10, 12, 21, 0, tzinfo=UTC))
    path = store.write_day(day)

    assert path == tmp_path / "research" / "bars" / "2026-10-12" / "4931.json"
    assert store.read_day(SESSION, 4931) == day
    payload = json.loads(path.read_text())
    assert (payload["symbol"], payload["con_id"], payload["session_date"]) == (
        "BF B",
        4931,
        "2026-10-12",
    )
    # 20-session ADV and prior close exactly as live qualification derives them.
    profile = liquidity_profile(
        BF_B.daily, session_date=SESSION, volume_multiplier=VOLUME_MULTIPLIER
    )
    assert payload["prior_close"] == "26.39" == str(profile.reference_price)
    assert Decimal(payload["avg_dollar_volume"]) == profile.average_dollar_volume.quantize(
        Decimal("0.01")
    )
    first = payload["bars"][0]
    assert first == {
        "interval_start_utc": "2026-10-12T13:30:00+00:00",
        "interval_end_utc": "2026-10-12T13:35:00+00:00",
        "open": "26.45",
        "high": "26.56",
        "low": "26.27",
        "close": "26.56",
        # Raw TWS lots, never converted to shares in the store.
        "volume": "1338",
        "rth": True,
    }
    assert len(payload["bars"]) == 70
    completed = store.read_day(SESSION, 4931).completed_bars()
    assert all(isinstance(bar, CompletedBar) and bar.con_id == 4931 for bar in completed)
    assert completed[-1].interval_end_utc == _et(SESSION, 15, 20)


def test_store_is_private_atomic_and_never_overwrites_silently(tmp_path):
    root = tmp_path / "research"
    store = BarsStore(ResearchPaths(root))
    assert not root.exists()  # construction touches nothing
    path = store.write_day(_stored_day(BF_B, SESSION))

    for directory in (root, root / "bars", root / "bars" / "2026-10-12"):
        assert _mode(directory) == 0o700
    assert _mode(path) == 0o600
    assert sorted(os.listdir(path.parent)) == ["4931.json"]  # no temporary file left

    with pytest.raises(BarsStoreError, match="already stored"):
        store.write_day(_stored_day(BF_B, SESSION))
    replacement = _stored_day(BF_B, SESSION, prior_close=Decimal("1"))
    store.write_day(replacement, overwrite=True)
    assert store.read_day(SESSION, 4931).prior_close == Decimal("1")
    assert _mode(path) == 0o600


def test_store_reads_fail_closed_on_tampered_misfiled_or_linked_days(tmp_path):
    store = _capture_store(tmp_path, (BF_B,))
    path = store.path_for(SESSION, BF_B.con_id)
    original = json.loads(path.read_text())

    def tamper(mutate) -> None:
        payload = json.loads(json.dumps(original))
        mutate(payload)
        path.write_text(json.dumps(payload))

    tamper(lambda payload: payload["bars"][0].update(high="26.00"))
    with pytest.raises(BarsStoreError, match="range"):
        store.read_day(SESSION, BF_B.con_id)
    tamper(lambda payload: payload["bars"][0].update(open=26.45))
    with pytest.raises(BarsStoreError, match="decimal text"):
        store.read_day(SESSION, BF_B.con_id)
    tamper(lambda payload: payload.update(con_id=99))
    with pytest.raises(BarsStoreError, match="another con_id"):
        store.read_day(SESSION, BF_B.con_id)
    tamper(lambda payload: payload.update(version=2))
    with pytest.raises(BarsStoreError, match="schema"):
        store.read_day(SESSION, BF_B.con_id)
    path.write_text("{not json")
    with pytest.raises(BarsStoreError, match="JSON"):
        store.read_day(SESSION, BF_B.con_id)

    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps(original))
    path.unlink()
    path.symlink_to(elsewhere)
    assert store.has_day(SESSION, BF_B.con_id) is False
    with pytest.raises(BarsStoreError, match="not stored"):
        store.read_day(SESSION, BF_B.con_id)


def test_stored_day_rejects_bars_outside_its_session_or_out_of_order():
    day = _stored_day(BF_B, SESSION)
    with pytest.raises(BarsStoreError, match="session date"):
        StoredDay(session_date=PRIOR, con_id=day.con_id, symbol=day.symbol, bars=day.bars)
    with pytest.raises(BarsStoreError, match="ordered"):
        StoredDay(
            session_date=SESSION,
            con_id=day.con_id,
            symbol=day.symbol,
            bars=(day.bars[1], day.bars[0]),
        )
    with pytest.raises(BarsStoreError, match="ordered"):
        StoredDay(
            session_date=SESSION,
            con_id=day.con_id,
            symbol=day.symbol,
            bars=(day.bars[0], day.bars[0]),
        )
    with pytest.raises(BarsStoreError, match="at least one bar"):
        StoredDay(session_date=SESSION, con_id=day.con_id, symbol=day.symbol, bars=())
    with pytest.raises(BarsStoreError, match="positive"):
        StoredDay(
            session_date=SESSION,
            con_id=day.con_id,
            symbol=day.symbol,
            bars=day.bars,
            prior_close=Decimal("0"),
        )


def test_store_lists_sessions_and_contracts_and_ignores_stray_files(tmp_path):
    store = _capture_store(tmp_path)
    session_directory = store.paths.session_directory(SESSION)
    (session_directory / ".4931.json.0123.tmp").write_text("partial")
    (session_directory / "notes.txt").write_text("x")
    (store.paths.bars_directory / "not-a-date").mkdir()
    store.paths.ensure_private(store.paths.bars_directory / "2026-10-13")  # empty session

    assert store.list_sessions() == (PRIOR, SESSION)
    assert store.list_con_ids(SESSION) == (4931, 493581450)
    assert store.stored_sessions(4931) == frozenset({PRIOR, SESSION})
    assert store.get_day(date(2026, 10, 8), 4931) is None
    assert [day.symbol for day in store.iter_days(PRIOR)] == ["BF B", "FIGS"]
    with pytest.raises(BarsStoreError, match="research root"):
        store.paths.ensure_private(tmp_path / "outside")


# --- replay on real bars --------------------------------------------------------


def test_replay_orb_on_real_bars(tmp_path):
    result = replay_strategy("orb", _capture_store(tmp_path))

    assert result.sessions == (SESSION,)
    assert (result.symbols, result.symbol_days) == (2, 2)
    # 2026-10-09 has no stored prior session, so it only serves as history.
    assert result.outcomes == {
        "skipped:prior_session_missing": 2,
        "trade:stop": 1,
        "trade:target": 1,
    }
    bf_b, figs = result.trades
    # BF B: the 09:55-10:00 bar closed at 26.60 over the 26.59 opening-range
    # high; the limit filled when the 10:00 bar (opened 26.61) traded down to
    # it, and the 10:05 bar's 26.51 low touched the 1 ATR stop.
    assert bf_b.to_payload() == {
        "date": "2026-10-12",
        "symbol": "BF B",
        "con_id": 4931,
        "strategy": "orb",
        "signal_at_et": "10:00",
        "entry": "26.6",
        "stop": "26.51",
        "target": "26.78",
        "filled_bar_et": "10:00",
        "exit_at_et": "10:10",
        "exit": "26.51",
        "exit_reason": "stop",
        "r_multiple": "-1.0000",
        "bars_held": 2,
    }
    # FIGS: breakout at 10:15 ET, filled at the next bar's 16.93 open, and the
    # 11:35-11:40 bar printed the 17.25 target.
    assert figs.to_payload() == {
        "date": "2026-10-12",
        "symbol": "FIGS",
        "con_id": 493581450,
        "strategy": "orb",
        "signal_at_et": "10:15",
        "entry": "16.93",
        "stop": "16.77",
        "target": "17.25",
        "filled_bar_et": "10:15",
        "exit_at_et": "11:40",
        "exit": "17.25",
        "exit_reason": "target",
        "r_multiple": "2.0000",
        "bars_held": 17,
    }
    session_bars = {
        bar.interval_start_utc.astimezone(NEW_YORK).strftime("%H:%M"): bar
        for bar in FIGS.bars
        if bar.interval_start_utc.astimezone(NEW_YORK).date() == SESSION
    }
    held = [
        bar
        for clock, bar in session_bars.items()
        if "10:15" <= clock < "11:35"
    ]
    # Nothing between the fill and the target bar touched either exit.
    assert min(bar.low for bar in held) > figs.stop
    assert max(bar.high for bar in held) < figs.target
    assert session_bars["11:35"].high == figs.target


def test_replay_momo_v2_on_real_bars_reports_the_bf_b_trade_honestly(tmp_path):
    result = replay_strategy("momo", _capture_store(tmp_path))

    assert result.outcomes == {
        "no_setup:NO_SIGNAL_YET": 1,
        "skipped:prior_session_missing": 2,
        "trade:data_end": 1,
    }
    (trade,) = result.trades
    # The live 11:50 ET signal: entry at the 26.76 signal close (the synthetic
    # ask; live paid one tick more), the V2 stop one tick under the 26.52
    # signal-bar low, and a 2R target on the rounded stop.
    assert trade.to_payload() == {
        "date": "2026-10-12",
        "symbol": "BF B",
        "con_id": 4931,
        "strategy": MOMO_STRATEGY_KIND,
        "signal_at_et": "11:50",
        "entry": "26.76",
        "stop": "26.51",
        "target": "27.26",
        "filled_bar_et": "11:50",
        "exit_at_et": "15:20",
        "exit": "26.73",
        "exit_reason": "data_end",
        "r_multiple": "-0.1200",
        "bars_held": 42,
    }
    # Honest exit: the capture stops at 15:20 ET with neither exit touched,
    # so the open trade is marked at the last close, not at a 15:45 price.
    after = [bar for bar in BF_B.bars if bar.interval_start_utc >= _et(SESSION, 11, 50)]
    assert after[-1].interval_end_utc == _et(SESSION, 15, 20) < flatten_at(SESSION)
    assert min(bar.low for bar in after) == Decimal("26.59") > trade.stop
    assert max(bar.high for bar in after) < trade.target
    assert after[0].open <= trade.entry  # filled at the 11:50 open
    # FIGS: its signals sat too far above VWAP all day (Claude abstained live).
    assert result.skip_reasons == {EXTENDED_ABOVE_VWAP: 1}
    assert result.unfilled == ()


@dataclass
class _Spy:
    """Wraps a strategy and records every evaluation the replay makes."""

    inner: Any
    calls: list[tuple[dict[str, Any], Any]] = field(default_factory=list)

    @property
    def strategy_kind(self) -> str:
        return self.inner.strategy_kind

    def entry_window(self, session_date: date) -> tuple[datetime, datetime]:
        return self.inner.entry_window(session_date)

    def evaluate(self, **kwargs: Any) -> Any:
        result = self.inner.evaluate(**kwargs)
        self.calls.append((kwargs, result))
        return result


@pytest.mark.parametrize(
    "inner",
    [
        ORBStrategy(),
        MomoStrategy(),
        # A wide pullback band so FIGS's 13:25 reclaim asks for a quote.
        VwapPbStrategy(VwapPbConfig(pullback_ceiling_atr=Decimal("3"))),
    ],
    ids=["orb", "momo", "vwap"],
)
def test_replay_feeds_only_completed_bars_and_synthetic_quotes(tmp_path, inner):
    spy = _Spy(inner)
    replay_strategy(inner.strategy_kind, _capture_store(tmp_path), strategy=spy)
    start, cutoff = inner.entry_window(SESSION)

    assert spy.calls
    previous: tuple[dict[str, Any], Any] | None = None
    for kwargs, result in spy.calls:
        now, bars, quote = kwargs["now"], kwargs["bars"], kwargs["quote"]
        latest = bars[-1]
        assert all(bar.interval_end_utc <= now for bar in bars)
        assert latest.interval_end_utc == now - timedelta(seconds=5)
        assert start <= latest.interval_end_utc and now < cutoff
        # History is the previous session plus today, like the live 2 D request.
        assert bars[0].interval_start_utc == _et(PRIOR, 9, 30)
        assert kwargs["session_date"] == SESSION
        if quote is not None:
            assert (quote.ask, quote.bid) == (latest.close, latest.close - Decimal("0.01"))
            assert quote.observed_at == now and quote.min_tick == Decimal("0.01")
            # A quote is offered only to the poll that asked for one.
            assert previous is not None
            assert previous[0]["now"] == now and previous[0]["quote"] is None
            assert previous[1].rejection_reason == QUOTE_REQUIRED
        previous = (kwargs, result)


def test_terminal_rule_rejection_ends_the_symbol_day(tmp_path):
    store = BarsStore(ResearchPaths(tmp_path / "research"))
    store.write_day(_stored_day(BF_B, PRIOR))
    store.write_day(_stored_day(BF_B, SESSION, avg_dollar_volume=Decimal("1000000")))
    spy = _Spy(ORBStrategy())

    result = replay_strategy("orb", store, strategy=spy)

    assert result.outcomes == {
        "rejected:candidate_dollar_volume_too_low": 1,
        "skipped:prior_session_missing": 1,
    }
    assert len(spy.calls) == 1  # a final decision is never re-polled
    assert result.trades == ()


def test_replay_skips_days_without_liquidity_and_is_deterministic(tmp_path):
    store = _capture_store(tmp_path)
    store.write_day(_stored_day(FIGS, SESSION, avg_dollar_volume=None), overwrite=True)

    first = replay_strategy("momo", store)
    second = replay_strategy("momo", store)

    assert first.outcomes["skipped:liquidity_unknown"] == 1
    stamp = datetime(2026, 10, 12, 22, 0, tzinfo=UTC)
    assert result_payload(first, generated_at=stamp) == result_payload(second, generated_at=stamp)
    with pytest.raises(ValueError, match="unknown strategy"):
        replay_strategy("bogus", store)


@dataclass
class _VwapWhy:
    """VWAP_PB_V1 exactly as ``VwapPbStrategy`` evaluates it, keeping the first
    rule each poll's latest bar failed (the pipeline only sees NO_SIGNAL_YET)."""

    strategy: VwapPbStrategy = field(default_factory=VwapPbStrategy)
    unmet: dict[str, Counter[str]] = field(default_factory=dict)

    @property
    def strategy_kind(self) -> str:
        return self.strategy.strategy_kind

    def entry_window(self, session_date: date) -> tuple[datetime, datetime]:
        return self.strategy.entry_window(session_date)

    def evaluate(self, **kwargs: Any) -> Any:
        result = evaluate_vwap_pb(
            **kwargs,
            config=self.strategy.config,
            session_close=xnys_session_close(kwargs["session_date"]),
        )
        symbol = kwargs["candidate"].symbol
        self.unmet.setdefault(symbol, Counter())[result.unmet or result.rejection_reason] += 1
        return result.as_strategy_result()


def test_replay_vwap_pb_on_real_bars_finds_no_reclaim_and_says_why(tmp_path):
    store = _capture_store(tmp_path)
    why = _VwapWhy()

    result = replay_strategy("vwap", store, strategy=why)

    assert result.strategy_kind == VWAP_PB_STRATEGY_KIND
    assert result.outcomes == {
        f"no_setup:{NO_SIGNAL_YET}": 2,
        "skipped:prior_session_missing": 2,
    }
    assert result.trades == () and result.unfilled == () and result.skip_reasons == {}
    # Each symbol was polled at every bar end 10:30-14:25 ET (48 polls) and
    # never priced. BF B only became a trend day late; FIGS ran +8-12% all day
    # but never pulled back to VWAP and reclaimed it on volume.
    assert why.unmet == {
        "BF B": {
            "day_change_too_low": 17,
            "too_few_bars_above_vwap": 16,
            "close_not_above_previous_high": 10,
            "signal_volume_too_low": 5,
        },
        "FIGS": {
            "close_not_above_previous_high": 34,
            "signal_volume_too_low": 12,
            "pullback_missed_vwap": 2,
        },
    }
    # The registered factory replays the same rules to the same result.
    default = replay_strategy("vwap", store)
    assert default.outcomes == result.outcomes
    assert default.metadata["rules_version"] == "VWAP_PB_V1"


def test_replay_simulates_a_vwap_pb_setup_like_any_other(tmp_path):
    # A pullback band of 3 ATR above VWAP (live: 0.25) lets FIGS's 13:25
    # reclaim through, so a VWAP_PB setup takes the bracket path on real bars.
    loose = VwapPbStrategy(VwapPbConfig(pullback_ceiling_atr=Decimal("3")))

    result = replay_strategy("vwap", _capture_store(tmp_path), strategy=loose)

    assert result.outcomes == {
        f"no_setup:{NO_SIGNAL_YET}": 1,
        "skipped:prior_session_missing": 2,
        "trade:data_end": 1,
    }
    (trade,) = result.trades
    # Stop 0.1 ATR under the pullback low, 2R target on the rounded stop; the
    # capture ends at 15:20 with neither exit touched.
    assert trade.to_payload() == {
        "date": "2026-10-12",
        "symbol": "FIGS",
        "con_id": 493581450,
        "strategy": VWAP_PB_STRATEGY_KIND,
        "signal_at_et": "13:25",
        "entry": "17.59",
        "stop": "17.33",
        "target": "18.11",
        "filled_bar_et": "13:25",
        "exit_at_et": "15:20",
        "exit": "17.71",
        "exit_reason": "data_end",
        "r_multiple": "0.4615",
        "bars_held": 23,
    }


def test_gap_go_replays_only_extended_days_and_never_fakes_without_them(tmp_path):
    store = _capture_store(tmp_path)

    result = replay_strategy("gap", store)

    # Regular-hours days alone feed nothing: no pre-market, so no GAP sample.
    assert (result.sessions, result.trades, result.symbol_days, result.outcomes) == ((), (), 0, {})
    assert result.metadata["bars"].startswith("extended hours")
    # GAP_GO (V1) replays; only GAP_GO_V2's all-session replay is not wired.
    assert "gap" not in UNREPLAYABLE and set(UNREPLAYABLE) <= {"gap-v2"}
    assert no_extended_bars("gap").reason == "needs_extended_bars"
    with pytest.raises(UnreplayableStrategyError):
        with pytest.MonkeyPatch.context() as patch:
            patch.setitem(UNREPLAYABLE, "gap", no_extended_bars("gap"))
            replay_strategy("gap", store, strategy=ORBStrategy())


def test_replay_covers_every_registered_live_strategy():
    from trading.operations import shadow_service

    # A managed entry (AI day manager) runs its family's rules: the rules-only
    # replay covers it as that family.
    rule_sets = tuple(
        name for name in shadow_service.STRATEGIES if name not in shadow_service.MANAGED_STRATEGIES
    )
    for name in shadow_service.MANAGED_STRATEGIES:
        family = shadow_service.strategy_family(name)
        assert family in rule_sets
        assert shadow_service.STRATEGIES[name][0] is shadow_service.STRATEGIES[family][0]
    assert REGISTERED_STRATEGIES == rule_sets
    replayable = {**STRATEGY_FACTORIES, **EXTENDED_BAR_STRATEGIES}
    assert set(replayable) | set(UNREPLAYABLE) == set(rule_sets)
    assert not set(STRATEGY_FACTORIES) & set(EXTENDED_BAR_STRATEGIES)
    assert not set(replayable) & set(UNREPLAYABLE)
    assert set(RULES_VERSIONS) == set(rule_sets)
    for name, factory in replayable.items():
        # The replay runs the live evaluator class itself, never a copy.
        assert factory is shadow_service.STRATEGIES[name][0]


# --- bracket simulation ------------------------------------------------------------


def _bar(hour: int, minute: int, o: str, h: str, low: str, c: str) -> CompletedBar:
    start = _et(SESSION, hour, minute)
    return CompletedBar(
        con_id=4242,
        interval_start_utc=start,
        interval_end_utc=start + BAR,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal("100"),
        rth=True,
        source="ibkr",
        data_mode="historical",
    )


def _simulate(bars: list[CompletedBar], **overrides: Any):
    values: dict[str, Any] = {
        "entry": Decimal("100"),
        "stop": Decimal("99"),
        "target": Decimal("102"),
        "placed_after": _et(SESSION, 10, 0),
        "bars": bars,
        "entry_cutoff": _et(SESSION, 15, 0),
        "flatten": flatten_at(SESSION),
    }
    values.update(overrides)
    return simulate_bracket(**values)


def test_bracket_target_on_the_fill_bar_counts_only_after_a_fill_at_the_open():
    at_open = _simulate([_bar(10, 0, "99.90", "102.10", "99.50", "102")])
    assert at_open is not None
    assert (at_open.exit_reason, at_open.exit_price, at_open.bars_held) == (
        "target",
        Decimal("102"),
        1,
    )
    # Opened above the limit: the 102.50 high may have printed before the
    # dip that filled the entry, so only the next bar can decide.
    mid_bar = _simulate(
        [
            _bar(10, 0, "100.50", "102.50", "99.90", "101"),
            _bar(10, 5, "101", "101.50", "98.90", "99"),
        ]
    )
    assert mid_bar is not None
    assert (mid_bar.exit_reason, mid_bar.exit_price, mid_bar.bars_held) == (
        "stop",
        Decimal("99"),
        2,
    )
    assert mid_bar.filled_at == _et(SESSION, 10, 0)
    assert mid_bar.exit_at == _et(SESSION, 10, 10)


def test_bracket_stop_comes_first_and_a_gap_exits_at_the_open():
    both = _simulate(
        [
            _bar(10, 0, "100", "100.50", "99.80", "100.20"),
            _bar(10, 5, "100.20", "102.50", "98.50", "101"),
        ]
    )
    assert both is not None and (both.exit_reason, both.exit_price) == ("stop", Decimal("99"))
    gap = _simulate(
        [
            _bar(10, 0, "100", "100.50", "99.80", "100.20"),
            _bar(10, 5, "98.50", "98.80", "98.00", "98.40"),
        ]
    )
    assert gap is not None and (gap.exit_reason, gap.exit_price) == ("stop", Decimal("98.50"))


def test_bracket_entry_expires_after_its_ttl_or_at_the_cutoff():
    late = [
        _bar(10, 0, "100.40", "100.60", "100.20", "100.50"),
        _bar(10, 5, "100.50", "100.70", "100.10", "100.40"),
        _bar(10, 10, "100.40", "100.50", "99.80", "100"),
    ]
    assert ENTRY_TTL == timedelta(minutes=10)
    assert _simulate(late) is None  # the third bar is past the 10-minute TTL
    assert _simulate(late, entry_ttl=timedelta(minutes=15)) is not None
    # Signalled at 10:05 with a 10:10 cutoff: only the 10:05 bar may fill.
    cut_off = _simulate(
        late[1:], placed_after=_et(SESSION, 10, 5), entry_cutoff=_et(SESSION, 10, 10)
    )
    assert cut_off is None
    with pytest.raises(ValueError, match="stop < entry < target"):
        _simulate(late, stop=Decimal("100"))


def test_bracket_flattens_at_the_1545_close_or_marks_the_last_bar():
    day = [_bar(10, 0, "100", "100.50", "99.80", "100.20")]
    minutes = [5 + 5 * index for index in range(68)]  # 10:05 .. 15:40 bar starts
    day += [
        _bar(10 + offset // 60, offset % 60, "100.20", "100.60", "99.60", "100.30")
        for offset in minutes
    ]
    assert day[-1].interval_end_utc == _et(SESSION, 15, 45)
    day.append(_bar(15, 45, "100.30", "103", "100.20", "102.90"))  # after the flatten
    closed = _simulate(day)
    assert closed is not None
    assert (closed.exit_reason, closed.exit_price, closed.exit_at) == (
        "time",
        Decimal("100.30"),
        _et(SESSION, 15, 45),
    )
    assert closed.bars_held == 69
    marked = _simulate(day[:24])
    assert marked is not None
    assert (marked.exit_reason, marked.exit_at) == ("data_end", _et(SESSION, 12, 0))
    # A quiet contract with no 15:40-15:45 bar still flattens at 15:45, at
    # the last price before it, because its data continues past the flatten.
    quiet = _simulate([*day[:-2], day[-1]])
    assert quiet is not None
    assert (quiet.exit_reason, quiet.exit_at) == ("time", _et(SESSION, 15, 40))
    # Half days flatten 15 minutes before the early close.
    assert flatten_at(date(2026, 11, 27)) == _et(date(2026, 11, 27), 12, 45)
    assert market_session(date(2026, 11, 27)).half_day is True


@pytest.mark.parametrize(
    "exit_bar",
    [
        ("101", "102.40", "100.60", "102.20"),  # target
        ("100.40", "100.60", "98.90", "99.10"),  # stop
        ("98.70", "98.80", "98.10", "98.20"),  # gap through the stop
    ],
)
def test_bracket_agrees_with_the_backtest_simulator_where_both_apply(exit_bar):
    """After a quiet fill bar the conservative rules equal simulate_bracket_trade."""
    bars = [
        _bar(10, 0, "100.20", "100.40", "99.95", "100.10"),
        _bar(10, 5, "100.10", "100.50", "99.70", "100.30"),
        _bar(10, 10, *exit_bar),
    ]
    ours = _simulate(bars)
    intent = TradeIntent(
        execution_key="research-cross-check",
        setup_fingerprint="a" * 64,
        approval_token="t" * 32,
        expires_at=_et(SESSION, 10, 1),
        strategy_id="research",
        strategy_version=1,
        symbol="SYN",
        quantity=1,
        entry_limit=Decimal("100"),
        stop_price=Decimal("99"),
        target_price=Decimal("102"),
    )
    theirs = simulate_bracket_trade(
        signal_at=_et(SESSION, 9, 55),
        intent=intent,
        bars=[
            OHLCBar(
                timestamp=bar.interval_start_utc,
                symbol="SYN",
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
            )
            for bar in bars
        ],
    )
    assert ours is not None and theirs.status == "closed"
    assert (ours.exit_reason, ours.exit_price) == (theirs.exit_reason, theirs.exit_price)
    assert ours.filled_at == theirs.entry_at
    assert ours.exit_at - BAR == theirs.exit_at


# --- statistics ------------------------------------------------------------------


@dataclass(frozen=True)
class _Trade:
    session_date: date
    symbol: str
    exit_at: datetime
    exit_reason: str
    r_multiple: Decimal


TRADES = (
    _Trade(date(2026, 10, 1), "AAA", _et(date(2026, 10, 1), 10, 30), "target", Decimal("2")),
    _Trade(date(2026, 10, 1), "BBB", _et(date(2026, 10, 1), 11, 0), "stop", Decimal("-1")),
    _Trade(date(2026, 10, 2), "AAA", _et(date(2026, 10, 2), 10, 0), "stop", Decimal("-1")),
    _Trade(date(2026, 10, 2), "CCC", _et(date(2026, 10, 2), 14, 0), "time", Decimal("0.5")),
    _Trade(date(2026, 10, 5), "AAA", _et(date(2026, 10, 5), 15, 45), "time", Decimal("0")),
    _Trade(date(2026, 10, 5), "BBB", _et(date(2026, 10, 5), 12, 0), "stop", Decimal("-1")),
)


def test_stats_math_matches_a_hand_computed_ledger():
    result = summarize("momo", reversed(TRADES), unfilled=2, symbol_days=40, sessions=3)

    assert (result.trades, result.wins, result.losses, result.scratches) == (6, 2, 3, 1)
    assert result.win_rate == Decimal(2) / Decimal(6)
    assert result.total_r == Decimal("-0.5")
    assert result.average_r == Decimal("-0.5") / Decimal(6)
    # Expectancy from its decomposition: 1/3 x 1.25R + 2/3 x -0.75R.
    assert result.average_win_r == Decimal("1.25")
    assert result.average_loss_r == Decimal("-1")
    assert result.expectancy_r is not None and result.average_r is not None
    assert result.expectancy_r.quantize(Decimal("1E-20")) == result.average_r.quantize(
        Decimal("1E-20")
    )
    assert result.profit_factor == Decimal("2.5") / Decimal("3")
    # Realized in exit order: +2, -1, -1, +0.5, -1 (10-05 12:00), 0 -> peak 2, trough -0.5.
    assert result.max_drawdown_r == Decimal("2.5")
    assert result.best_day == (date(2026, 10, 1), Decimal("1"))
    assert result.worst_day == (date(2026, 10, 5), Decimal("-1"))
    assert result.by_symbol == (
        ("AAA", 3, Decimal("1")),
        ("BBB", 2, Decimal("-2")),
        ("CCC", 1, Decimal("0.5")),
    )
    assert result.exit_reasons == (("stop", 3), ("time", 2), ("target", 1))

    payload = stats_payload(result)
    assert payload["win_rate"] == "0.3333"
    assert payload["average_r"] == payload["expectancy_r"] == "-0.0833"
    assert payload["profit_factor"] == "0.8333"
    assert payload["max_drawdown_r"] == "2.5000"
    assert payload["best_day"] == {"date": "2026-10-01", "r": "1.0000"}
    assert payload["by_symbol"][0] == {"symbol": "AAA", "trades": 3, "total_r": "1.0000"}
    assert (payload["unfilled"], payload["symbol_days"], payload["sessions"]) == (2, 40, 3)


def test_stats_without_trades_or_without_losses():
    empty = summarize("orb", [])
    assert (empty.trades, empty.win_rate, empty.profit_factor, empty.max_drawdown_r) == (
        0,
        None,
        None,
        Decimal("0"),
    )
    assert empty.best_day is None and "no filled trades" in format_strategy(empty)

    winners = summarize("orb", TRADES[:1] + TRADES[3:4])
    assert winners.profit_factor is None and winners.max_drawdown_r == Decimal("0")
    text = format_strategy(winners, label="ORB (ORB_V1)")
    assert "profit factor n/a (no losses)" in text
    assert text.splitlines()[0] == "ORB (ORB_V1)"


def test_text_report_is_plain_telegram_text(tmp_path):
    store = _capture_store(tmp_path)
    report = text_report([replay_strategy(name, store) for name in ("orb", "momo")])

    assert report.splitlines()[0] == "Research replay 2026-10-12..2026-10-12: 1 sessions, 2 symbols"
    assert "Claude review not simulated" in report
    assert "ORB (ORB_V1)\n  2 trades from 2 symbol-days" in report
    assert "MOMO (MOMO_HOD_V2)\n  1 trade from 2 symbol-days" in report
    assert "win rate 50.0% | avg R +0.50R | expectancy +0.50R/trade" in report
    assert "exits: data_end 1" in report
    assert "signals skipped (symbol-days): extended_above_vwap 1" in report
    assert not any(marker in report for marker in ("*", "`", "_["))
    assert len(report) < 4096


def test_reports_are_private_and_carry_the_baseline_caveats(tmp_path):
    store = _capture_store(tmp_path)
    results = [replay_strategy(name, store) for name in ("orb", "momo")]
    stamp = datetime(2026, 10, 12, 22, 0, tzinfo=UTC)

    written = write_reports(results, paths=store.paths, generated_at=stamp)

    directory = tmp_path / "research" / "reports" / "2026-10-12"
    assert [Path(path).name for path in written] == [
        "replay-orb.json",
        "replay-momo.json",
        "summary.txt",
    ]
    assert _mode(directory) == 0o700 and _mode(directory.parent) == 0o700
    assert all(_mode(Path(path)) == 0o600 for path in written)
    payload = json.loads((directory / "replay-momo.json").read_text())
    assert payload["metadata"]["baseline"] == "rule-only (Claude review not simulated)"
    assert payload["metadata"]["quotes"].startswith("synthetic")
    assert payload["metadata"]["rules_version"] == "MOMO_HOD_V2"
    assert payload["coverage"] == {"sessions": 1, "symbols": 2, "symbol_days": 2}
    assert payload["stats"]["trades"] == 1
    assert payload["trades"][0]["exit_reason"] == "data_end"
    assert payload["generated_at"] == "2026-10-12T22:00:00+00:00"


def test_run_replay_cli_writes_reports_under_the_research_root(tmp_path, monkeypatch, capsys):
    root = tmp_path / "research"
    monkeypatch.setattr(
        bars_store.ResearchPaths, "default", classmethod(lambda cls: cls(root=root))
    )
    assert run_replay_cli(["momo"]) == 1  # nothing stored yet
    _capture_store(tmp_path)

    assert run_replay_cli(["bogus"]) == 2
    assert run_replay_cli(["momo", "orb"]) == 0
    output = capsys.readouterr().out
    assert "MOMO (MOMO_HOD_V2)" in output and "ORB (ORB_V1)" in output
    assert str(root / "reports" / "2026-10-12" / "summary.txt") in output
    assert (root / "reports" / "2026-10-12" / "replay-orb.json").is_file()


def _research_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "research"
    monkeypatch.setattr(
        bars_store.ResearchPaths, "default", classmethod(lambda cls: cls(root=root))
    )
    return root


def test_run_replay_cli_reports_every_registered_strategy_and_skips_gap_explicitly(
    tmp_path, monkeypatch, capsys
):
    from trading.research.nightly import NightlyEnvironment

    root = _research_root(tmp_path, monkeypatch)
    _capture_store(tmp_path)
    # The nightly evidence run that follows a full replay, on paths of its own.
    environment = NightlyEnvironment(
        database=tmp_path / "no-database.db",
        bridge_directory=tmp_path / "bridge",
        clock=lambda: datetime(2026, 10, 12, 21, 40, tzinfo=UTC),
    )

    assert run_replay_cli([], environment=environment) == 0

    directory = root / "reports" / "2026-10-12"
    assert sorted(path.name for path in directory.iterdir()) == [
        "evidence.json",
        "evidence.txt",
        "live-r.json",
        "proposals.json",
        "replay-gap-v2.json",
        "replay-gap.json",
        "replay-momo.json",
        "replay-orb-v2.json",
        "replay-orb.json",
        "replay-vwap-v2.json",
        "replay-vwap.json",
        "summary.txt",
        "windows.json",
    ]
    assert not (tmp_path / "no-database.db").exists()  # the tracker never creates one
    gap = json.loads((directory / "replay-gap.json").read_text())
    assert (gap["status"], gap["reason"], gap["rules_version"]) == (
        "skipped",
        "needs_extended_bars",
        "GAP_GO_V1",
    )
    assert "stats" not in gap and "trades" not in gap  # no invented numbers
    vwap = json.loads((directory / "replay-vwap.json").read_text())
    assert (vwap["status"], vwap["metadata"]["rules_version"]) == ("replayed", "VWAP_PB_V1")
    assert vwap["coverage"] == {"sessions": 1, "symbols": 2, "symbol_days": 2}
    summary = (directory / "summary.txt").read_text()
    sections = [block.splitlines()[0] for block in summary.split("\n\n")[1:]]
    assert sections == [
        "ORB (ORB_V1)",
        "MOMO (MOMO_HOD_V2)",
        "VWAP (VWAP_PB_V1)",
        "ORB-V2 (ORB_V2)",
        "VWAP-V2 (VWAP_PB_V2)",
        "GAP (GAP_GO_V1)",
        "GAP-V2 (GAP_GO_V2)",
    ]
    assert "VWAP (VWAP_PB_V1)\n  0 trades from 2 symbol-days\n  no filled trades" in summary
    assert "GAP (GAP_GO_V1)\n  not replayed: needs extended bars: GAP_GO's gap" in summary
    assert summary.rstrip("\n") in capsys.readouterr().out


def test_run_replay_cli_hands_the_summary_to_telegram_only_when_asked(
    tmp_path, monkeypatch, capsys
):
    from trading.research import report_notify

    _research_root(tmp_path, monkeypatch)
    _capture_store(tmp_path)
    calls: list[dict[str, Any]] = []
    outcome = {"value": report_notify.SENT}

    def fake_notify(summary: str, **kwargs: Any) -> str:
        calls.append({"summary": summary, **kwargs})
        return outcome["value"]

    monkeypatch.setattr(report_notify, "notify_summary", fake_notify)

    assert run_replay_cli(["orb"]) == 0  # the default never touches Telegram
    assert calls == []
    printed = capsys.readouterr().out

    assert run_replay_cli(["orb"], notify="weekly") == 0
    (call,) = calls
    assert call["mode"] == "weekly"
    assert call["newest_session"] == SESSION  # the newest stored session
    assert call["summary"] in printed and call["summary"].startswith("Research replay")
    assert "telegram: summary sent" in capsys.readouterr().out

    # A weekly summary that fails never fails the replay (a restart would
    # only redo it); an on-demand one that is not delivered does.
    outcome["value"] = report_notify.FAILED
    assert run_replay_cli(["orb"], notify="weekly") == 0
    assert "weekly summary not sent" in capsys.readouterr().err
    outcome["value"] = report_notify.NOT_CONFIGURED
    assert run_replay_cli(["orb"], notify="weekly") == 0
    assert capsys.readouterr().err == ""  # not set up: silent
    assert run_replay_cli(["orb"], notify="now") == 1
    assert "summary NOT sent: Telegram is not set up" in capsys.readouterr().err


# --- recorder --------------------------------------------------------------------


class _RecordingTWSClient:
    def __init__(self, callbacks: Any) -> None:
        self.callbacks = callbacks
        self.history_calls: list[tuple[object, ...]] = []

    def connect(self, host: str, port: int, client_id: int) -> None:
        self.callbacks.nextValidId(900)

    def run(self) -> None:
        return None

    def reqManagedAccts(self) -> None:
        self.callbacks.managedAccounts("DU123456")

    def reqMarketDataType(self, data_type: int) -> None:
        return None

    def reqHistoricalData(self, *args: object) -> None:
        self.history_calls.append(args)
        request_id = int(args[0])
        bar = SimpleNamespace(
            date=str(int(_et(SESSION, 9, 30).timestamp())),
            open="26.45",
            high="26.56",
            low="26.27",
            close="26.56",
            volume="1338",
        )
        self.callbacks.historicalData(request_id, bar)
        self.callbacks.historicalDataEnd(request_id)

    def cancelHistoricalData(self, request_id: int) -> None:
        return None

    def disconnect(self) -> None:
        return None


async def test_research_actor_requests_like_completed_trade_bars_except_duration():
    clients: list[_RecordingTWSClient] = []

    def factory(callbacks: Any) -> _RecordingTWSClient:
        clients.append(_RecordingTWSClient(callbacks))
        return clients[-1]

    actor = ResearchTWSActor(
        TWSActorConfig(
            account="DU123456",
            client_id=28,
            handshake_timeout_seconds=0.5,
            request_timeout_seconds=0.5,
        ),
        client_factory=factory,
        contract_factory=lambda con_id: ("contract", con_id),
    )
    with pytest.raises(TWSConnectionError):
        await actor.intraday_trade_bars(4931, duration="20 D")
    await actor.connect()
    live = await actor.completed_trade_bars(4931)
    research = await actor.intraday_trade_bars(4931, duration="20 D")
    await actor.disconnect()

    live_call, research_call = clients[0].history_calls
    assert live_call[0] != research_call[0]  # each request has its own ID
    assert live_call[3] == "2 D" and research_call[3] == "20 D"
    assert live_call[1:3] + live_call[4:] == research_call[1:3] + research_call[4:]
    assert research_call[1:] == (
        ("contract", 4931), "", "20 D", "5 mins", "TRADES", 1, 2, False, []
    )

    def comparable(bar: IBKRTradeBar) -> tuple[object, ...]:
        return (bar.con_id, bar.interval_start_utc, bar.interval_end_utc, bar.open, bar.high,
                bar.low, bar.close, bar.volume, bar.rth, bar.bar_type)

    assert [comparable(bar) for bar in research.bars] == [comparable(bar) for bar in live.bars]
    assert research.bars[0].interval_start_utc == _et(SESSION, 9, 30)


def test_duration_and_session_helpers():
    assert validate_duration("20 D") == 20
    for bad in ("0 D", "61 D", "1 W", "20D", " 2 D"):
        with pytest.raises(TWSRequestError):
            validate_duration(bad)
    evening = datetime(2026, 10, 12, 21, 0, tzinfo=UTC)  # 17:00 ET Monday
    assert completed_sessions(evening, 3) == (date(2026, 10, 8), PRIOR, SESSION)
    midday = datetime(2026, 10, 12, 16, 0, tzinfo=UTC)  # 12:00 ET: today is not closed
    assert completed_sessions(midday, 1) == (PRIOR,)
    sessions = completed_sessions(evening, 20)
    assert len(sessions) == 20 and sessions[0] == date(2026, 9, 15)
    assert request_days(sessions, [date(2026, 10, 8)], evening) == 3
    # IBKR counts an open session in its duration, so a midday request is one longer.
    assert request_days((PRIOR,), [PRIOR], midday) == 2


def _sessions_back(last: date, count: int) -> list[date]:
    sessions: list[date] = []
    day = last
    while len(sessions) < count:
        if market_session(day) is not None:
            sessions.append(day)
        day -= timedelta(days=1)
    return sorted(sessions)


def _intraday(con_id: int, sessions: list[date]) -> list[IBKRTradeBar]:
    bars: list[IBKRTradeBar] = []
    for day_index, session_date in enumerate(sessions):
        opening = _et(session_date, 9, 30)
        for index in range(78):
            price = Decimal("50") + Decimal(day_index) + Decimal(index) / Decimal(100)
            bars.append(
                IBKRTradeBar(
                    con_id=con_id,
                    interval_start_utc=opening + BAR * index,
                    interval_end_utc=opening + BAR * (index + 1),
                    open=price,
                    high=price + Decimal("0.05"),
                    low=price - Decimal("0.05"),
                    close=price,
                    volume=Decimal(100 + index),
                    rth=True,
                )
            )
    return bars


def _daily(con_id: int, sessions: list[date]) -> list[IBKRDailyBar]:
    return [
        IBKRDailyBar(
            con_id=con_id,
            session_date=session_date,
            close=Decimal("40") + Decimal(index) / Decimal(10),
            volume=Decimal("10000"),
        )
        for index, session_date in enumerate(sessions)
    ]


class _FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


EVENING = datetime(2026, 10, 12, 21, 0, tzinfo=UTC)


@dataclass
class _FakeHistory:
    """IBKR-like history: ``"<n> D"`` returns the newest n sessions it has."""

    time: _FakeTime
    intraday: dict[int, list[IBKRTradeBar]]
    daily: dict[int, list[IBKRDailyBar]]
    script: dict[int, list[BarBatch | Exception]] = field(default_factory=dict)
    calls: list[tuple[str, int, str | None, float]] = field(default_factory=list)

    async def intraday_trade_bars(self, con_id: int, *, duration: str) -> BarBatch:
        self.calls.append(("intraday", con_id, duration, self.time.now))
        scripted = self.script.get(con_id)
        if scripted:
            item = scripted.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        count = int(duration.split()[0])
        bars = self.intraday[con_id]
        days = sorted({bar.interval_start_utc.astimezone(NEW_YORK).date() for bar in bars})
        wanted = set(days[-count:])
        return BarBatch(
            bars=tuple(
                bar for bar in bars if bar.interval_start_utc.astimezone(NEW_YORK).date() in wanted
            )
        )

    async def daily_trade_bars(self, con_id: int) -> DailyBarBatch:
        self.calls.append(("daily", con_id, None, self.time.now))
        return DailyBarBatch(bars=tuple(self.daily[con_id]))


def _history(con_ids: tuple[int, ...] = (101, 202, 303)) -> _FakeHistory:
    intraday_sessions = _sessions_back(SESSION, 5)
    daily_sessions = _sessions_back(SESSION, 45)
    return _FakeHistory(
        time=_FakeTime(),
        intraday={con_id: _intraday(con_id, intraday_sessions) for con_id in con_ids},
        daily={con_id: _daily(con_id, daily_sessions) for con_id in con_ids},
    )


async def _run(
    history: _FakeHistory, store: BarsStore, *, days: int = 3, clock: datetime = EVENING, **kw: Any
):
    instruments = kw.pop(
        "instruments", [(con_id, f"S{con_id}") for con_id in history.intraday]
    )
    return await backfill(
        history,
        con_ids_with_symbols=instruments,
        days=days,
        pace_seconds=kw.pop("pace_seconds", 2.0),
        store=store,
        clock=lambda: clock,
        sleep=history.time.sleep,
        monotonic=history.time.monotonic,
        **kw,
    )


async def test_backfill_splits_one_request_by_session_with_liquidity_context(tmp_path):
    history = _history((101,))
    store = BarsStore(ResearchPaths(tmp_path / "research"))

    report = await _run(history, store, instruments={101: "AAA"}, primary_exchanges={101: "NYSE"})

    assert report.completed and report.sessions == (date(2026, 10, 8), PRIOR, SESSION)
    assert (report.requested, report.days_written, report.symbols_written) == (1, 3, 1)
    assert [call[:3] for call in history.calls] == [
        ("intraday", 101, "3 D"),
        ("daily", 101, None),
    ]
    assert store.list_sessions() == (date(2026, 10, 8), PRIOR, SESSION)
    day = store.read_day(SESSION, 101)
    assert (day.symbol, day.primary_exchange, len(day.bars)) == ("AAA", "NYSE", 78)
    assert {bar.interval_start_utc.astimezone(NEW_YORK).date() for bar in day.bars} == {SESSION}
    assert day.bars[0].volume == Decimal("100")  # raw lots as TWS sent them
    daily = DailyBarBatch(bars=tuple(history.daily[101]))
    profile = liquidity_profile(daily, session_date=SESSION, volume_multiplier=VOLUME_MULTIPLIER)
    # The prior close is the previous session's daily close (Friday 2026-10-09).
    previous = next(bar for bar in history.daily[101] if bar.session_date == PRIOR)
    assert day.prior_close == profile.reference_price == previous.close
    assert day.avg_dollar_volume == profile.average_dollar_volume.quantize(Decimal("0.01"))
    # Mean close of the 20 prior sessions (42.40..44.30) x 10,000 lots x 100 shares.
    assert day.avg_dollar_volume == Decimal("43350000.00")


async def test_backfill_paces_every_request(tmp_path):
    history = _history()
    store = BarsStore(ResearchPaths(tmp_path / "research"))

    report = await _run(history, store, pace_seconds=2.0)

    assert report.completed and report.days_written == 9
    starts = [call[3] for call in history.calls]
    assert len(starts) == 6  # one intraday and one daily request per contract
    assert all(later - earlier >= 2.0 for earlier, later in zip(starts, starts[1:]))


async def test_backfill_resumes_and_requests_only_what_is_missing(tmp_path):
    history = _history((101, 202))
    store = BarsStore(ResearchPaths(tmp_path / "research"))
    await _run(history, store)
    written = store.path_for(SESSION, 101).read_bytes()
    history.calls.clear()

    again = await _run(history, store)
    assert again.completed and (again.already_stored, again.requested) == (2, 0)
    assert history.calls == []

    store.path_for(PRIOR, 101).unlink()
    resumed = await _run(history, store)
    assert (resumed.already_stored, resumed.requested, resumed.days_written) == (1, 1, 1)
    # One request reaching back to the oldest missing session only.
    assert [call[:3] for call in history.calls] == [
        ("intraday", 101, "2 D"),
        ("daily", 101, None),
    ]
    assert store.has_day(PRIOR, 101)
    assert store.path_for(SESSION, 101).read_bytes() == written  # never rewritten


async def test_backfill_backs_off_after_a_pacing_violation_and_retries(tmp_path):
    history = _history((101,))
    history.script[101] = [BarBatch(bars=(), pacing_failed=True, error=PACING_TEXT)]
    store = BarsStore(ResearchPaths(tmp_path / "research"))

    report = await _run(history, store, pacing_backoff_seconds=15.0)

    assert report.completed and report.days_written == 3 and report.failures == ()
    first, retry = (call for call in history.calls if call[0] == "intraday")
    assert retry[3] - first[3] >= 15.0


@pytest.mark.parametrize(
    ("scripted", "reason"),
    [
        ([BarBatch(bars=(), pacing_failed=True, error=PACING_TEXT)] * 2, "pacing"),
        ([BarBatch(bars=(), pacing_failed=True, error=COMPETING_TEXT)], "competing_session"),
        ([BarBatch(bars=(), connected=False)], "disconnected"),
        ([TWSConnectionError("TWS connection closed")], "disconnected"),
    ],
)
async def test_backfill_stops_gracefully_and_keeps_what_it_wrote(tmp_path, scripted, reason):
    history = _history()
    history.script[202] = list(scripted)
    store = BarsStore(ResearchPaths(tmp_path / "research"))

    report = await _run(history, store, max_attempts=2, pacing_backoff_seconds=1.0)

    assert report.stopped == reason and not report.completed
    assert report.remaining == 2  # the interrupted contract and the one after it
    assert report.days_written == 3 and store.stored_sessions(101) == frozenset(report.sessions)
    assert store.stored_sessions(202) == frozenset() == store.stored_sessions(303)
    assert all(call[1] != 303 for call in history.calls)
    # The next run resumes with the contracts that were not stored.
    history.script.clear()
    resumed = await _run(history, store)
    assert resumed.completed and (resumed.already_stored, resumed.days_written) == (1, 6)


async def test_backfill_skips_a_contract_without_data_and_continues(tmp_path):
    history = _history()
    history.script[202] = [BarBatch(bars=(), pacing_failed=True, error=NO_DATA_TEXT)]
    store = BarsStore(ResearchPaths(tmp_path / "research"))

    report = await _run(history, store)

    assert report.completed and report.days_written == 6
    assert report.failures == (("S202", NO_DATA_TEXT),)
    # "No data" shares IBKR code 162 with pacing but is not retried.
    assert [call[:2] for call in history.calls if call[1] == 202] == [("intraday", 202)]


async def test_backfill_stops_after_repeated_contract_failures(tmp_path):
    history = _history()
    for con_id in history.intraday:
        history.script[con_id] = [TWSRequestError("TWS historical-data request timed out")]
    store = BarsStore(ResearchPaths(tmp_path / "research"))

    report = await _run(history, store, max_attempts=1, max_consecutive_failures=2)

    assert report.stopped == "repeated_failures" and report.remaining == 1
    assert len(report.failures) == 2


class _FullDiskStore(BarsStore):
    def __init__(self, paths: ResearchPaths, *, fail_after: int) -> None:
        super().__init__(paths)
        self.fail_after = fail_after

    def write_day(self, day: StoredDay, *, overwrite: bool = False) -> Path:
        if self.fail_after <= 0:
            raise OSError(28, "No space left on device")
        self.fail_after -= 1
        return super().write_day(day, overwrite=overwrite)


async def test_backfill_stops_when_the_store_cannot_be_written(tmp_path):
    history = _history((101, 202))
    store = _FullDiskStore(ResearchPaths(tmp_path / "research"), fail_after=4)

    report = await _run(history, store)

    assert report.stopped == "store_unwritable" and report.remaining == 1
    # 101 stored all three sessions and 202 one before the disk filled.
    assert (report.days_written, report.symbols_written) == (4, 2)
    assert store.stored_sessions(202) == frozenset({date(2026, 10, 8)})
    assert "No space left on device" in report.failures[-1][1]


async def test_backfill_records_sessions_ibkr_has_no_bars_for(tmp_path):
    history = _history((101,))
    history.intraday[101] = [
        bar for bar in history.intraday[101]
        if bar.interval_start_utc.astimezone(NEW_YORK).date() != PRIOR
    ]
    store = BarsStore(ResearchPaths(tmp_path / "research"))

    report = await _run(history, store, days=2)

    assert report.missing_days == (("S101", "2026-10-09"),)
    assert store.stored_sessions(101) == frozenset({SESSION})


async def test_record_today_never_stores_an_unfinished_session(tmp_path):
    """Run as the capture was taken (15:25 ET): only Friday's closed session is stored."""
    clock = datetime(2026, 10, 12, 19, 25, tzinfo=UTC)
    time_source = _FakeTime()
    history = _FakeHistory(
        time=time_source,
        intraday={BF_B.con_id: list(BF_B.bars)},
        daily={BF_B.con_id: list(BF_B.daily.bars)},
    )
    store = BarsStore(ResearchPaths(tmp_path / "research"))

    report = await record_today(
        history,
        con_ids_with_symbols=[Instrument(BF_B.con_id, BF_B.symbol, "NYSE")],
        store=store,
        clock=lambda: clock,
        sleep=time_source.sleep,
        monotonic=time_source.monotonic,
    )

    assert report.sessions == (PRIOR,) and report.days_written == 1
    assert history.calls[0][:3] == ("intraday", BF_B.con_id, "2 D")
    assert store.list_sessions() == (PRIOR,)
    stored = store.read_day(PRIOR, BF_B.con_id)
    assert stored.bars == _stored_day(BF_B, PRIOR).bars and len(stored.bars) == 78
    assert stored.prior_close == Decimal("26.54") and stored.primary_exchange == "NYSE"


def test_load_qualified_universe_reads_the_newest_qualified_session(tmp_path):
    path = tmp_path / "autotrader.db"
    with closing(sqlite3.connect(path)) as db:
        db.executescript(
            """
            CREATE TABLE autotrader_universe_daily (
                session_date TEXT NOT NULL, con_id INTEGER NOT NULL, symbol TEXT NOT NULL,
                prior_close TEXT NULL, avg_daily_volume_shares TEXT NULL,
                avg_dollar_volume TEXT NULL, daily_atr TEXT NULL,
                qualified INTEGER NOT NULL, reason TEXT NULL, observed_at TEXT NOT NULL,
                PRIMARY KEY(session_date, con_id));
            CREATE TABLE autotrader_universe (
                source TEXT NOT NULL, symbol TEXT NOT NULL, listing_exchange TEXT NOT NULL,
                ibkr_symbol TEXT NOT NULL, con_id INTEGER NULL, primary_exchange TEXT NULL,
                status TEXT NOT NULL, last_error TEXT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(source, symbol));
            INSERT INTO autotrader_universe VALUES
                ('watchlist', 'BBB', 'NYSE', 'BBB', 2, 'NYSE', 'resolved', NULL, 'x'),
                ('watchlist', 'CCC', 'NASDAQ', 'CCC', 3, 'NASDAQ', 'resolved', NULL, 'x');
            INSERT INTO autotrader_universe_daily VALUES
                ('2026-10-09', 1, 'AAA', '20', NULL, '30000000', NULL, 1, NULL, 'x'),
                ('2026-10-12', 3, 'CCC', '20', NULL, '30000000', NULL, 1, NULL, 'x'),
                ('2026-10-12', 2, 'BBB', '20', NULL, '30000000', NULL, 1, NULL, 'x'),
                ('2026-10-12', 4, 'DDD', '5', NULL, '30000000', NULL, 0, 'price_out_of_range', 'x'),
                ('2026-10-13', 5, 'EEE', '5', NULL, '1', NULL, 0, 'dollar_volume_too_low', 'x');
            """
        )
        db.commit()
    before = path.read_bytes()

    session_date, instruments = load_qualified_universe(path)

    assert session_date == SESSION
    assert instruments == (Instrument(2, "BBB", "NYSE"), Instrument(3, "CCC", "NASDAQ"))
    assert path.read_bytes() == before


async def test_run_backfill_cli_refuses_bad_days_and_missing_settings(monkeypatch, capsys):
    from trading.operations.settings import ShadowSettingsError

    assert await run_backfill_cli(0) == 2
    assert await run_backfill_cli(61) == 2

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise ShadowSettingsError("shadow configuration is unavailable")

    monkeypatch.setattr("trading.operations.settings.load_shadow_runtime_config", unavailable)
    assert await run_backfill_cli(20) == 1
    errors = capsys.readouterr().err
    assert "--days must be between 1 and 60" in errors
    assert "shadow configuration is unavailable" in errors
