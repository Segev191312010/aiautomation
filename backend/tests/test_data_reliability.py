"""Real MOMO's data path: poll slots per strategy, no doomed stale polls, error reasons.

2026-10-12 evidence (shadow.log): MOMO's run started late and polled at once,
78 s after its slot, so 28 of its 35 bar requests were refused as stale; the
scanner children polled together 5 s after each close and one of them lost
requests to IBKR's identical-request pacing rule.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from itertools import combinations
import logging
from zoneinfo import ZoneInfo

import pytest

from trading.contracts import (
    AccountSnapshot,
    AIReview,
    Candidate,
    RiskPolicy,
    Run,
    ScannerRun,
    fingerprint_payload,
)
from trading.market.ibkr_boundary import (
    FAULT_DISCONNECTED,
    FAULT_INVALID,
    FAULT_PACING,
    FAULT_RECEIPT_IN_FUTURE,
    FAULT_REQUEST_FAILED,
    FAULT_STALE,
    MARKET_DATA_FAULTS,
    BarBatch,
    DailyBarBatch,
    IBKRScannerResult,
    IBKRTradeBar,
    LiquidityProfile,
    MarketDataQualityError,
    PaperIBKRMarketData,
    liquidity_profile,
    qualify_scanner_results,
    validate_completed_trade_bars,
)
from trading.operations import shadow_service
from trading.operations.shadow_service import (
    MIN_POLL_SEPARATION,
    STRATEGIES,
    STRATEGY_POLL_OFFSETS,
    WATCHLIST_BAR_MAX_AGE,
    bar_freshness_limit,
    poll_offset_problems,
    poll_schedule,
    strategy_entry_window,
)
from trading.operations.supervisor import STRATEGY_UNIVERSES
from trading.orchestration import RepositoryBackedShadowRunner, ShadowPipeline
from trading.orchestration.session_runner import (
    BAR_INTERVAL,
    DATA_ERROR_REASONS,
    MIN_PASS_BUDGET,
    PollSchedule,
    SessionAborted,
    SessionReport,
    ShadowSessionRunner,
    bars_error_reason,
    ensure_orb_strategy,
    ensure_strategy,
    momo_strategy_spec,
)
from trading.rules.momo import MomoStrategy
from trading.scheduler.sessions import market_session

NEW_YORK = ZoneInfo("America/New_York")
SESSION = date(2026, 10, 12)  # MOMO's first real day, a Monday session
HALF_DAY = date(2026, 11, 27)  # the day after Thanksgiving closes at 13:00


def et(hour: int, minute: int, second: float = 0, *, day: date = SESSION) -> datetime:
    whole = int(second)
    moment = datetime.combine(day, time(hour, minute, whole), tzinfo=NEW_YORK)
    return (moment + timedelta(seconds=second - whole)).astimezone(timezone.utc)


@contextmanager
def _log_lines(name: str) -> Iterator[list[str]]:
    """``LEVEL message`` lines from one logger, whatever its propagation."""
    lines: list[str] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(f"{record.levelname} {record.getMessage()}")

    target = logging.getLogger(name)
    handler = Collect(level=logging.DEBUG)
    previous = target.level
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    try:
        yield lines
    finally:
        target.removeHandler(handler)
        target.setLevel(previous)


# --------------------------------------------------------------------------
# 1. Poll offsets: pinned, keyed by the registry, 15 s apart, MOMO first
# --------------------------------------------------------------------------


def test_poll_offsets_are_pinned_per_registry_strategy_and_fifteen_seconds_apart():
    assert STRATEGY_POLL_OFFSETS == {
        "momo": timedelta(seconds=15),
        "orb": timedelta(seconds=30),
        "gap": timedelta(seconds=45),
        "vwap": timedelta(seconds=60),
        "orb-v2": timedelta(seconds=75),
        "gap-v2": timedelta(seconds=90),
        "vwap-v2": timedelta(seconds=105),
    }
    assert set(STRATEGY_POLL_OFFSETS) == set(STRATEGIES)
    # The supervisor schedules registry strategies only (the V2 simulations
    # in place of their V1 strategies).
    assert set(STRATEGY_UNIVERSES) < set(STRATEGIES)
    # IBKR refuses an identical historical request within 15 s of the first.
    assert MIN_POLL_SEPARATION == timedelta(seconds=15)
    for (first, first_at), (second, second_at) in combinations(STRATEGY_POLL_OFFSETS.items(), 2):
        assert abs(first_at - second_at) >= MIN_POLL_SEPARATION, (first, second)
    # Real MOMO asks first: a collision costs a shadow child, never MOMO.
    assert min(STRATEGY_POLL_OFFSETS, key=STRATEGY_POLL_OFFSETS.__getitem__) == "momo"
    assert momo_strategy_spec().name == "MOMO_HOD_V2"
    # VWAP polls MOMO's own hot list, so it polls furthest behind it.
    assert max(STRATEGY_POLL_OFFSETS, key=STRATEGY_POLL_OFFSETS.__getitem__) == "vwap-v2"
    scheduled = {name: STRATEGY_POLL_OFFSETS[name] for name in STRATEGY_UNIVERSES}
    assert min(scheduled, key=scheduled.__getitem__) == "momo"
    assert max(scheduled, key=scheduled.__getitem__) == "vwap-v2"
    assert poll_offset_problems(STRATEGY_POLL_OFFSETS) == []


@pytest.mark.parametrize(
    ("offsets", "problem"),
    [
        ({"momo": 15, "orb": 25, "gap": 45, "vwap": 60}, "momo and orb poll 0:00:10 apart"),
        (
            {"momo": 30, "orb": 15, "gap": 45, "vwap": 60},
            "every sendable strategy must poll before every shadow strategy",
        ),
        ({"momo": 15, "orb": 30, "gap": 45}, "poll offsets must name exactly the strategy registry"),
        ({"momo": 15, "orb": 30, "gap": 45, "vwap": 300}, "every poll offset must fall inside its bar"),
    ],
)
def test_a_broken_offset_table_refuses_every_session_loudly(monkeypatch, offsets, problem):
    table = {name: timedelta(seconds=seconds) for name, seconds in offsets.items()}
    assert problem in poll_offset_problems(table)
    monkeypatch.setattr(shadow_service, "STRATEGY_POLL_OFFSETS", table)
    with _log_lines("autotrader.shadow.service") as log, pytest.raises(SessionAborted) as refused:
        shadow_service.poll_schedule("orb", "scanner")
    assert str(refused.value).startswith("poll_offsets_invalid: ")
    assert problem in str(refused.value)
    assert any(line.startswith("CRITICAL poll_offsets_invalid: ") for line in log)


def test_each_child_keeps_a_pass_budget_under_its_freshness_limit():
    schedules = {name: poll_schedule(name, universe) for name, universe in STRATEGY_UNIVERSES.items()}
    assert {name: schedule.bar_max_age for name, schedule in schedules.items()} == {
        "momo": WATCHLIST_BAR_MAX_AGE,
        "vwap-v2": WATCHLIST_BAR_MAX_AGE,
        "gap-v2": WATCHLIST_BAR_MAX_AGE,
        # A scanner run keeps its old 25 s after its slot (30 s at +5 s before).
        "orb-v2": timedelta(seconds=100),
    }
    assert WATCHLIST_BAR_MAX_AGE == timedelta(seconds=240) and MIN_PASS_BUDGET == timedelta(seconds=25)
    assert {name: schedule.late_poll_limit for name, schedule in schedules.items()} == {
        "momo": timedelta(seconds=112.5),
        "orb-v2": timedelta(seconds=12.5),
        "gap-v2": timedelta(seconds=75),
        "vwap-v2": timedelta(seconds=67.5),
    }
    for schedule in schedules.values():
        assert schedule.bar_max_age < BAR_INTERVAL
        assert schedule.bar_max_age - schedule.offset >= MIN_PASS_BUDGET
    # Any strategy on any universe still gets a valid schedule (CLI runs).
    for name in STRATEGIES:
        for universe in shadow_service.UNIVERSES:
            assert bar_freshness_limit(name, universe) - STRATEGY_POLL_OFFSETS[name] >= MIN_PASS_BUDGET


def test_a_schedule_that_would_poll_into_stale_bars_is_refused():
    with pytest.raises(ValueError, match="follow the bar close"):
        PollSchedule(offset=timedelta(0), bar_max_age=timedelta(seconds=240))
    with pytest.raises(ValueError, match="under one bar interval"):
        PollSchedule(offset=timedelta(seconds=15), bar_max_age=BAR_INTERVAL)
    # ORB's slot with ORB's old 30 s limit: every request would arrive stale.
    with pytest.raises(ValueError, match="too little time"):
        PollSchedule(offset=STRATEGY_POLL_OFFSETS["orb"], bar_max_age=timedelta(seconds=30))
    with pytest.raises(TypeError, match="PollSchedule"):
        ShadowSessionRunner(
            market=object(),  # type: ignore[arg-type]
            read_account=_account(lambda: et(10, 0)),
            runner=object(),  # type: ignore[arg-type]
            clock=lambda: et(10, 0),
            poll_schedule=timedelta(seconds=15),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("day", [date(2026, 10, 13), HALF_DAY], ids=["full-day", "half-day"])
def test_the_offsets_keep_every_poll_of_every_window_on_full_and_half_days(day):
    session = market_session(day)
    assert session is not None
    for name in STRATEGIES:
        start, cutoff = strategy_entry_window(name, day)
        first_close = session.first_signal_at if name == "orb" else start
        if cutoff <= first_close:
            continue  # no window on this day
        closes = []
        close = first_close
        while close < cutoff:
            closes.append(close)
            close += BAR_INTERVAL
        offset = STRATEGY_POLL_OFFSETS[name]
        # Cutoffs sit on bar boundaries, so a later slot never drops the last poll.
        assert (cutoff - first_close) % BAR_INTERVAL == timedelta(0), (name, day)
        assert [slot for slot in (close + offset for close in closes) if slot < cutoff] == [
            close + offset for close in closes
        ]
        assert closes[-1] + offset < cutoff


# --------------------------------------------------------------------------
# 2. "stale market data cannot feed a candidate" on a late first poll
# --------------------------------------------------------------------------


def _quiet_bar(con_id: int, start: datetime, received_at: datetime) -> IBKRTradeBar:
    return IBKRTradeBar(
        con_id=con_id,
        interval_start_utc=start,
        interval_end_utc=start + BAR_INTERVAL,
        open=Decimal("100"),
        high=Decimal("100.5"),
        low=Decimal("99.5"),
        close=Decimal("100"),
        volume=Decimal("1000"),
        rth=True,
        received_at=received_at,
    )


class ReplyingActor:
    """TWS historical replies: today's bars through the forming one, ``latency`` late."""

    def __init__(self, clock: dict[str, datetime], *, latency: timedelta) -> None:
        self.clock = clock
        self.latency = latency
        self.requests: list[tuple[int, datetime]] = []

    async def completed_trade_bars(self, con_id: int) -> BarBatch:
        self.requests.append((con_id, self.clock["now"]))
        self.clock["now"] += self.latency
        received = self.clock["now"]
        bars = []
        start = et(9, 30, day=received.astimezone(NEW_YORK).date())
        while start <= received:
            bars.append(_quiet_bar(con_id, start, received))
            start += BAR_INTERVAL
        return BarBatch(bars=tuple(bars))


