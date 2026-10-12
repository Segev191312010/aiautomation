from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.contracts import (
    AIReview,
    Candidate,
    CompletedBar,
    EngineMode,
    FixedUniverse,
    PortfolioSnapshot,
    RiskPolicy,
    RuleCondition,
    Run,
    ScannerRun,
    StrategySpec,
    StrategyState,
    QuoteSnapshot,
)
from trading.data import RunRepository, ScannerRunRepository
from trading.orchestration import CandidateInput, RepositoryBackedShadowRunner, ShadowPipeline
from trading.storage.repository import create_strategy, list_decisions, transition_strategy
from trading.contracts import fingerprint_payload


# 11:30 New York: inside the entry window (cutoff is 12:00).
NOW = datetime(2026, 8, 27, 15, 30, tzinfo=timezone.utc)
HASH = "b" * 64


def _bars(con_id: int) -> tuple[CompletedBar, ...]:
    start = datetime(2026, 8, 27, 13, 30, tzinfo=timezone.utc)
    bars: list[CompletedBar] = []
    for index in range(18):
        high = Decimal("102") if index in {3, 4, 5} else Decimal("101")
        close = Decimal("101") if index in {3, 4, 5} else Decimal("100.5")
        if index >= 14:
            high = Decimal("104")
            close = Decimal("103")
        bars.append(
            CompletedBar(
                con_id=con_id,
                interval_start_utc=start + timedelta(minutes=index * 5),
                interval_end_utc=start + timedelta(minutes=(index + 1) * 5),
                open="100",
                high=high,
                low="99",
                close=close,
                volume="10000",
                rth=True,
                source="ibkr",
                data_mode="historical",
            )
        )
    return tuple(bars)


class _Provider:
    def __init__(self) -> None:
        self.calls = 0

    async def propose_trade(self, *, setup):
        self.calls += 1
        return AIReview(
            setup_fingerprint=setup.fingerprint(),
            decision="PROPOSE",
            confidence="0.8",
            rationale="Persistence fixture review.",
            model_id="fixture-model",
            prompt_version="fixture-prompt",
            response_hash=HASH,
            latency_ms=1,
            cost_usd="0",
        )


@pytest.mark.anyio
async def test_repository_backed_shadow_run_materializes_and_retries_idempotently(
    tmp_path, monkeypatch
):
    from config import cfg
    from db.core import init_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "shadow.db"))
    await init_db()
    spec = StrategySpec(
        name="Persisted shadow strategy",
        universe=FixedUniverse(symbols=("AAPL",)),
        conditions=(RuleCondition(feature="rvol", operator="gte", value="1.5"),),
        rationale="Repository-backed pipeline fixture.",
    )
    strategy = await create_strategy(spec=spec, owner_id="owner-a", actor="owner-a")
    strategy = await transition_strategy(
        strategy_id=strategy.strategy_id,
        version=1,
        owner_id="owner-a",
        expected_revision=0,
        target=StrategyState.BACKTESTING,
        actor="owner-a",
        reason="test",
    )
    strategy = await transition_strategy(
        strategy_id=strategy.strategy_id,
        version=1,
        owner_id="owner-a",
        expected_revision=1,
        target=StrategyState.SHADOW,
        actor="owner-a",
        reason="test",
    )
    run = Run(
        id="run-persisted",
        owner_id="owner-a",
        source_sha="abcdef1",
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        policy_fingerprints={"risk": RiskPolicy().fingerprint()},
        session_date=date(2026, 8, 27),
        mode=EngineMode.SHADOW,
    )
    scanner = ScannerRun(
        id="scanner-persisted",
        run_id=run.id,
        parameters={"scan_code": "HOT_BY_VOLUME", "limit": 50},
        parameters_fingerprint=fingerprint_payload(
            {"scan_code": "HOT_BY_VOLUME", "limit": 50}
        ),
        status="completed",
        started_at=NOW - timedelta(minutes=15),
    )
    candidate = Candidate(
        id="candidate-persisted",
        scanner_run_id=scanner.id,
        con_id=101,
        symbol="AAPL",
        primary_exchange="NASDAQ",
        rank=1,
        admission_status="admitted",
    )
    item = CandidateInput(
        candidate=candidate,
        bars=_bars(candidate.con_id),
        quote=QuoteSnapshot(
            con_id=candidate.con_id,
            bid="102.99",
            ask="103",
            observed_at=NOW,
            min_tick="0.01",
        ),
        reference_price="103",
        average_dollar_volume="25000000",
    )
    provider = _Provider()
    runner = RepositoryBackedShadowRunner(
        pipeline=ShadowPipeline(proposal_provider=provider),
        clock=lambda: NOW,
    )

    first = await runner.execute(
        run=run,
        scanner_run=scanner,
        strategy=strategy,
        candidates=[item],
        portfolio=PortfolioSnapshot(observed_at=NOW, net_liquidation="100000"),
        policy=RiskPolicy(),
        holder_id="worker-a",
        now=NOW,
    )
    second = await runner.execute(
        run=run,
        scanner_run=scanner,
        strategy=strategy,
        candidates=[item],
        portfolio=PortfolioSnapshot(observed_at=NOW, net_liquidation="100000"),
        policy=RiskPolicy(),
        holder_id="worker-a",
        now=NOW,
    )

    assert first[0].decision_key == second[0].decision_key
    assert first[0].outcome.value == "shadow_accepted"
    assert provider.calls == 1
    assert (await RunRepository().get(run_id=run.id, owner_id="owner-a")).status == "completed"
    assert len(await list_decisions(owner_id="owner-a", run_id=run.id)) == 1
    assert await ScannerRunRepository().get(scanner.id) == scanner


