"""Dynamic hot-list runs: symbols that heat up mid-session join the same run."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import logging
from zoneinfo import ZoneInfo

import pytest

from trading.contracts import (
    AccountSnapshot,
    AIReview,
    Candidate,
    CompletedBar,
    EngineMode,
    PortfolioSnapshot,
    QuoteSnapshot,
    RiskPolicy,
    Run,
    ScannerRun,
    StrategyRecord,
    fingerprint_payload,
)
from trading.data import CandidateRepository, RunRepository, ScannerRunRepository
from trading.market.ibkr_boundary import LiquidityProfile
from trading.orchestration import (
    CandidateInput,
    RepositoryBackedShadowRunner,
    ShadowPipeline,
    ShadowPersistenceConflictError,
)
from trading.operations.shadow_service import poll_schedule
from trading.orchestration.persistence import MAX_CANDIDATE_RANK
from trading.orchestration.session_runner import ShadowSessionRunner, ensure_orb_strategy
from trading.orchestration.watchlist_source import HotListUnavailable
from trading.scheduler.sessions import market_session
from trading.storage.repository import list_decisions

ET = ZoneInfo("America/New_York")
SESSION = date(2026, 8, 27)
HASH = "d" * 64
# ORB's rules on the dynamic hot list: its production slot, 30 s after each close.
SCHEDULE = poll_schedule("orb", "watchlist")


def et(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime.combine(SESSION, time(hour, minute, second), tzinfo=ET).astimezone(timezone.utc)


OPEN = et(9, 30)
CUTOFF = et(12, 0)  # ORB_V1's entry cutoff
PORTFOLIO = PortfolioSnapshot(observed_at=et(10, 0), net_liquidation="100000")


@contextmanager
def _log_lines(name: str) -> Iterator[list[str]]:
    """``LEVEL message`` lines from one logger, whatever its propagation."""
    lines: list[str] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(f"{record.levelname} {record.getMessage()}")

    log = logging.getLogger(name)
    handler = Collect(level=logging.DEBUG)
    previous = log.level
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        yield lines
    finally:
        log.removeHandler(handler)
        log.setLevel(previous)


def _bar(con_id: int, start: datetime, *, high: str, close: str) -> CompletedBar:
    return CompletedBar(
        con_id=con_id,
        interval_start_utc=start,
        interval_end_utc=start + timedelta(minutes=5),
        open="100",
        high=high,
        low="99",
        close=close,
        volume="10000",
        rth=True,
        source="ibkr",
        data_mode="live",
    )


def _day_bars(con_id: int, now: datetime, breakout_at: datetime | None) -> tuple[CompletedBar, ...]:
    """Yesterday's history, today's 101 opening range, then a 103 close from ``breakout_at``."""
    prior_open = OPEN - timedelta(days=1)
    bars = [
        _bar(con_id, prior_open + timedelta(minutes=5 * index), high="101", close="100")
        for index in range(20)
    ]
    start = OPEN
    while start + timedelta(minutes=5) <= now:
        breakout = breakout_at is not None and start >= breakout_at
        quiet_high = "101" if start < OPEN + timedelta(minutes=15) else "100.8"
        bars.append(
            _bar(
                con_id,
                start,
                high="103.2" if breakout else quiet_high,
                close="103" if breakout else "100.5",
            )
        )
        start += timedelta(minutes=5)
    return tuple(bars)


def _quote(con_id: int, now: datetime) -> QuoteSnapshot:
    return QuoteSnapshot(con_id=con_id, bid="102.99", ask="103", observed_at=now, min_tick="0.01")


class Provider:
    def __init__(self) -> None:
        self.symbols: list[str] = []

    async def propose_trade(self, *, setup):
        self.symbols.append(setup.symbol)
        return AIReview(
            setup_fingerprint=setup.fingerprint(),
            decision="PROPOSE",
            confidence="0.7",
            rationale="Dynamic candidate fixture review.",
            model_id="fixture",
            prompt_version="fixture",
            response_hash=HASH,
            latency_ms=1,
            cost_usd="0.02",
        )


# --- the leased session ----------------------------------------------------------


async def _world(tmp_path, monkeypatch, *, name: str = "dynamic.db") -> StrategyRecord:
    from config import cfg
    from db.core import init_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / name))
    await init_db()
    return await ensure_orb_strategy(owner_id="demo")


def _run(strategy: StrategyRecord) -> Run:
    return Run(
        id="run-dynamic",
        owner_id="demo",
        source_sha="abcdef1",
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        policy_fingerprints={"risk": RiskPolicy().fingerprint()},
        session_date=SESSION,
        mode=EngineMode.SHADOW,
    )


def _scanner(run: Run, snapshot_id: str) -> ScannerRun:
    parameters: dict[str, object] = {"source": "screener_hotlist", "snapshot_id": snapshot_id}
    return ScannerRun(
        id=f"{run.id}-hotlist",
        run_id=run.id,
        parameters=parameters,
        parameters_fingerprint=fingerprint_payload(parameters),
        status="completed",
        started_at=et(9, 55),
        completed_at=et(9, 55),
    )


def _candidate(run: Run, symbol: str, con_id: int, rank: int = 1) -> Candidate:
    """A hot-list candidate; ``rank`` is provisional until the session admits it."""
    return Candidate(
        id=f"{run.id}:{con_id}",
        scanner_run_id=f"{run.id}-hotlist",
        con_id=con_id,
        symbol=symbol,
        primary_exchange="NASDAQ",
        rank=rank,
        admission_status="admitted",
    )


def _item(
    candidate: Candidate,
    now: datetime,
    *,
    breakout_at: datetime | None = None,
    quote: bool = False,
) -> CandidateInput:
    return CandidateInput(
        candidate=candidate,
        bars=_day_bars(candidate.con_id, now, breakout_at),
        quote=_quote(candidate.con_id, now) if quote else None,
        reference_price="103",
        average_dollar_volume="25000000",
    )


def _runner(
    clock: dict[str, datetime], provider: Provider | None = None
) -> RepositoryBackedShadowRunner:
    # The fake clock jumps without background renewal, so the lease outlives the day.
    return RepositoryBackedShadowRunner(
        pipeline=ShadowPipeline(proposal_provider=provider or Provider()),
        clock=lambda: clock["now"],
        lease_ttl=timedelta(hours=6),
        lease_renew_interval=timedelta(hours=1),
    )


async def _status(run: Run) -> str:
    stored = await RunRepository().get(run_id=run.id, owner_id=run.owner_id)
    assert stored is not None
    return stored.status


async def _stored_ranks(run: Run) -> dict[str, int]:
    stored = await CandidateRepository().list_for_scanner(f"{run.id}-hotlist")
    return {candidate.symbol: candidate.rank for candidate in stored}


async def test_added_candidates_take_the_next_free_rank_and_readds_are_no_ops(
    tmp_path, monkeypatch
):
    strategy = await _world(tmp_path, monkeypatch)
    run = _run(strategy)
    clock = {"now": et(10, 0)}
    aapl, msft = _candidate(run, "AAPL", 101, 1), _candidate(run, "MSFT", 102, 2)

    async with _runner(clock).open_session(
        run=run,
        scanner_run=_scanner(run, "snap-1"),
        strategy=strategy,
        candidates=(aapl, msft),
        policy=RiskPolicy(),
        holder_id="daemon-1",
        dynamic=True,
    ) as session:
        clock["now"] = et(10, 20)
        # A refresh hands provisional ranks; a held conId is a no-op whatever its rank.
        added = await session.add_candidates(
            (_candidate(run, "NVDA", 103, 1), _candidate(run, "AAPL", 101, 7))
        )
        assert [(candidate.symbol, candidate.rank) for candidate in added] == [("NVDA", 3)]
        assert await session.add_candidates((_candidate(run, "NVDA", 103, 1),)) == ()
        (nvda,) = added
        assert [candidate.symbol for candidate in session.pending()] == ["AAPL", "MSFT", "NVDA"]

        # The added symbol is part of the run's admitted universe: a breakout
        # passes review and risk like any candidate selected at the start.
        clock["now"] = et(10, 45, 5)
        quoted = _item(nvda, clock["now"], breakout_at=et(10, 40), quote=True)
        accepted = await session.assess(quoted, portfolio=PORTFOLIO)
        assert accepted.decision is not None
        assert accepted.decision.outcome.value == "shadow_accepted"

        with pytest.raises(ValueError, match="does not belong to scanner run"):
            await session.add_candidates((aapl.model_copy(update={"scanner_run_id": "elsewhere"}),))

    assert await _stored_ranks(run) == {"AAPL": 1, "MSFT": 2, "NVDA": 3}


async def test_additions_stop_at_the_rank_cap_and_log_it_once(tmp_path, monkeypatch):
    strategy = await _world(tmp_path, monkeypatch)
    run = _run(strategy)
    clock = {"now": et(10, 0)}
    opening = tuple(_candidate(run, f"S{index}", 1000 + index, index) for index in range(1, 49))

    with _log_lines("autotrader.shadow.persistence") as log:
        async with _runner(clock).open_session(
            run=run,
            scanner_run=_scanner(run, "snap-1"),
            strategy=strategy,
            candidates=opening,
            policy=RiskPolicy(),
            holder_id="daemon-1",
            dynamic=True,
        ) as session:
            later = tuple(_candidate(run, f"N{index}", 2000 + index) for index in range(1, 6))
            added = await session.add_candidates(later)
            assert [(candidate.symbol, candidate.rank) for candidate in added] == [
                ("N1", 49),
                ("N2", 50),
            ]
            more = tuple(_candidate(run, f"M{index}", 3000 + index) for index in range(1, 4))
            assert await session.add_candidates(more) == ()
            assert len(session.candidates) == MAX_CANDIDATE_RANK == 50

    assert len([line for line in log if "(the cap)" in line]) == 1
    assert len(await _stored_ranks(run)) == 50


async def test_dynamic_run_completes_only_at_the_cutoff_sweep(tmp_path, monkeypatch):
    strategy = await _world(tmp_path, monkeypatch)
    run = _run(strategy)
    clock = {"now": et(10, 0)}
    aapl = _candidate(run, "AAPL", 101)

    async with _runner(clock).open_session(
        run=run,
        scanner_run=_scanner(run, "snap-1"),
        strategy=strategy,
        candidates=(aapl,),
        policy=RiskPolicy(),
        holder_id="daemon-1",
        dynamic=True,
    ) as session:
        clock["now"] = et(10, 15, 5)
        decided = await session.assess(
            _item(aapl, clock["now"], breakout_at=et(10, 10), quote=True), portfolio=PORTFOLIO
        )
        assert decided.decision is not None
        # Every candidate is terminal, yet a later refresh may still add one.
        assert session.complete and await _status(run) == "running"

        clock["now"] = et(11, 0)
        (msft,) = await session.add_candidates((_candidate(run, "MSFT", 102),))
        assert session.pending() == (msft,)

        clock["now"] = CUTOFF
        assert await session.add_candidates((_candidate(run, "NVDA", 103),)) == ()
        final = await session.finalize([], portfolio=PORTFOLIO, now=CUTOFF)
        assert final[msft.id].outcome.value == "data_blocked"
        assert await _status(run) == "completed"
        assert await session.add_candidates((_candidate(run, "AMD", 104),)) == ()

    assert len(await list_decisions(owner_id="demo", run_id=run.id)) == 2


class Crash(BaseException):
    """A process kill: nothing in the session may swallow it."""


async def test_reopened_dynamic_run_resumes_every_stored_candidate(tmp_path, monkeypatch):
    strategy = await _world(tmp_path, monkeypatch)
    run = _run(strategy)
    clock = {"now": et(9, 55)}
    provider = Provider()
    runner = _runner(clock, provider)
    first_snapshot = _scanner(run, "snap-1")
    aapl, msft = _candidate(run, "AAPL", 101, 1), _candidate(run, "MSFT", 102, 2)

    with pytest.raises(Crash):
        async with runner.open_session(
            run=run,
            scanner_run=first_snapshot,
            strategy=strategy,
            candidates=(aapl, msft),
            policy=RiskPolicy(),
            holder_id="daemon-1",
            dynamic=True,
        ) as session:
            clock["now"] = et(10, 15, 5)
            await session.assess(
                _item(aapl, clock["now"], breakout_at=et(10, 10), quote=True), portfolio=PORTFOLIO
            )
            clock["now"] = et(10, 20, 5)
            await session.add_candidates((_candidate(run, "NVDA", 103),))
            raise Crash
    assert await _status(run) == "failed"

    # The restarted process reads a newer snapshot whose hot list dropped AAPL
    # and NVDA, re-ranked MSFT and added AMD.
    clock["now"] = et(10, 31)
    async with runner.open_session(
        run=run,
        scanner_run=_scanner(run, "snap-2"),
        strategy=strategy,
        candidates=(_candidate(run, "AMD", 104, 1), _candidate(run, "MSFT", 102, 2)),
        policy=RiskPolicy(),
        holder_id="daemon-2",
        dynamic=True,
    ) as resumed:
        assert [candidate.symbol for candidate in resumed.candidates] == [
            "AAPL",
            "MSFT",
            "NVDA",
            "AMD",
        ]
        assert [candidate.symbol for candidate in resumed.pending()] == ["MSFT", "NVDA", "AMD"]
        assert resumed.decisions[aapl.id].outcome.value == "shadow_accepted"
        assert await _status(run) == "running"

    assert await _stored_ranks(run) == {"AAPL": 1, "MSFT": 2, "NVDA": 3, "AMD": 4}
    # The scanner run keeps the first selection's parameters.
    assert await ScannerRunRepository().get(first_snapshot.id) == first_snapshot
    assert provider.symbols == ["AAPL"]


async def test_fixed_sessions_keep_their_universe(tmp_path, monkeypatch):
    """ORB scanner runs are unchanged: no additions, and a re-scan cannot resume."""
    strategy = await _world(tmp_path, monkeypatch)
    run = _run(strategy)
    clock = {"now": et(10, 0)}
    aapl = _candidate(run, "AAPL", 101)

    async with _runner(clock).open_session(
        run=run,
        scanner_run=_scanner(run, "snap-1"),
        strategy=strategy,
        candidates=(aapl,),
        policy=RiskPolicy(),
        holder_id="daemon-1",
    ) as session:
        assert session.dynamic is False
        with pytest.raises(ValueError, match="only a dynamic session"):
            await session.add_candidates((_candidate(run, "MSFT", 102),))
        clock["now"] = et(10, 15, 5)
        await session.assess(
            _item(aapl, clock["now"], breakout_at=et(10, 10), quote=True), portfolio=PORTFOLIO
        )
        # The last terminal decision completes a fixed run at once.
        assert await _status(run) == "completed"

    with pytest.raises(ShadowPersistenceConflictError, match="stored scanner run differs"):
        async with _runner(clock).open_session(
            run=run,
            scanner_run=_scanner(run, "snap-2"),
            strategy=strategy,
            candidates=(aapl,),
            policy=RiskPolicy(),
            holder_id="daemon-2",
        ):
            pass


# --- the session runner -------------------------------------------------------------


class FakeMarket:
    """Bars, quotes and liquidity; a quote needs its contract loaded, as live."""

    def __init__(self, clock, breakouts: dict[int, datetime]) -> None:
        self.clock = clock
        self.breakouts = breakouts
        self.bar_requests: list[tuple[int, datetime]] = []
        self.quotes: list[int] = []
        self.loaded: set[int] = set()
        self.liquidity_requests: list[int] = []

    async def scanner_snapshot(self, *, scanner_run_id):
        raise AssertionError("hot-list sessions never scan")

    async def completed_bars(self, *, con_id, now):
        self.bar_requests.append((con_id, self.clock()))
        return _day_bars(con_id, now, self.breakouts.get(con_id))

    async def load_contract(self, con_id: int) -> None:
        self.loaded.add(con_id)

    async def quote(self, *, con_id):
        if con_id not in self.loaded:
            raise RuntimeError("contract details were not loaded for this conId")
        self.quotes.append(con_id)
        return _quote(con_id, self.clock())

    async def liquidity(self, *, con_id, session_date):
        self.liquidity_requests.append(con_id)
        return _profile(con_id, session_date)


def _profile(con_id: int, session_date: date) -> LiquidityProfile:
    return LiquidityProfile(
        con_id=con_id,
        # PENY trades at $1.36, below ORB's price floor.
        reference_price=Decimal("1.36") if con_id == 104 else Decimal("103"),
        average_dollar_volume=Decimal("25000000"),
        sessions_used=20,
        last_session_date=session_date - timedelta(days=1),
    )


class FakeHotList:
    """The screener's hot list over the day: ``(from, ((symbol, conId), ...))``."""

    def __init__(self, clock, schedule) -> None:
        self.clock = clock
        self.schedule = schedule
        self.selections = 0
        self.refreshes: list[datetime] = []
        self.failures = 0

    def _rows(self):
        return [rows for start, rows in self.schedule if start <= self.clock()][-1]

    def _candidates(self, run: Run, rows) -> tuple[Candidate, ...]:
        return tuple(
            _candidate(run, symbol, con_id, rank) for rank, (symbol, con_id) in enumerate(rows, 1)
        )

    def _liquidity(self, rows) -> dict[int, LiquidityProfile]:
        return {con_id: _profile(con_id, SESSION) for _symbol, con_id in rows}

    async def source(self, run: Run):
        self.selections += 1
        rows = self._rows()
        parameters: dict[str, object] = {
            "source": "screener_hotlist",
            "snapshot_id": f"snap-{self.selections}",
            "admitted": len(rows),
        }
        scanner_run = ScannerRun(
            id=f"{run.id}-hotlist",
            run_id=run.id,
            parameters=parameters,
            parameters_fingerprint=fingerprint_payload(parameters),
            status="completed",
            started_at=self.clock(),
            completed_at=self.clock(),
        )
        return scanner_run, self._candidates(run, rows), self._liquidity(rows)

    async def refresh(self, run: Run, now: datetime):
        self.refreshes.append(now)
        if self.failures:
            self.failures -= 1
            raise HotListUnavailable("screener snapshot is 0:07:00 old")
        # The whole list: re-adding held symbols must be a no-op.
        rows = self._rows()
        return self._candidates(run, rows), self._liquidity(rows)