def _account(clock):
    async def read_account() -> AccountSnapshot:
        return AccountSnapshot(
            paper_account_id="DU1234567",
            client_id=20,
            observed_at=clock(),
            net_liquidation=Decimal("100000"),
            available_funds=Decimal("100000"),
            realized_day_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
        )

    return read_account


class NeverReviewed:
    async def propose_trade(self, *, setup) -> AIReview:
        raise AssertionError(f"quiet bars never build a setup ({setup.symbol})")


def _hot_list(size: int, now):
    """A screener hot list of ``size`` liquid symbols, liquidity already seeded."""

    async def source(run: Run):
        parameters: dict[str, object] = {"source": "screener_hotlist", "admitted": size}
        scanner_run = ScannerRun(
            id=f"{run.id}-hotlist",
            run_id=run.id,
            parameters=parameters,
            parameters_fingerprint=fingerprint_payload(parameters),
            status="completed",
            started_at=now(),
            completed_at=now(),
        )
        candidates = tuple(
            Candidate(
                id=f"{run.id}:{1000 + rank}",
                scanner_run_id=scanner_run.id,
                con_id=1000 + rank,
                symbol=f"HOT{rank}",
                primary_exchange="NYSE",
                rank=rank,
                admission_status="admitted",
            )
            for rank in range(1, size + 1)
        )
        liquidity = {
            candidate.con_id: LiquidityProfile(
                con_id=candidate.con_id,
                reference_price=Decimal("100"),
                average_dollar_volume=Decimal("50000000"),
                sessions_used=20,
                last_session_date=date(2026, 10, 9),
            )
            for candidate in candidates
        }
        return scanner_run, candidates, liquidity

    return source


