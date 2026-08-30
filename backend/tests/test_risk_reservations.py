from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.contracts import (
    AIReview,
    Candidate,
    EngineMode,
    FixedUniverse,
    PortfolioSnapshot,
    RiskPolicy,
    RuleCondition,
    RuleSetup,
    Run,
    ScannerRun,
    StrategySpec,
)
from trading.data import CandidateRepository, RunRepository, ScannerRunRepository
from trading.risk.engine import size_and_evaluate_trade
from trading.risk.reservations import (
    DuplicateReservationError,
    ReservationError,
    ReservationExpiredError,
    RiskReservationRepository,
)
from trading.contracts import fingerprint_payload


HASH = "a" * 64


def _spec() -> StrategySpec:
    return StrategySpec(
        name="Reservation test strategy",
        universe=FixedUniverse(symbols=("AAPL", "MSFT")),
        conditions=(RuleCondition(feature="rvol", operator="gte", value="1.5"),),
        rationale="Atomic reservation fixture.",
    )


def _setup(*, run_id: str, candidate_id: str, strategy_id: str, symbol: str = "AAPL") -> RuleSetup:
    return RuleSetup(
        run_id=run_id,
        candidate_id=candidate_id,
        strategy_id=strategy_id,
        strategy_version=1,
        symbol=symbol,
        opening_range_high="100",
        opening_range_low="98",
        atr="1",
        entry_limit="100",
        stop_price="99",
        target_price="102",
        bar_fingerprints=(HASH,),
        quote_fingerprint=HASH,
        detected_at=datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc),
    )


def _approval(setup: RuleSetup, *, policy: RiskPolicy, portfolio: PortfolioSnapshot, now: datetime):
    review = AIReview(
        setup_fingerprint=setup.fingerprint(),
        decision="PROPOSE",
        confidence="0.8",
        rationale="Reservation test proposal.",
        model_id="test-model",
        prompt_version="test-prompt",
        response_hash=HASH,
        latency_ms=1,
        cost_usd="0",
    )
    return size_and_evaluate_trade(
        policy=policy,
        portfolio=portfolio,
        setup=setup,
        review=review,
        admitted_symbols=frozenset({setup.symbol}),
        now=now,
    )


async def _fixture(tmp_path, monkeypatch, *, second_symbol: bool = False):
    from config import cfg
    from db.core import init_db
    from trading.storage.repository import create_strategy

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "reservations.db"))
    await init_db()
    strategy = await create_strategy(spec=_spec(), owner_id="owner-a", actor="owner-a")
    run = Run(
        owner_id="owner-a",
        source_sha="abcdef1",
        strategy_id=strategy.strategy_id,
        strategy_version=1,
        policy_fingerprints={"risk": HASH},
        session_date=date(2026, 8, 27),
        mode=EngineMode.SHADOW,
    )
    await RunRepository().save(run)
    parameters = {"scan_code": "HOT_BY_VOLUME"}
    scanner_run = ScannerRun(
        run_id=run.id,
        parameters=parameters,
        parameters_fingerprint=fingerprint_payload(parameters),
        status="completed",
        started_at=datetime(2026, 8, 27, 13, 45, tzinfo=timezone.utc),
    )
    await ScannerRunRepository().save(scanner_run)
    first = Candidate(
        scanner_run_id=scanner_run.id,
        con_id=123,
        symbol="AAPL",
        primary_exchange="NASDAQ",
        rank=1,
        admission_status="admitted",
    )
    await CandidateRepository().save(first)
    second = None
    if second_symbol:
        second = Candidate(
            scanner_run_id=scanner_run.id,
            con_id=456,
            symbol="MSFT",
            primary_exchange="NASDAQ",
            rank=2,
            admission_status="admitted",
        )
        await CandidateRepository().save(second)
    return run, first, second, strategy.strategy_id