async def _shadow_world(tmp_path, monkeypatch, *, run_id: str):
    from config import cfg
    from db.core import init_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "session.db"))
    await init_db()
    spec = StrategySpec(
        name="Session shadow strategy",
        universe=FixedUniverse(symbols=("AAPL", "MSFT", "NVDA")),
        conditions=(RuleCondition(feature="rvol", operator="gte", value="1.5"),),
        rationale="Session fixture.",
    )
    strategy = await create_strategy(spec=spec, owner_id="owner-a", actor="owner-a")
    for revision, target in enumerate((StrategyState.BACKTESTING, StrategyState.SHADOW)):
        strategy = await transition_strategy(
            strategy_id=strategy.strategy_id,
            version=1,
            owner_id="owner-a",
            expected_revision=revision,
            target=target,
            actor="owner-a",
            reason="test",
        )
    run = Run(
        id=run_id,
        owner_id="owner-a",
        source_sha="abcdef1",
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        policy_fingerprints={"risk": RiskPolicy().fingerprint()},
        session_date=date(2026, 8, 27),
        mode=EngineMode.SHADOW,
    )
    parameters = {"scan_code": "HOT_BY_VOLUME", "limit": 50}
    scanner = ScannerRun(
        id=f"{run_id}-scanner",
        run_id=run.id,
        parameters=parameters,
        parameters_fingerprint=fingerprint_payload(parameters),
        status="completed",
        started_at=NOW - timedelta(minutes=60),
    )
    candidates = tuple(
        Candidate(
            id=f"{run_id}-{symbol.lower()}",
            scanner_run_id=scanner.id,
            con_id=con_id,
            symbol=symbol,
            primary_exchange="NASDAQ",
            rank=rank,
            admission_status="admitted",
        )
        for rank, (symbol, con_id) in enumerate((("AAPL", 101), ("MSFT", 102), ("NVDA", 103)), 1)
    )
    return strategy, run, scanner, candidates


def _flat_bars(con_id: int) -> tuple[CompletedBar, ...]:
    return tuple(
        bar.model_copy(update={"high": Decimal("101"), "close": Decimal("100.5")})
        if index >= 14
        else bar
        for index, bar in enumerate(_bars(con_id))
    )


def _item(candidate, bars, quote=None) -> CandidateInput:
    return CandidateInput(
        candidate=candidate,
        bars=bars,
        quote=quote,
        reference_price="103",
        average_dollar_volume="25000000",
    )