async def _momo_restart(tmp_path, monkeypatch, *, schedule: PollSchedule, restart_at: datetime):
    """A MOMO child started at ``restart_at`` against the real market boundary."""
    from config import cfg
    from db.core import init_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "momo-restart.db"))
    await init_db()
    strategy = await ensure_strategy(owner_id="demo", spec=momo_strategy_spec())
    clock = {"now": restart_at}
    now = lambda: clock["now"]  # noqa: E731 - the shared simulated clock

    async def sleep(seconds: float) -> None:
        clock["now"] += timedelta(seconds=seconds)

    # ~0.7 s per reply, as live: 33 refusals in 22 s on 2026-10-12.
    actor = ReplyingActor(clock, latency=timedelta(seconds=0.7))
    market = PaperIBKRMarketData(actor=actor, host="127.0.0.1", bar_max_age=schedule.bar_max_age)
    runner = ShadowSessionRunner(
        market=market,
        read_account=_account(now),
        runner=RepositoryBackedShadowRunner(
            pipeline=ShadowPipeline(proposal_provider=NeverReviewed(), strategy=MomoStrategy()),
            clock=now,
            lease_ttl=timedelta(hours=6),
            lease_renew_interval=timedelta(hours=1),
        ),
        clock=now,
        poll_schedule=schedule,
        sleep=sleep,
    )
    window_start = strategy_entry_window("momo", SESSION)[0]
    with _log_lines("autotrader.shadow.session") as log:
        report = await runner.run(
            session=market_session(SESSION),
            strategy=strategy,
            policy=RiskPolicy(),
            source_sha="abcdef1",
            policy_fingerprints={"risk": RiskPolicy().fingerprint()},
            run_id=f"momo-restart-{restart_at:%H%M%S}",
            candidate_source=_hot_list(35, now),
            window_start=window_start,
        )
    return report, actor, log