@pytest.mark.anyio
async def test_reservation_hashes_token_and_enforces_one_time_lifecycle(tmp_path, monkeypatch):
    run, candidate, _, strategy_id = await _fixture(tmp_path, monkeypatch)
    now = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)
    portfolio = PortfolioSnapshot(observed_at=now, net_liquidation="100000")
    policy = RiskPolicy()
    approval = _approval(_setup(run_id=run.id, candidate_id=candidate.id, strategy_id=strategy_id), policy=policy, portfolio=portfolio, now=now)
    repository = RiskReservationRepository()
    reservation = await repository.reserve(
        owner_id="owner-a",
        run_id=run.id,
        candidate_id=candidate.id,
        approval=approval,
        policy=policy,
        portfolio=portfolio,
        now=now,
    )
    assert reservation.state == "active"
    assert approval.intent is not None

    from config import cfg
    import aiosqlite

    async with aiosqlite.connect(cfg.DB_PATH) as database:
        async with database.execute(
            "SELECT approval_token_hash FROM autotrader_risk_reservations WHERE id=?",
            (reservation.id,),
        ) as cursor:
            stored_hash = (await cursor.fetchone())[0]
    assert stored_hash != approval.intent.approval_token
    assert len(stored_hash) == 64

    with pytest.raises(ReservationError, match="invalid"):
        await repository.consume(
            reservation_id=reservation.id,
            owner_id="owner-a",
            approval_token="wrong-token",
            now=now,
        )
    consumed = await repository.consume(
        reservation_id=reservation.id,
        owner_id="owner-a",
        approval_token=approval.intent.approval_token,
        now=now,
    )
    assert consumed.state == "consumed"
    with pytest.raises(ReservationError, match="no longer active"):
        await repository.consume(
            reservation_id=reservation.id,
            owner_id="owner-a",
            approval_token=approval.intent.approval_token,
            now=now,
        )


@pytest.mark.anyio
async def test_reservation_rejects_scope_and_fingerprint_mismatches(tmp_path, monkeypatch):
    run, candidate, _, strategy_id = await _fixture(tmp_path, monkeypatch)
    now = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)
    portfolio = PortfolioSnapshot(observed_at=now, net_liquidation="100000")
    policy = RiskPolicy()
    approval = _approval(_setup(run_id=run.id, candidate_id=candidate.id, strategy_id=strategy_id), policy=policy, portfolio=portfolio, now=now)
    repository = RiskReservationRepository()
    with pytest.raises(ReservationError, match="scope"):
        await repository.reserve(
            owner_id="owner-b",
            run_id=run.id,
            candidate_id=candidate.id,
            approval=approval,
            policy=policy,
            portfolio=portfolio,
            now=now,
        )
    with pytest.raises(ReservationError, match="policy fingerprint"):
        await repository.reserve(
            owner_id="owner-a",
            run_id=run.id,
            candidate_id=candidate.id,
            approval=approval,
            policy=RiskPolicy(min_reward_to_risk="3"),
            portfolio=portfolio,
            now=now,
        )
    with pytest.raises(ReservationExpiredError):
        await repository.reserve(
            owner_id="owner-a",
            run_id=run.id,
            candidate_id=candidate.id,
            approval=approval,
            policy=policy,
            portfolio=portfolio,
            now=now + timedelta(seconds=16),
        )
    reservation = await repository.reserve(
        owner_id="owner-a",
        run_id=run.id,
        candidate_id=candidate.id,
        approval=approval,
        policy=policy,
        portfolio=portfolio,
        now=now,
    )
    with pytest.raises(ReservationExpiredError):
        await repository.release(
            reservation_id=reservation.id,
            owner_id="owner-a",
            now=now + timedelta(seconds=16),
        )
    stored = await repository.get(reservation_id=reservation.id, owner_id="owner-a")
    assert stored is not None and stored.state == "expired"


@pytest.mark.anyio
async def test_concurrent_reservations_are_serialized_and_exposure_limited(tmp_path, monkeypatch):
    run, first, second, strategy_id = await _fixture(tmp_path, monkeypatch, second_symbol=True)
    assert second is not None
    now = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)
    portfolio = PortfolioSnapshot(observed_at=now, net_liquidation="100000")
    policy = RiskPolicy(max_total_notional_pct="15")
    first_approval = _approval(_setup(run_id=run.id, candidate_id=first.id, strategy_id=strategy_id), policy=policy, portfolio=portfolio, now=now)
    second_approval = _approval(_setup(run_id=run.id, candidate_id=second.id, strategy_id=strategy_id, symbol="MSFT"), policy=policy, portfolio=portfolio, now=now)
    repository = RiskReservationRepository()

    results = await asyncio.gather(
        repository.reserve(
            owner_id="owner-a", run_id=run.id, candidate_id=first.id,
            approval=first_approval, policy=policy, portfolio=portfolio, now=now,
        ),
        repository.reserve(
            owner_id="owner-a", run_id=run.id, candidate_id=second.id,
            approval=second_approval, policy=policy, portfolio=portfolio, now=now,
        ),
        return_exceptions=True,
    )
    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], ReservationError)

    same_symbol_results = await asyncio.gather(
        repository.reserve(
            owner_id="owner-a", run_id=run.id, candidate_id=first.id,
            approval=first_approval, policy=policy, portfolio=portfolio, now=now,
        ),
        repository.reserve(
            owner_id="owner-a", run_id=run.id, candidate_id=first.id,
            approval=first_approval, policy=policy, portfolio=portfolio, now=now,
        ),
        return_exceptions=True,
    )
    assert all(isinstance(result, (DuplicateReservationError, ReservationError)) for result in same_symbol_results)
