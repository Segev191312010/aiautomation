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


NOW = datetime(2026, 8, 27, 16, 0, tzinfo=timezone.utc)
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