@pytest.mark.parametrize(
    ("schedule", "restart_at", "old_request_at", "skipped_slot", "first_poll"),
    [
        # The live failure: 2026-10-12's 90 s watchlist limit and 5 s slot; the
        # run started at 14:31:23 ET, and the first refusal came at 14:31:31.
        (
            PollSchedule(offset=timedelta(seconds=5), bar_max_age=timedelta(seconds=90)),
            et(14, 31, 23),
            et(14, 31, 30.3),
            "14:30:05",
            et(14, 35, 5),
        ),
        # Today's MOMO slot and 240 s limit, restarted late in the bar.
        (poll_schedule("momo", "watchlist"), et(14, 34), et(14, 34), "14:30:15", et(14, 35, 15)),
    ],
    ids=["2026-10-12-live", "momo-today"],
)
async def test_a_late_start_waits_for_its_next_slot_instead_of_polling_stale_bars(
    tmp_path, monkeypatch, schedule, restart_at, old_request_at, skipped_slot, first_poll
):
    # The poll the old catch-up made at once is refused by the freshness rule.
    probe_clock = {"now": old_request_at}
    probe = PaperIBKRMarketData(
        actor=ReplyingActor(probe_clock, latency=timedelta(seconds=0.7)),
        host="127.0.0.1",
        bar_max_age=schedule.bar_max_age,
    )
    with pytest.raises(MarketDataQualityError, match="stale market data") as refused:
        await probe.completed_bars(con_id=1001, now=old_request_at)
    assert refused.value.fault == FAULT_STALE

    report, actor, log = await _momo_restart(
        tmp_path, monkeypatch, schedule=schedule, restart_at=restart_at
    )

    # No request is spent on bars that cannot pass; the next slot's all pass.
    assert actor.requests[0] == (1001, first_poll)
    assert report.late_polls_skipped == 1
    assert (report.data_errors, dict(report.data_error_reasons)) == (0, {})
    assert report.polls == 5  # 14:35 through 14:55
    assert len(actor.requests) == 5 * 35
    assert dict(report.outcomes) == {"rule_rejected": 35}
    late = [line for line in log if "[late_polls_skipped]" in line]
    assert len(late) == 1 and late[0].startswith(f"WARNING skipping the {skipped_slot} ET poll")
    assert not any("bars unavailable" in line for line in log)