def _account_reader(clock, crash_at: datetime | None = None):
    async def read_account() -> AccountSnapshot:
        if crash_at is not None and clock["now"] >= crash_at:
            raise Crash
        return AccountSnapshot(
            paper_account_id="DU1",
            client_id=20,
            observed_at=clock["now"],
            net_liquidation=Decimal("100000"),
            available_funds=Decimal("100000"),
            realized_day_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
        )

    return read_account


def _session_runner(clock, market: FakeMarket, provider: Provider, *, crash_at=None, holder: str):
    async def sleep(seconds: float) -> None:
        clock["now"] += timedelta(seconds=seconds)

    return ShadowSessionRunner(
        market=market,
        read_account=_account_reader(clock, crash_at),
        runner=_runner(clock, provider),
        clock=lambda: clock["now"],
        poll_schedule=SCHEDULE,
        sleep=sleep,
        holder_id=holder,
        contract_loader=market.load_contract,
    )


async def _run_day(runner: ShadowSessionRunner, strategy: StrategyRecord, hot_list: FakeHotList):
    return await runner.run(
        session=market_session(SESSION),
        strategy=strategy,
        policy=RiskPolicy(),
        source_sha="abcdef1",
        policy_fingerprints={"risk": RiskPolicy().fingerprint()},
        candidate_source=hot_list.source,
        candidate_refresh=hot_list.refresh,
    )


