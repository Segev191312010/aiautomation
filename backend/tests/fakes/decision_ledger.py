"""Seed real, risk-approved decisions into a test ledger.

The execution engine reads accepted decisions straight from the decision
ledger and its outbox has a foreign key to them, so tests write the same rows
the shadow pipeline writes: a strategy version, a run for the session, an
admitted candidate and a ``shadow_accepted`` decision carrying the risk
engine's trade intent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256


SETUP = "a" * 64


async def _strategy(name: str, owner: str) -> str:
    from trading.contracts import FixedUniverse, RuleCondition, StrategySpec, StrategyState
    from trading.storage.repository import create_strategy, transition_strategy

    spec = StrategySpec(
        name=name,
        universe=FixedUniverse(symbols=("AAPL",)),
        conditions=(RuleCondition(feature="rvol", operator="gte", value="1.5"),),
        rationale=f"{name} execution-engine fixture.",
    )
    record = await create_strategy(spec=spec, owner_id=owner, actor=owner)
    for revision, target in enumerate((StrategyState.BACKTESTING, StrategyState.SHADOW)):
        record = await transition_strategy(
            strategy_id=record.strategy_id, version=1, owner_id=owner, expected_revision=revision,
            target=target, actor=owner, reason="fixture",
        )
    return record.strategy_id


async def _run(run_id: str, strategy_id: str, owner: str, session_date: date) -> None:
    from trading.contracts import EngineMode, RiskPolicy, Run, ScannerRun, fingerprint_payload
    from trading.data import RunRepository, ScannerRunRepository

    await RunRepository().save(Run(
        id=run_id, owner_id=owner, source_sha="abcdef1", strategy_id=strategy_id, strategy_version=1,
        policy_fingerprints={"risk": RiskPolicy().fingerprint()}, session_date=session_date,
        mode=EngineMode.SHADOW,
    ))
    parameters = {"scan_code": "HOT_BY_VOLUME", "limit": 50}
    await ScannerRunRepository().save(ScannerRun(
        id=f"{run_id}-scan", run_id=run_id, parameters=parameters,
        parameters_fingerprint=fingerprint_payload(parameters), status="completed",
        started_at=datetime.combine(session_date, datetime.min.time(), tzinfo=timezone.utc),
    ))


@dataclass
class DecisionSeeder:
    """Accepted decisions for one session; one run per strategy name."""

    session_date: date
    owner: str = "demo"
    _runs: dict[str, tuple[str, str]] = field(default_factory=dict)
    _ranks: dict[str, int] = field(default_factory=dict)
    _contracts: dict[str, set[int]] = field(default_factory=dict)

    async def run_for(self, strategy_name: str, *, run_label: str | None = None) -> tuple[str, str]:
        if strategy_name not in self._runs:
            strategy_id = await _strategy(strategy_name, self.owner)
            run_id = f"run-{(run_label or strategy_name).lower()}-{self.session_date.isoformat()}"
            await _run(run_id, strategy_id, self.owner, self.session_date)
            self._runs[strategy_name] = (strategy_id, run_id)
        return self._runs[strategy_name]

    async def _run_with_room(self, strategy_name: str, con_id: int) -> tuple[str, str]:
        """A run holds one candidate per contract: a repeated contract opens a new run of the session."""
        strategy_id, run_id = await self.run_for(strategy_name)
        if con_id in self._contracts.setdefault(run_id, set()):
            run_id = f"{run_id}-r{len(self._contracts) + 1}"
            await _run(run_id, strategy_id, self.owner, self.session_date)
            self._runs[strategy_name] = (strategy_id, run_id)
            self._contracts[run_id] = set()
        self._contracts[run_id].add(con_id)
        return strategy_id, run_id

    async def accept(
        self,
        symbol: str,
        *,
        created: datetime,
        strategy: str = "MOMO_HOD_V2",
        con_id: int,
        primary_exchange: str = "NASDAQ",
        quantity: int = 100,
        entry: str = "50.00",
        stop: str = "49.50",
        target: str = "51.00",
        ttl: timedelta = timedelta(seconds=15),
        setup_fingerprint: str | None = None,
    ) -> str:
        from trading.contracts import (
            AIReview, Candidate, Decision, DecisionOutcome, RiskApproval, TradeIntent,
        )
        from trading.data import CandidateRepository
        from trading.storage.repository import record_decision

        strategy_id, run_id = await self._run_with_room(strategy, con_id)
        rank = self._ranks.get(run_id, 0) + 1
        self._ranks[run_id] = rank
        setup = setup_fingerprint or sha256(f"{run_id}:{symbol}:{rank}".encode()).hexdigest()
        candidate = Candidate(
            id=f"{run_id}-{rank}", scanner_run_id=f"{run_id}-scan", con_id=con_id, symbol=symbol,
            primary_exchange=primary_exchange, rank=rank, admission_status="admitted",
        )
        await CandidateRepository().save(candidate)
        review = AIReview(
            setup_fingerprint=setup, decision="PROPOSE", confidence="0.7", rationale="Fixture breakout.",
            model_id="fixture-model", prompt_version="fixture-prompt", response_hash="c" * 64,
            latency_ms=5, cost_usd="0.01",
        )
        utc = created.astimezone(timezone.utc)
        expires = utc + ttl
        intent = TradeIntent(
            execution_key=f"exec-{run_id}-{rank}", setup_fingerprint=setup, approval_token="t" * 32,
            expires_at=expires, strategy_id=strategy_id, strategy_version=1, symbol=symbol,
            quantity=quantity, entry_limit=Decimal(entry), stop_price=Decimal(stop),
            target_price=Decimal(target),
        )
        decision = Decision(
            id=f"decision-{run_id}-{rank}", run_id=run_id, candidate_id=candidate.id,
            strategy_id=strategy_id, strategy_version=1,
            decision_key=sha256(f"{run_id}:{symbol}:{rank}".encode()).hexdigest(),
            outcome=DecisionOutcome.SHADOW_ACCEPTED, setup_fingerprint=setup, ai_review=review,
            risk=RiskApproval(
                approved=True, intent=intent, setup_fingerprint=setup,
                ai_review_fingerprint=review.fingerprint(), policy_fingerprint="d" * 64,
                portfolio_fingerprint="e" * 64, expires_at=expires,
            ),
            created_at=utc,
        )
        await record_decision(decision=decision, owner_id=self.owner)
        return decision.id


__all__ = ["DecisionSeeder", "SETUP"]