async def test_a_slot_missed_by_less_than_half_its_budget_is_still_polled_at_once(
    tmp_path, monkeypatch
):
    schedule = poll_schedule("momo", "watchlist")
    report, actor, _log = await _momo_restart(
        tmp_path, monkeypatch, schedule=schedule, restart_at=et(14, 30, 40)
    )
    # 25 s after the 14:30:15 slot: the 14:30 bar is still fresh for the pass.
    assert actor.requests[0] == (1001, et(14, 30, 40))
    assert report.late_polls_skipped == 0
    assert (report.data_errors, report.polls) == (0, 6)


async def test_a_pass_that_overruns_its_next_slot_skips_it(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "overrun.db"))
    await init_db()
    strategy = await ensure_strategy(owner_id="demo", spec=momo_strategy_spec())
    schedule = poll_schedule("momo", "watchlist")
    clock = {"now": et(9, 50)}  # started on time, before MOMO's window
    now = lambda: clock["now"]  # noqa: E731 - the shared simulated clock

    async def sleep(seconds: float) -> None:
        clock["now"] += timedelta(seconds=seconds)

    reads: list[datetime] = []

    async def slow_account() -> AccountSnapshot:
        reads.append(now())
        if len(reads) == 1:
            clock["now"] += timedelta(minutes=7)  # a broker download that hung
        return await _account(now)()

    actor = ReplyingActor(clock, latency=timedelta(seconds=0.7))
    runner = ShadowSessionRunner(
        market=PaperIBKRMarketData(actor=actor, host="127.0.0.1", bar_max_age=schedule.bar_max_age),
        read_account=slow_account,
        runner=RepositoryBackedShadowRunner(
            pipeline=ShadowPipeline(proposal_provider=NeverReviewed(), strategy=MomoStrategy()),
            clock=now,
            lease_ttl=timedelta(hours=6),
            lease_renew_interval=timedelta(hours=1),
        ),
        clock=now,
        poll_schedule=schedule,
        sleep=sleep,
    )
    with _log_lines("autotrader.shadow.session") as log:
        report = await runner.run(
            session=market_session(SESSION),
            strategy=strategy,
            policy=RiskPolicy(),
            source_sha="abcdef1",
            policy_fingerprints={"risk": RiskPolicy().fingerprint()},
            run_id="momo-overrun",
            candidate_source=_hot_list(3, now),
            window_start=strategy_entry_window("momo", SESSION)[0],
        )
    # The 10:00:15 pass ended at 10:07:17; its 10:05:15 successor was 122 s
    # late, so the next poll is 10:10:15.
    assert reads[:2] == [et(10, 0, 15), et(10, 10, 15)]
    assert report.late_polls_skipped == 1
    skipped = [line for line in log if "[late_polls_skipped]" in line]
    assert skipped == [
        "WARNING skipping the 10:05:15 ET poll [late_polls_skipped]: "
        "122 s late, past the 112 s a late poll may start"
    ]
    assert report.data_errors == 0


# --------------------------------------------------------------------------
# 3. The data-error breakdown in the "session complete" payload
# --------------------------------------------------------------------------


def test_data_error_reasons_and_market_data_faults_are_pinned():
    assert MARKET_DATA_FAULTS == {
        "stale",
        "pacing",
        "disconnected",
        "request_failed",
        "receipt_in_future",
        "invalid",
    }
    assert DATA_ERROR_REASONS == {
        "account_unavailable",
        "liquidity_unavailable",
        "quote_unavailable",
        "bars_error",
        "bars_stale",
        "bars_pacing",
        "bars_disconnected",
        "bars_request_failed",
        "bars_receipt_in_future",
        "bars_invalid",
    }
    for fault in MARKET_DATA_FAULTS:
        assert bars_error_reason(MarketDataQualityError("refused", fault=fault)) == f"bars_{fault}"
    assert bars_error_reason(TimeoutError("TWS historical-data request timed out")) == "bars_error"
    with pytest.raises(ValueError, match="unknown market-data fault"):
        MarketDataQualityError("refused", fault="slow")


def _refusal(batch: BarBatch, *, now: datetime) -> MarketDataQualityError:
    with pytest.raises(MarketDataQualityError) as refused:
        validate_completed_trade_bars(batch, now=now)
    return refused.value


