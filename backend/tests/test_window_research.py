"""Window research: one walk per family serves every swept window, split by hour.

The store holds the real 2026-10-09/12 captures (tests/fixtures/momo) plus one
synthetic contract whose only opening-range breakout comes at 13:00 ET, after
ORB_V1's 12:00 cutoff: the extra hours are where the windows differ.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.test_research import PRIOR, SESSION, _capture_store
from trading.research.bars_store import BarsStore, StoredBar, StoredDay
from trading.research.costs import DEFAULT_COST_MODEL
from trading.research.replay import replay_strategy
from trading.research.windows import (
    FAMILY_REGISTRY_KEYS,
    SWEEP_WINDOWS,
    research_payload,
    sweep_family,
    widest_window,
    window_lines,
)
from trading.rules.all_session import ALL_SESSION_WINDOW, V1_WINDOWS, EntryWindow, windowed_strategy

NEW_YORK = ZoneInfo("America/New_York")
UTC = timezone.utc
LATE = 777


def _at(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=NEW_YORK).astimezone(UTC)


def _bar(day: date, hour: int, minute: int, o: str, h: str, low: str, c: str) -> StoredBar:
    return StoredBar(
        interval_start_utc=_at(day, hour, minute),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal("1000"),
    )


def _session_starts(day: date):
    start = _at(day, 9, 30)
    return [start + timedelta(minutes=5 * index) for index in range(78)]


def _late_breakout_days() -> tuple[StoredDay, StoredDay]:
    prior = tuple(
        StoredBar(interval_start_utc=moment, open=Decimal("100"), high=Decimal("100.4"),
                  low=Decimal("99.6"), close=Decimal("100"), volume=Decimal("1000"))
        for moment in _session_starts(PRIOR)
    )
    bars = [
        _bar(SESSION, 9, 30, "100", "101", "99.5", "100.5"),
        _bar(SESSION, 9, 35, "100.5", "100.9", "100", "100.4"),
        _bar(SESSION, 9, 40, "100.4", "100.8", "100.1", "100.5"),
    ]
    for moment in _session_starts(SESSION)[3:]:
        clock = moment.astimezone(NEW_YORK).time()
        if clock < time(12, 55):
            bars.append(StoredBar(interval_start_utc=moment, open=Decimal("100.5"), high=Decimal("100.8"),
                                  low=Decimal("100.2"), close=Decimal("100.5"), volume=Decimal("1000")))
        elif clock == time(12, 55):  # the first close above the 101 range high
            bars.append(_bar(SESSION, 12, 55, "100.5", "101.6", "100.4", "101.5"))
        elif clock == time(13, 0):
            bars.append(_bar(SESSION, 13, 0, "101.45", "101.7", "101.3", "101.6"))
        elif clock < time(14, 0):
            bars.append(StoredBar(interval_start_utc=moment, open=Decimal("101.6"), high=Decimal("102"),
                                  low=Decimal("101.5"), close=Decimal("101.9"), volume=Decimal("1000")))
        else:
            bars.append(StoredBar(interval_start_utc=moment, open=Decimal("103"), high=Decimal("104"),
                                  low=Decimal("102.9"), close=Decimal("103.5"), volume=Decimal("1000")))
    common = {"con_id": LATE, "symbol": "LATE", "avg_dollar_volume": Decimal("50000000"),
              "primary_exchange": "NASDAQ"}
    return (
        StoredDay(session_date=PRIOR, bars=prior, prior_close=Decimal("100"), **common),
        StoredDay(session_date=SESSION, bars=tuple(bars), prior_close=Decimal("100"), **common),
    )


@pytest.fixture
def store(tmp_path) -> BarsStore:
    research = _capture_store(tmp_path)
    for day in _late_breakout_days():
        research.write_day(day)
    return research


def _replayed(trades) -> list[tuple]:
    return [
        (trade.session_date, trade.symbol, trade.signal_at, trade.exit_at, trade.exit_reason, trade.r_multiple)
        for trade in trades
    ]


def _swept(trades) -> list[tuple]:
    return [
        (trade.session_date, trade.symbol, trade.signal_at, trade.exit_at, trade.exit_reason, trade.gross_r)
        for trade in trades
    ]


@pytest.mark.parametrize("family", ["orb", "vwap_pb", "momo_hod"])
def test_every_swept_window_equals_a_direct_replay_with_that_window(store, family):
    research = sweep_family(family, store)

    assert research.status == "swept" and research.sessions == (SESSION,)
    assert [result.window for result in research.windows] == list(SWEEP_WINDOWS[family])
    for result in research.windows:
        direct = replay_strategy(
            FAMILY_REGISTRY_KEYS[family], store, strategy=windowed_strategy(family, result.window)
        )
        assert _swept(result.trades) == _replayed(direct.trades), result.label
        assert result.unfilled == len(direct.unfilled), result.label
        assert research.symbol_days == direct.symbol_days


def test_orb_windows_reach_the_late_breakout_only_once_they_cover_13_00(store):
    research = sweep_family("orb", store)

    by_window = {result.label: [trade.symbol for trade in result.trades] for result in research.windows}
    assert by_window == {
        "09:50-12:00": ["BF B", "FIGS"],
        # The breakout bar ends 13:00: its poll at 13:00:05 misses a 13:00 cutoff.
        "09:50-13:00": ["BF B", "FIGS"],
        "09:50-14:00": ["BF B", "FIGS", "LATE"],
        "09:50-15:00": ["BF B", "FIGS", "LATE"],
        "09:35-15:30": ["BF B", "FIGS", "LATE"],
    }
    v2 = research.windows[-1]
    assert v2.window == ALL_SESSION_WINDOW and research.windows[0].core
    # The V1 and V2 windows are the live ORB_V1 and ORB_V2 replays.
    assert _swept(research.windows[0].trades) == _replayed(replay_strategy("orb", store).trades)
    assert _swept(v2.trades) == _replayed(replay_strategy("orb-v2", store).trades)
    late = v2.trades[-1]
    assert (late.symbol, late.exit_reason, late.gross_r, late.hour_bucket, late.core) == (
        "LATE", "target", Decimal("2.0000"), 12, False,
    )
    direct = replay_strategy("orb-v2", store).trades[-1]
    assert direct.entry == Decimal("101.5")
    assert late.cost_r == DEFAULT_COST_MODEL.cost_r(
        entry=direct.entry, stop=direct.stop, exit_reason="target"
    )
    assert late.r_multiple == late.gross_r - late.cost_r


def test_hour_buckets_split_core_from_extra_hours(store):
    research = sweep_family("orb", store)

    assert [(bucket.hour, bucket.trades, bucket.scope) for bucket in research.hours] == [
        (9, 1, "core"),  # BF B: the 09:55-10:00 signal bar
        (10, 1, "core"),  # FIGS: the 10:10-10:15 signal bar
        (12, 1, "extra"),  # LATE: the 12:55-13:00 signal bar, after ORB_V1's cutoff
    ]
    assert research.core_stats is not None and research.extra_stats is not None
    assert (research.core_stats.trades, research.extra_stats.trades) == (2, 1)
    lines = window_lines(research)
    assert lines[0] == "ORB (1 sessions, 3 symbol-days)"
    assert lines[1].startswith("  09:50-12:00 (V1): 2 trades, ")
    assert lines[-3].startswith("  09:35-15:30 (V2): 3 trades, ")
    assert lines[-2].startswith("  by signal hour: 09h 1 ") and "12h 1 " in lines[-2] and lines[-2].endswith("*")
    assert lines[-1].startswith("  core 2 trades ") and "extra hours 1 trades" in lines[-1]
    payload = research_payload(research)
    assert payload["basis"] == "rules-only replay, net R after costs"
    assert [item["hour"] for item in payload["hours"]] == [9, 10, 12]
    assert payload["windows"][0]["window"] == V1_WINDOWS["orb"].payload()


def test_gap_go_is_not_swept_without_extended_bars(store):
    research = sweep_family("gap_go", store)

    assert research.status == "skipped" and "needs extended bars" in research.note
    assert window_lines(research) == ["GAP_GO: not swept (needs extended bars)"]
    assert research_payload(research)["note"] == research.note


def test_the_sweep_windows_start_at_v1_and_end_at_the_all_session_window():
    for family, windows in SWEEP_WINDOWS.items():
        assert windows[0] == V1_WINDOWS[family]
        assert windows[-1] == ALL_SESSION_WINDOW
        widest = widest_window(windows)
        assert widest.cutoff == time(15, 30)
        assert widest.close_buffer == min(window.close_buffer for window in windows)
    assert widest_window(
        (EntryWindow(start=time(10, 0), cutoff=time(12, 0)), EntryWindow(start=time(9, 40), cutoff=time(11, 0)))
    ) == EntryWindow(start=time(9, 40), cutoff=time(12, 0))