@pytest.mark.anyio
async def test_leased_session_polls_until_breakout_then_sweeps_at_cutoff(tmp_path, monkeypatch):
    strategy, run, scanner, (aapl, msft, nvda) = await _shadow_world(
        tmp_path, monkeypatch, run_id="run-session"
    )
    provider = _Provider()
    clock = {"now": NOW}
    # The fake clock jumps between polls without the background renewal, so
    # the lease must outlive the whole simulated session.
    runner = RepositoryBackedShadowRunner(
        pipeline=ShadowPipeline(proposal_provider=provider),
        clock=lambda: clock["now"],
        lease_ttl=timedelta(hours=4),
        lease_renew_interval=timedelta(hours=1),
    )
    portfolio = PortfolioSnapshot(observed_at=NOW, net_liquidation="100000")
    first_poll = datetime(2026, 8, 27, 14, 20, 5, tzinfo=timezone.utc)
    breakout_poll = datetime(2026, 8, 27, 14, 45, 5, tzinfo=timezone.utc)
    cutoff = datetime(2026, 8, 27, 16, 0, tzinfo=timezone.utc)
    clock["now"] = first_poll

    async with runner.open_session(
        run=run,
        scanner_run=scanner,
        strategy=strategy,
        candidates=(aapl, msft, nvda),
        policy=RiskPolicy(),
        holder_id="daemon-1",
    ) as session:
        early = await session.assess(_item(aapl, _bars(101)[:10]), portfolio=portfolio, now=first_poll)
        assert early.decision is None and early.pending_reason == "NO_SIGNAL_YET"

        clock["now"] = breakout_poll
        unquoted = await session.assess(
            _item(aapl, _bars(101)[:15]), portfolio=portfolio, now=breakout_poll
        )
        assert unquoted.needs_quote is True
        quote = QuoteSnapshot(
            con_id=101, bid="102.99", ask="103", observed_at=breakout_poll, min_tick="0.01"
        )
        accepted = await session.assess(
            _item(aapl, _bars(101)[:15], quote), portfolio=portfolio, now=breakout_poll
        )
        assert accepted.decision is not None
        assert accepted.decision.outcome.value == "shadow_accepted"
        assert {candidate.id for candidate in session.pending()} == {msft.id, nvda.id}

        clock["now"] = cutoff
        final = await session.finalize(
            [_item(msft, _flat_bars(102))], portfolio=portfolio, now=cutoff
        )

    assert final[aapl.id] == accepted.decision
    assert final[msft.id].outcome.value == "rule_rejected"
    assert final[nvda.id].outcome.value == "data_blocked"
    assert provider.calls == 1
    assert (await RunRepository().get(run_id=run.id, owner_id="owner-a")).status == "completed"
    assert len(await list_decisions(owner_id="owner-a", run_id=run.id)) == 3

    # A restarted daemon resumes from durable decisions and re-assesses nothing.
    async with runner.open_session(
        run=run,
        scanner_run=scanner,
        strategy=strategy,
        candidates=(aapl, msft, nvda),
        policy=RiskPolicy(),
        holder_id="daemon-2",
    ) as resumed:
        assert resumed.complete is True
        assert resumed.pending() == ()
        again = await resumed.assess(
            _item(aapl, _bars(101), quote), portfolio=portfolio, now=breakout_poll
        )
        assert again.decision == accepted.decision
    assert provider.calls == 1


@pytest.mark.anyio
async def test_session_rejects_candidates_outside_the_scanner_run(tmp_path, monkeypatch):
    from trading.orchestration import ShadowPersistenceConflictError

    strategy, run, scanner, (aapl, msft, _nvda) = await _shadow_world(
        tmp_path, monkeypatch, run_id="run-foreign"
    )
    runner = RepositoryBackedShadowRunner(
        pipeline=ShadowPipeline(proposal_provider=_Provider()), clock=lambda: NOW
    )
    async with runner.open_session(
        run=run,
        scanner_run=scanner,
        strategy=strategy,
        candidates=(aapl,),
        policy=RiskPolicy(),
        holder_id="daemon-1",
    ) as session:
        with pytest.raises(ShadowPersistenceConflictError, match="not part of"):
            await session.assess(
                _item(msft, _bars(102)),
                portfolio=PortfolioSnapshot(observed_at=NOW, net_liquidation="100000"),
                now=NOW,
            )