def test_every_bar_refusal_names_its_fault():
    now = et(10, 0, 5)
    fresh = _quiet_bar(7, et(9, 55), now)
    assert _refusal(BarBatch(bars=(), connected=False), now=now).fault == FAULT_DISCONNECTED
    assert _refusal(BarBatch(bars=(), pacing_failed=True), now=now).fault == FAULT_PACING
    assert _refusal(BarBatch(bars=(), error="TWS error 200: x"), now=now).fault == FAULT_REQUEST_FAILED
    assert _refusal(BarBatch(bars=(fresh, fresh)), now=now).fault == FAULT_INVALID
    future = _quiet_bar(7, et(9, 55), now + timedelta(seconds=2))
    assert _refusal(BarBatch(bars=(future,)), now=now).fault == FAULT_RECEIPT_IN_FUTURE
    old = _quiet_bar(7, et(9, 50), now)
    stale = _refusal(BarBatch(bars=(old,)), now=now)
    assert (stale.fault, str(stale)) == (FAULT_STALE, "stale market data cannot feed a candidate")
    assert validate_completed_trade_bars(BarBatch(bars=(fresh,)), now=now)[0].con_id == 7
    with pytest.raises(MarketDataQualityError) as daily:
        liquidity_profile(
            DailyBarBatch(bars=(), pacing_failed=True),
            session_date=SESSION,
            volume_multiplier=Decimal("100"),
        )
    assert daily.value.fault == FAULT_PACING


def test_a_refused_request_keeps_ibkrs_own_report():
    # The actor maps 162, 165, 366 and 420 to pacing; only IBKR's text tells
    # a pacing violation from, say, a login active elsewhere.
    now = et(10, 0, 5)
    violation = "TWS error 162: Historical Market Data Service error message:pacing violation"
    pacing = _refusal(BarBatch(bars=(), pacing_failed=True, error=violation), now=now)
    assert (pacing.fault, str(pacing)) == (
        FAULT_PACING,
        f"IBKR market-data pacing limit blocked the request ({violation})",
    )
    elsewhere = "TWS error 165: Trading TWS session is connected from a different IP address"
    competing = _refusal(BarBatch(bars=(), pacing_failed=True, error=elsewhere), now=now)
    assert str(competing).endswith(f"({elsewhere})")
    failed = _refusal(BarBatch(bars=(), error="TWS error 200: No security definition"), now=now)
    assert str(failed) == "IBKR market-data request failed (TWS error 200: No security definition)"
    closed = _refusal(BarBatch(bars=(), connected=False), now=now)
    assert str(closed) == "IBKR market-data connection is unavailable"


def test_an_unregistered_reason_is_still_counted_and_raises_a_critical():
    report = SessionReport(run_id="run", session_date=SESSION)
    with _log_lines("autotrader.shadow.session") as log:
        report.record_data_error("bars_stale")
        report.record_data_error("bars_mystery")
    assert report.data_errors == 2 == sum(report.data_error_reasons.values())
    assert report.summary()["data_error_reasons"] == {"bars_mystery": 1, "bars_stale": 1}
    assert log == ["CRITICAL unregistered data-error reason 'bars_mystery'"]


ORB_DAY = date(2026, 8, 27)
ORB_OPEN = et(9, 30, day=ORB_DAY)


def _orb_bar(con_id: int, start: datetime, *, breakout: bool):
    from trading.contracts import CompletedBar

    opening = start < ORB_OPEN + timedelta(minutes=15)
    return CompletedBar(
        con_id=con_id,
        interval_start_utc=start,
        interval_end_utc=start + BAR_INTERVAL,
        open="100",
        high="103.2" if breakout else ("101" if opening else "100.8"),
        low="99",
        close="103" if breakout else "100.5",
        volume="10000",
        rth=True,
        source="ibkr",
        data_mode="live",
    )