# AAPL breaks out at 10:10; PENY fails ORB's price floor; NVDA joins the hot
# list at 10:20 and breaks out at 10:40; AMD joins at 10:30 and stays quiet.
BREAKOUTS = {101: et(10, 10), 103: et(10, 40)}
OPENING_HOT = (("AAPL", 101), ("PENY", 104))
NVDA_HOT = (("AAPL", 101), ("PENY", 104), ("NVDA", 103))


async def test_symbol_that_heats_up_mid_session_is_evaluated_at_the_next_poll(
    tmp_path, monkeypatch
):
    strategy = await _world(tmp_path, monkeypatch)
    clock = {"now": OPEN - timedelta(minutes=20)}
    market = FakeMarket(lambda: clock["now"], BREAKOUTS)
    provider = Provider()
    hot_list = FakeHotList(
        lambda: clock["now"],
        [(OPEN - timedelta(hours=1), OPENING_HOT), (et(10, 20), NVDA_HOT)],
    )
    hot_list.failures = 1  # the first refresh finds a stale screener

    with _log_lines("autotrader.shadow.session") as log:
        report = await _run_day(
            _session_runner(clock, market, provider, holder="daemon-1"), strategy, hot_list
        )

    # Refreshes run every 15 minutes before a poll, from the first poll on.
    assert hot_list.refreshes[:3] == [et(9, 50, 30), et(10, 5, 30), et(10, 20, 30)]
    assert any(
        line.startswith("WARNING candidate refresh failed: screener snapshot") for line in log
    )
    assert "INFO refresh added 1 candidates: NVDA" in log
    # NVDA's bars are first requested in the poll right after it turned hot.
    assert min(at for con_id, at in market.bar_requests if con_id == 103) == et(10, 20, 30)
    # Quotes only at breakouts, each after the runner loaded the contract.
    assert market.quotes == [101, 103] and market.loaded == {101, 103}
    assert provider.symbols == ["AAPL", "NVDA"]
    assert (report.candidates, report.added_candidates) == (3, 1)
    assert dict(report.outcomes) == {"shadow_accepted": 2, "rule_rejected": 1}
    # Polls stop while nothing is pending (10:15 to 10:20, after 10:45); the
    # run still waits for refreshes and completes at the cutoff sweep.
    assert report.polls == 12
    assert clock["now"] >= CUTOFF
    run = await RunRepository().get(run_id=report.run_id, owner_id="demo")
    assert run is not None and run.status == "completed"
    assert run.closed_at is not None and run.closed_at >= CUTOFF
    assert await _stored_ranks(run) == {"AAPL": 1, "PENY": 2, "NVDA": 3}
    assert len(await list_decisions(owner_id="demo", run_id=report.run_id)) == 3


