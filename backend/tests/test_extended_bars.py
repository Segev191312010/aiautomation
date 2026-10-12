"""Extended-hours bars: calendar-derived RTH flags, the all-hours request, its validation.

Live 2026-10-12 (TWS PAPER, NVDA, read-only): the "1 D" all-hours request
returned only that day, 04:00-16:35 ET, as 66 pre-market, 78 regular-session
and 8 after-hours bars; its 78 regular-session bars matched the RTH-only "2 D"
request exactly.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import inspect
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from trading.market.ibkr_boundary import (
    DEFAULT_BAR_MAX_AGE,
    EXTENDED_HOURS_BAR_MAX_AGE,
    BarBatch,
    IBKRConnectionActor,
    IBKRTradeBar,
    MarketDataQualityError,
    PaperIBKRMarketData,
    extended_hours_bar_max_age,
    is_regular_session_bar,
    regular_session_bounds,
)
from trading.market.tws_actor import (
    EXTENDED_HISTORY_DURATION,
    INTRADAY_HISTORY_DURATION,
    TWSActorConfig,
    TWSPaperActor,
    TWSRequestError,
)

NEW_YORK = ZoneInfo("America/New_York")
SESSION = date(2026, 10, 13)
HALF_DAY = date(2026, 11, 27)  # the day after Thanksgiving closes at 13:00 ET
HOLIDAY = date(2026, 11, 26)
SATURDAY = date(2026, 10, 10)
DST_ENDED = date(2026, 11, 2)  # first session on EST
DST_STARTED = date(2026, 3, 9)  # first session on EDT
BAR = timedelta(minutes=5)
CON_ID = 101


def _et(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=NEW_YORK).astimezone(
        timezone.utc
    )


def _starts(day: date, first: tuple[int, int], last: tuple[int, int]) -> list[datetime]:
    """Five-minute bar starts from ``first`` through ``last`` (inclusive), New York time."""
    start, stop = _et(day, *first), _et(day, *last)
    count = int((stop - start) / BAR) + 1
    return [start + BAR * index for index in range(count)]


# --- calendar-derived RTH flags -----------------------------------------------


@pytest.mark.parametrize(
    ("day", "start", "rth"),
    [
        (SESSION, (4, 0), False),
        (SESSION, (9, 25), False),
        (SESSION, (9, 30), True),
        (SESSION, (12, 0), True),
        (SESSION, (15, 55), True),
        (SESSION, (16, 0), False),
        (SESSION, (19, 55), False),
        (HALF_DAY, (9, 30), True),
        (HALF_DAY, (12, 55), True),
        (HALF_DAY, (13, 0), False),
        (HALF_DAY, (15, 55), False),
        (HOLIDAY, (9, 30), False),
        (SATURDAY, (10, 0), False),
        (DST_ENDED, (9, 25), False),
        (DST_ENDED, (9, 30), True),
        (DST_ENDED, (15, 55), True),
        (DST_STARTED, (9, 30), True),
        (DST_STARTED, (16, 0), False),
    ],
)
def test_rth_flag_comes_from_the_xnys_session_calendar(day, start, rth):
    begin = _et(day, *start)
    assert is_regular_session_bar(begin, begin + BAR) is rth


def test_a_bar_counts_as_rth_only_when_wholly_inside_the_session():
    assert is_regular_session_bar(_et(SESSION, 9, 28), _et(SESSION, 9, 33)) is False
    assert is_regular_session_bar(_et(SESSION, 15, 58), _et(SESSION, 16, 3)) is False
    # An hour off (a UTC/EST mix-up) would put 08:30 EST inside the session.
    assert is_regular_session_bar(_et(DST_ENDED, 8, 30), _et(DST_ENDED, 8, 35)) is False
    with pytest.raises(MarketDataQualityError, match="timezone-aware"):
        is_regular_session_bar(datetime(2026, 10, 13, 14, 30), datetime(2026, 10, 13, 14, 35))


def test_session_bounds_close_early_on_half_days_and_are_absent_on_closed_dates():
    assert regular_session_bounds(SESSION) == (_et(SESSION, 9, 30), _et(SESSION, 16, 0))
    assert regular_session_bounds(HALF_DAY) == (_et(HALF_DAY, 9, 30), _et(HALF_DAY, 13, 0))
    assert regular_session_bounds(HOLIDAY) is None
    assert regular_session_bounds(SATURDAY) is None


# --- the actor ----------------------------------------------------------------


class _HistoryClient:
    """Answers reqHistoricalData with five-minute bars at the scripted starts."""

    def __init__(
        self,
        callbacks: Any,
        starts: dict[int, list[datetime]],
        *,
        respond: bool = True,
        error_code: int | None = None,
    ) -> None:
        self.callbacks = callbacks
        self.starts = starts
        self.respond = respond
        self.error_code = error_code
        self.history_calls: list[tuple[object, ...]] = []
        self.cancelled_history: list[int] = []

    def connect(self, host: str, port: int, client_id: int) -> None:
        self.callbacks.nextValidId(500)

    def run(self) -> None:
        return None

    def reqManagedAccts(self) -> None:
        self.callbacks.managedAccounts("DU123456")

    def reqMarketDataType(self, data_type: int) -> None:
        return None

    def reqHistoricalData(self, *args: object) -> None:
        self.history_calls.append(args)
        request_id = int(args[0])  # type: ignore[call-overload]
        if not self.respond:
            return
        if self.error_code is not None:
            self.callbacks.error(request_id, self.error_code, "Historical data pacing violation")
            return
        # Positional order: reqId, contract, end, duration, size, what, useRTH, ...
        for start in self.starts[int(args[6])]:  # type: ignore[call-overload]
            self.callbacks.historicalData(request_id, _raw_bar(start))
        self.callbacks.historicalDataEnd(request_id)

    def cancelHistoricalData(self, request_id: int) -> None:
        self.cancelled_history.append(request_id)

    def disconnect(self) -> None:
        return None


def _raw_bar(start: datetime) -> SimpleNamespace:
    # formatDate=2: TWS sends the bar start as UTC epoch seconds.
    return SimpleNamespace(
        date=str(int(start.timestamp())),
        open="100",
        high="101",
        low="99",
        close="100.5",
        volume="12",
    )


async def _actor(starts: dict[int, list[datetime]], **client_options: Any):
    clients: list[_HistoryClient] = []

    def factory(callbacks: Any) -> _HistoryClient:
        client = _HistoryClient(callbacks, starts, **client_options)
        clients.append(client)
        return client

    actor = TWSPaperActor(
        TWSActorConfig(
            account="DU123456",
            client_id=29,
            handshake_timeout_seconds=0.2,
            request_timeout_seconds=0.2,
        ),
        client_factory=factory,
    )
    await actor.connect()
    return actor, clients[0]


EXTENDED_DAY = (
    _starts(SESSION, (4, 0), (9, 25))
    + _starts(SESSION, (9, 30), (15, 55))
    + _starts(SESSION, (16, 0), (19, 55))
)


@pytest.mark.anyio
async def test_extended_request_is_one_all_hours_day_with_epoch_dates():
    actor, client = await _actor({0: EXTENDED_DAY})
    batch = await actor.extended_trade_bars(CON_ID)

    call = client.history_calls[0]
    assert call[0] == 500 and getattr(call[1], "conId") == CON_ID
    assert call[2:] == ("", "1 D", "5 mins", "TRADES", 0, 2, False, [])
    assert EXTENDED_HISTORY_DURATION == "1 D"
    assert batch.error is None and batch.connected is True
    assert len(batch.bars) == 192
    flags = {bar.interval_start_utc: bar.rth for bar in batch.bars}
    regular = set(_starts(SESSION, (9, 30), (15, 55)))
    assert {start for start, rth in flags.items() if rth} == regular
    assert sum(not rth for rth in flags.values()) == 66 + 48
    first = batch.bars[0]
    assert (first.con_id, first.interval_start_utc, first.interval_end_utc) == (
        CON_ID,
        _et(SESSION, 4, 0),
        _et(SESSION, 4, 5),
    )
    assert (first.bar_type, first.complete, first.delayed, first.frozen) == ("TRADES", True, False, False)


@pytest.mark.anyio
async def test_extended_bars_on_a_half_day_stop_being_rth_at_13_00():
    day = _starts(HALF_DAY, (4, 0), (9, 25)) + _starts(HALF_DAY, (9, 30), (16, 55))
    actor, _ = await _actor({0: day})
    batch = await actor.extended_trade_bars(CON_ID)
    rth = [bar.interval_start_utc for bar in batch.bars if bar.rth]
    assert rth == _starts(HALF_DAY, (9, 30), (12, 55))
    assert len(rth) == 42


@pytest.mark.anyio
async def test_completed_trade_bars_request_and_results_are_unchanged():
    # useRTH=1 data: every bar lies inside its session, so the derived flag is
    # the True the parser used to hard-code -- on a full day, across both DST
    # changes, and on a half day.
    rth_only = (
        _starts(DST_STARTED, (9, 30), (15, 55))
        + _starts(SESSION, (9, 30), (15, 55))
        + _starts(DST_ENDED, (9, 30), (15, 55))
        + _starts(HALF_DAY, (9, 30), (12, 55))
    )
    actor, client = await _actor({1: rth_only})
    batch = await actor.completed_trade_bars(CON_ID)

    call = client.history_calls[0]
    assert call[2:] == ("", "2 D", "5 mins", "TRADES", 1, 2, False, [])
    assert INTRADAY_HISTORY_DURATION == "2 D"
    assert len(batch.bars) == 78 * 3 + 42
    for start, bar in zip(rth_only, batch.bars, strict=True):
        before_the_change = IBKRTradeBar(
            con_id=CON_ID,
            interval_start_utc=start,
            interval_end_utc=start + BAR,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=Decimal("12"),
            rth=True,
            received_at=bar.received_at,
        )
        assert bar == before_the_change


@pytest.mark.anyio
async def test_extended_request_timeout_cancels_the_request():
    actor, client = await _actor({0: []}, respond=False)
    with pytest.raises(TWSRequestError, match="extended-hours history request timed out"):
        await actor.extended_trade_bars(CON_ID)
    assert client.cancelled_history == [500]
    with pytest.raises(TWSRequestError, match="positive"):
        await actor.extended_trade_bars(0)


@pytest.mark.anyio
async def test_extended_request_reports_pacing_without_fake_data():
    actor, client = await _actor({0: EXTENDED_DAY}, error_code=162)
    batch = await actor.extended_trade_bars(CON_ID)
    assert batch.bars == () and batch.pacing_failed is True and batch.error is not None
    assert len(client.history_calls) == 1


def test_the_actor_implements_every_connection_protocol_method():
    names = [
        name
        for name, member in vars(IBKRConnectionActor).items()
        if not name.startswith("_") and inspect.isfunction(member)
    ]
    assert "extended_trade_bars" in names and "completed_trade_bars" in names
    for name in names:
        assert inspect.iscoroutinefunction(getattr(TWSPaperActor, name)), name


# --- boundary validation ------------------------------------------------------


def _trade_bar(start: datetime, *, received_at: datetime | None = None, con_id: int = CON_ID) -> IBKRTradeBar:
    end = start + BAR
    return IBKRTradeBar(
        con_id=con_id,
        interval_start_utc=start,
        interval_end_utc=end,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("12"),
        rth=is_regular_session_bar(start, end),
        received_at=received_at or end + timedelta(seconds=1),
    )


class _ExtendedActor:
    def __init__(self, batch: BarBatch) -> None:
        self.batch = batch
        self.requests: list[int] = []

    async def supports_scanner(self, request: object) -> bool:
        return True

    async def extended_trade_bars(self, con_id: int) -> BarBatch:
        self.requests.append(con_id)
        return self.batch


def _market(*starts: datetime, received_at: datetime | None = None, con_id: int = CON_ID):
    batch = BarBatch(bars=tuple(_trade_bar(start, received_at=received_at, con_id=con_id) for start in starts))
    actor = _ExtendedActor(batch)
    return PaperIBKRMarketData(actor=actor, host="127.0.0.1"), actor


@pytest.mark.anyio
async def test_thin_pre_market_bars_may_be_minutes_old():
    # Newest pre-market bar ended 07:50; at 08:00:05 it is ten minutes old.
    market, actor = _market(*_starts(SESSION, (4, 0), (7, 45)))
    bars = await market.extended_bars(con_id=CON_ID, now=_et(SESSION, 8, 0, 5))
    assert len(bars) == 46 and not any(bar.rth for bar in bars)
    assert all(bar.source == "ibkr" and bar.data_mode == "live" for bar in bars)
    assert actor.requests == [CON_ID]

    old, _ = _market(*_starts(SESSION, (4, 0), (7, 35)))
    with pytest.raises(MarketDataQualityError, match="stale"):
        await old.extended_bars(con_id=CON_ID, now=_et(SESSION, 8, 0, 5))


@pytest.mark.anyio
async def test_the_strict_rule_starts_when_the_first_regular_bar_completes():
    # While the 09:30 bar is forming, the newest bar is still pre-market.
    premarket = _starts(SESSION, (4, 0), (9, 15))
    forming, _ = _market(*premarket)
    bars = await forming.extended_bars(con_id=CON_ID, now=_et(SESSION, 9, 33))
    assert bars[-1].interval_end_utc == _et(SESSION, 9, 20)

    # 09:35:05 with no 09:30 bar: the newest bar ended five minutes ago.
    missing, _ = _market(*_starts(SESSION, (4, 0), (9, 25)))
    with pytest.raises(MarketDataQualityError, match="stale"):
        await missing.extended_bars(con_id=CON_ID, now=_et(SESSION, 9, 35, 5))

    fresh, _ = _market(*_starts(SESSION, (4, 0), (9, 30)))
    bars = await fresh.extended_bars(con_id=CON_ID, now=_et(SESSION, 9, 35, 5))
    assert [bar.rth for bar in bars[-2:]] == [False, True]


@pytest.mark.anyio
async def test_regular_session_bars_follow_the_strict_freshness_rule():
    day = _starts(SESSION, (4, 0), (9, 55))
    current, _ = _market(*day)
    bars = await current.extended_bars(con_id=CON_ID, now=_et(SESSION, 10, 0, 5))
    assert sum(bar.rth for bar in bars) == 6

    lagging, _ = _market(*day[:-1])
    with pytest.raises(MarketDataQualityError, match="stale"):
        await lagging.extended_bars(con_id=CON_ID, now=_et(SESSION, 10, 0, 5))


@pytest.mark.anyio
async def test_the_forming_bar_is_dropped_and_a_slow_reply_is_observed_at_receipt():
    requested_at = _et(SESSION, 9, 45, 5)
    received_at = _et(SESSION, 9, 45, 9)
    market, _ = _market(*_starts(SESSION, (4, 0), (9, 45)), received_at=received_at)
    bars = await market.extended_bars(con_id=CON_ID, now=requested_at)
    assert bars[-1].interval_end_utc == _et(SESSION, 9, 45)
    assert len(bars) == 69


@pytest.mark.anyio
async def test_after_the_close_and_after_a_half_day_close_bars_may_be_minutes_old():
    after_close, _ = _market(*_starts(SESSION, (15, 0), (16, 5)))
    bars = await after_close.extended_bars(con_id=CON_ID, now=_et(SESSION, 16, 20))
    assert not bars[-1].rth

    half_day, _ = _market(*_starts(HALF_DAY, (12, 0), (12, 55)))
    bars = await half_day.extended_bars(con_id=CON_ID, now=_et(HALF_DAY, 13, 10))
    assert bars[-1].rth and bars[-1].interval_end_utc == _et(HALF_DAY, 13, 0)

    morning, _ = _market(*_starts(HALF_DAY, (9, 30), (12, 20)))
    with pytest.raises(MarketDataQualityError, match="stale"):
        await morning.extended_bars(con_id=CON_ID, now=_et(HALF_DAY, 12, 30, 5))


def test_extended_hours_allowance_by_time_of_day():
    strict, lenient = DEFAULT_BAR_MAX_AGE, EXTENDED_HOURS_BAR_MAX_AGE
    assert lenient == timedelta(minutes=15)
    cases = [
        (_et(SESSION, 8, 0), lenient),
        (_et(SESSION, 9, 34, 59), lenient),
        (_et(SESSION, 9, 35), strict),
        (_et(SESSION, 16, 0), strict),
        (_et(SESSION, 16, 0, 1), lenient),
        (_et(HALF_DAY, 12, 59), strict),
        (_et(HALF_DAY, 13, 0, 1), lenient),
        (_et(HOLIDAY, 11, 0), lenient),
    ]
    for observed_at, expected in cases:
        assert extended_hours_bar_max_age(observed_at, regular_max_age=strict) == expected
    # A configured allowance longer than the extended one is never shortened.
    hour = timedelta(hours=1)
    assert extended_hours_bar_max_age(_et(SESSION, 8, 0), regular_max_age=hour) == hour


@pytest.mark.anyio
async def test_extended_bars_reject_failed_or_foreign_batches():
    now = _et(SESSION, 8, 0, 5)
    foreign, _ = _market(*_starts(SESSION, (7, 0), (7, 50)), con_id=CON_ID + 1)
    with pytest.raises(MarketDataQualityError, match="different contract"):
        await foreign.extended_bars(con_id=CON_ID, now=now)
    for batch, message in (
        (BarBatch(bars=(), connected=False), "unavailable"),
        (BarBatch(bars=(), pacing_failed=True), "pacing"),
        (BarBatch(bars=(), error="TWS error 162: no data"), "failed"),
    ):
        market = PaperIBKRMarketData(actor=_ExtendedActor(batch), host="127.0.0.1")
        with pytest.raises(MarketDataQualityError, match=message):
            await market.extended_bars(con_id=CON_ID, now=now)
    market, actor = _market()
    with pytest.raises(MarketDataQualityError, match="positive"):
        await market.extended_bars(con_id=0, now=now)
    assert actor.requests == []
    # A thin name with no trades at all yet is an empty, not a stale, batch.
    assert await market.extended_bars(con_id=CON_ID, now=now) == ()