class FailingMarket:
    """One ORB scan whose every candidate fails in its own way."""

    SYMBOLS = {101: "BRK", 102: "STAL", 103: "PACE", 105: "SLOW", 106: "THIN"}

    def __init__(self, clock) -> None:
        self.clock = clock

    async def scanner_snapshot(self, *, scanner_run_id: str):
        results = tuple(
            IBKRScannerResult(
                con_id=con_id,
                symbol=symbol,
                currency="USD",
                security_type="STK",
                stock_type="COMMON",
                primary_exchange="NASDAQ",
                rank=rank,
                raw_rank=rank - 1,
            )
            for rank, (con_id, symbol) in enumerate(self.SYMBOLS.items(), 1)
        )
        return qualify_scanner_results(results, scanner_run_id=scanner_run_id)

    async def completed_bars(self, *, con_id: int, now: datetime):
        if con_id == 102:
            raise MarketDataQualityError("stale market data cannot feed a candidate", fault=FAULT_STALE)
        if con_id == 103:
            raise MarketDataQualityError(
                "IBKR market-data pacing limit blocked the request", fault=FAULT_PACING
            )
        if con_id == 105:
            raise TimeoutError("TWS historical-data request timed out")
        bars = []
        start = ORB_OPEN
        while start + BAR_INTERVAL <= now:
            bars.append(_orb_bar(con_id, start, breakout=start >= ORB_OPEN + timedelta(minutes=40)))
            start += BAR_INTERVAL
        return tuple(bars)

    async def quote(self, *, con_id: int):
        raise TimeoutError("TWS quote snapshot timed out")

    async def liquidity(self, *, con_id: int, session_date: date):
        if con_id == 106:
            raise MarketDataQualityError("insufficient_daily_history")
        return LiquidityProfile(
            con_id=con_id,
            reference_price=Decimal("103"),
            average_dollar_volume=Decimal("25000000"),
            sessions_used=20,
            last_session_date=session_date - timedelta(days=1),
        )


async def test_session_complete_breaks_data_errors_down_by_reason(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "breakdown.db"))
    await init_db()
    strategy = await ensure_orb_strategy(owner_id="demo")
    clock = {"now": et(11, 44, day=ORB_DAY)}  # restarted late in the morning
    now = lambda: clock["now"]  # noqa: E731 - the shared simulated clock

    async def sleep(seconds: float) -> None:
        clock["now"] += timedelta(seconds=seconds)

    reads: list[datetime] = []

    async def read_account() -> AccountSnapshot:
        reads.append(now())
        if len(reads) == 1:
            raise TimeoutError("TWS account download timed out")
        return await _account(now)()

    runner = ShadowSessionRunner(
        market=FailingMarket(now),
        read_account=read_account,
        runner=RepositoryBackedShadowRunner(
            pipeline=ShadowPipeline(proposal_provider=NeverReviewed()),
            clock=now,
            lease_ttl=timedelta(hours=6),
            lease_renew_interval=timedelta(hours=1),
        ),
        clock=now,
        poll_schedule=poll_schedule("orb", "scanner"),
        sleep=sleep,
    )
    with _log_lines("autotrader.shadow.session") as log:
        report = await runner.run(
            session=market_session(ORB_DAY),
            strategy=strategy,
            policy=RiskPolicy(),
            source_sha="abcdef1",
            policy_fingerprints={"risk": RiskPolicy().fingerprint()},
        )

    # The 11:40:30 slot was missed; 11:45:30 lost the account, then two full polls.
    assert report.polls == 3 and report.late_polls_skipped == 1
    expected = {
        "account_unavailable": 1,
        "bars_error": 2,
        "bars_pacing": 2,
        "bars_stale": 2,
        "liquidity_unavailable": 2,
        "quote_unavailable": 2,
    }
    summary = report.summary()
    assert summary["data_error_reasons"] == expected
    assert summary["data_errors"] == report.data_errors == sum(expected.values()) == 11
    assert dict(report.outcomes) == {"data_blocked": 5}
    complete = [line for line in log if line.startswith("INFO session complete: ")]
    assert len(complete) == 1
    assert (
        "'data_errors': 11, 'data_error_reasons': {'account_unavailable': 1, "
        "'bars_error': 2, 'bars_pacing': 2, 'bars_stale': 2, 'liquidity_unavailable': 2, "
        "'quote_unavailable': 2}" in complete[0]
    )
    # Every warning carries the same token as the summary key.
    tagged = {
        "account_unavailable": "WARNING account snapshot unavailable [account_unavailable]: ",
        "bars_stale": "WARNING bars unavailable for STAL [bars_stale]: stale market data",
        "bars_pacing": "WARNING bars unavailable for PACE [bars_pacing]: IBKR market-data pacing",
        "bars_error": "WARNING bars unavailable for SLOW [bars_error]: TWS historical-data",
        "liquidity_unavailable": "WARNING liquidity unavailable for THIN [liquidity_unavailable]: ",
        "quote_unavailable": "WARNING quote unavailable for BRK [quote_unavailable]: ",
    }
    for reason, prefix in tagged.items():
        assert sum(line.startswith(prefix) for line in log) == expected[reason], reason