async def test_restarted_session_resumes_every_candidate_added_before(tmp_path, monkeypatch):
    strategy = await _world(tmp_path, monkeypatch)
    clock = {"now": OPEN - timedelta(minutes=20)}
    schedule = [
        (OPEN - timedelta(hours=1), OPENING_HOT),
        (et(10, 20), NVDA_HOT),
        (et(10, 30), (("AMD", 102),)),
    ]
    first_market = FakeMarket(lambda: clock["now"], BREAKOUTS)
    first_provider = Provider()
    hot_list = FakeHotList(lambda: clock["now"], schedule)

    with pytest.raises(Crash):
        await _run_day(
            _session_runner(
                clock, first_market, first_provider, crash_at=et(10, 30), holder="daemon-1"
            ),
            strategy,
            hot_list,
        )
    assert first_provider.symbols == ["AAPL"]

    # The supervisor restarts the child a minute later, in a new process with
    # no contracts loaded; the hot list is now only AMD.
    clock["now"] = et(10, 31)
    second_market = FakeMarket(lambda: clock["now"], BREAKOUTS)
    second_provider = Provider()
    report = await _run_day(
        _session_runner(clock, second_market, second_provider, holder="daemon-2"),
        strategy,
        hot_list,
    )

    assert report.candidates == 4
    assert dict(report.outcomes) == {"shadow_accepted": 2, "rule_rejected": 2}
    # AAPL is not reviewed again; NVDA, no longer hot, is still evaluated.
    assert second_provider.symbols == ["NVDA"]
    assert second_market.quotes == [103] and 103 in second_market.loaded
    # Only NVDA lacks seeded liquidity after the restart (PENY and AAPL are decided).
    assert second_market.liquidity_requests == [103]
    run = await RunRepository().get(run_id=report.run_id, owner_id="demo")
    assert run is not None and run.status == "completed"
    assert await _stored_ranks(run) == {"AAPL": 1, "PENY": 2, "NVDA": 3, "AMD": 4}
    stored_scanner = await ScannerRunRepository().get(f"{run.id}-hotlist")
    assert stored_scanner is not None and stored_scanner.parameters["snapshot_id"] == "snap-1"
    assert len(await list_decisions(owner_id="demo", run_id=report.run_id)) == 4


async def test_candidate_refresh_needs_a_candidate_source_and_a_positive_interval():
    async def refresh(_run, _now):
        return (), {}

    runner = ShadowSessionRunner(
        market=FakeMarket(lambda: OPEN, {}),
        read_account=_account_reader({"now": OPEN}),
        runner=RepositoryBackedShadowRunner(
            pipeline=ShadowPipeline(proposal_provider=Provider()), clock=lambda: OPEN
        ),
        clock=lambda: OPEN,
        poll_schedule=SCHEDULE,
    )
    record = StrategyRecord.model_construct(strategy_id="s" * 32, owner_id="demo", version=1)
    common = dict(
        session=market_session(SESSION),
        strategy=record,
        policy=RiskPolicy(),
        source_sha="abcdef1",
        policy_fingerprints={},
        candidate_refresh=refresh,
    )
    with pytest.raises(ValueError, match="candidate_source"):
        await runner.run(**common)

    async def source(_run):
        raise AssertionError("refused before any selection")

    with pytest.raises(ValueError, match="refresh_interval"):
        await runner.run(**common, candidate_source=source, refresh_interval=timedelta(0))
