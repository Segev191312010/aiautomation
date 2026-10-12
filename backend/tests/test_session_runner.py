"""One simulated trading day through the real leased session and database."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.contracts import (
    AccountSnapshot,
    AIReview,
    CompletedBar,
    QuoteSnapshot,
    RiskPolicy,
    StrategyState,
)
from trading.market.ibkr_boundary import (
    IBKRScannerResult,
    LiquidityProfile,
    qualify_scanner_results,
)
from trading.orchestration import RepositoryBackedShadowRunner, ShadowPipeline
from trading.orchestration.session_runner import (
    ORB_STRATEGY_NAME,
    SessionAborted,
    ShadowSessionRunner,
    ensure_orb_strategy,
)
from trading.scheduler.sessions import market_session
from trading.storage.repository import list_decisions

SESSION = date(2026, 8, 27)
OPEN = datetime(2026, 8, 27, 13, 30, tzinfo=timezone.utc)
HASH = "c" * 64
BREAKOUT_START = OPEN + timedelta(minutes=40)  # 10:10-10:15 New York


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


class FakeMarket:
    def __init__(self, clock):
        self.clock = clock
        self.quotes: list[int] = []

    async def scanner_snapshot(self, *, scanner_run_id):
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
            for rank, (symbol, con_id) in enumerate((("AAPL", 101), ("MSFT", 102), ("NVDA", 103)), 1)
        )
        return qualify_scanner_results(results, scanner_run_id=scanner_run_id)

    async def completed_bars(self, *, con_id, now):
        if con_id == 103:
            raise RuntimeError("pacing violation")
        prior_open = OPEN - timedelta(days=1)
        bars = [
            _bar(con_id, prior_open + timedelta(minutes=5 * index), high="101", close="100")
            for index in range(20)
        ]
        start = OPEN
        while start + timedelta(minutes=5) <= now:
            breakout = con_id == 101 and start >= BREAKOUT_START
            bars.append(
                _bar(
                    con_id,
                    start,
                    high="103.2" if breakout else ("101" if start < OPEN + timedelta(minutes=15) else "100.8"),
                    close="103" if breakout else "100.5",
                )
            )
            start += timedelta(minutes=5)
        return tuple(bars)

    async def quote(self, *, con_id):
        self.quotes.append(con_id)
        return QuoteSnapshot(
            con_id=con_id, bid="102.99", ask="103", observed_at=self.clock(), min_tick="0.01"
        )

    async def liquidity(self, *, con_id, session_date):
        return LiquidityProfile(
            con_id=con_id,
            reference_price=Decimal("103"),
            average_dollar_volume=Decimal("25000000"),
            sessions_used=20,
            last_session_date=session_date - timedelta(days=1),
        )


class Provider:
    def __init__(self):
        self.calls = 0

    async def propose_trade(self, *, setup):
        self.calls += 1
        return AIReview(
            setup_fingerprint=setup.fingerprint(),
            decision="PROPOSE",
            confidence="0.7",
            rationale="Session fixture review.",
            model_id="fixture",
            prompt_version="fixture",
            response_hash=HASH,
            latency_ms=1,
            cost_usd="0.02",
        )


async def _account():
    return AccountSnapshot(
        paper_account_id="DU1",
        client_id=17,
        observed_at=datetime.now(timezone.utc),
        net_liquidation=Decimal("100000"),
        available_funds=Decimal("100000"),
        realized_day_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
    )


async def _world(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "day.db"))
    await init_db()
    clock = {"now": OPEN - timedelta(minutes=20)}

    async def sleep(seconds):
        clock["now"] += timedelta(seconds=seconds)

    return clock, sleep


@pytest.mark.anyio
async def test_simulated_day_decides_every_candidate(tmp_path, monkeypatch):
    clock, sleep = await _world(tmp_path, monkeypatch)
    strategy = await ensure_orb_strategy(owner_id="demo")
    assert strategy.spec.name == ORB_STRATEGY_NAME and strategy.state == StrategyState.SHADOW
    assert (await ensure_orb_strategy(owner_id="demo")).strategy_id == strategy.strategy_id

    provider = Provider()
    market = FakeMarket(lambda: clock["now"])
    runner = ShadowSessionRunner(
        market=market,
        read_account=_account,
        runner=RepositoryBackedShadowRunner(
            pipeline=ShadowPipeline(proposal_provider=provider),
            clock=lambda: clock["now"],
            lease_ttl=timedelta(hours=6),
            lease_renew_interval=timedelta(hours=1),
        ),
        clock=lambda: clock["now"],
        sleep=sleep,
    )

    report = await runner.run(
        session=market_session(SESSION),
        strategy=strategy,
        policy=RiskPolicy(),
        source_sha="abcdef1",
        policy_fingerprints={"risk": RiskPolicy().fingerprint()},
    )

    assert report.candidates == 3
    assert dict(report.outcomes) == {
        "shadow_accepted": 1,
        "rule_rejected": 1,
        "data_blocked": 1,
    }
    # Quotes are requested only at the breakout, never for quiet candidates.
    assert market.quotes == [101]
    assert provider.calls == 1
    # Polls run 09:50:05 through 11:55:05 while two candidates stay pending.
    assert report.polls == 26
    assert clock["now"] >= datetime(2026, 8, 27, 16, 0, tzinfo=timezone.utc)
    stored = await list_decisions(owner_id="demo", run_id=report.run_id)
    assert len(stored) == 3


@pytest.mark.anyio
async def test_unanswered_scanner_aborts_the_session(tmp_path, monkeypatch):
    clock, sleep = await _world(tmp_path, monkeypatch)
    strategy = await ensure_orb_strategy(owner_id="demo")

    class DeadScanner(FakeMarket):
        async def scanner_snapshot(self, *, scanner_run_id):
            raise RuntimeError("scanner unavailable")

    reconnects = []

    async def reconnect():
        reconnects.append(clock["now"])

    runner = ShadowSessionRunner(
        market=DeadScanner(lambda: clock["now"]),
        read_account=_account,
        runner=RepositoryBackedShadowRunner(
            pipeline=ShadowPipeline(proposal_provider=Provider()), clock=lambda: clock["now"]
        ),
        clock=lambda: clock["now"],
        sleep=sleep,
        reconnect=reconnect,
    )
    with pytest.raises(SessionAborted):
        await runner.run(
            session=market_session(SESSION),
            strategy=strategy,
            policy=RiskPolicy(),
            source_sha="abcdef1",
            policy_fingerprints={},
        )
    assert len(reconnects) == 3
