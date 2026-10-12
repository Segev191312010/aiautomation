"""Drive one real-data ORB session: scan, poll each bar close, sweep at cutoff.

This module composes existing parts only: the IBKR data facade, the leased
shadow session, the AI review provider, and broker account truth for risk.
It never submits orders.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import logging
from typing import Protocol
from uuid import uuid4

from trading.contracts import (
    AccountSnapshot,
    Candidate,
    CompletedBar,
    Decision,
    DecisionOutcome,
    EngineMode,
    PortfolioSnapshot,
    QuoteSnapshot,
    RiskPolicy,
    RuleCondition,
    Run,
    ScannerRun,
    ScannerUniverse,
    StrategyRecord,
    StrategySpec,
    StrategyState,
    fingerprint_payload,
)
from trading.market.ibkr_boundary import (
    DEFAULT_SCANNER_REQUEST,
    LiquidityProfile,
    ScannerSnapshot,
)
from trading.orchestration.persistence import RepositoryBackedShadowRunner, ShadowRunSession
from trading.orchestration.pipeline import CandidateInput
from trading.risk.portfolio import portfolio_from_account
from trading.rules.orb import qualify_candidate
from trading.scheduler.sessions import MarketSession

logger = logging.getLogger("autotrader.shadow.session")

ORB_STRATEGY_NAME = "ORB_V1"
MOMO_STRATEGY_NAME = "MOMO_HOD_V1"
BAR_INTERVAL = timedelta(minutes=5)
# Poll a few seconds after each bar closes so IBKR has finalized that bar.
POLL_DELAY_AFTER_BAR_CLOSE = timedelta(seconds=5)
SCANNER_ATTEMPTS = 3
SCANNER_RETRY_DELAY = timedelta(seconds=20)


class SessionMarketData(Protocol):
    async def scanner_snapshot(self, *, scanner_run_id: str) -> ScannerSnapshot: ...

    async def completed_bars(self, *, con_id: int, now: datetime) -> tuple[CompletedBar, ...]: ...

    async def quote(self, *, con_id: int) -> QuoteSnapshot: ...

    async def liquidity(self, *, con_id: int, session_date: date) -> LiquidityProfile: ...


AccountReader = Callable[[], Awaitable[AccountSnapshot]]
CandidateSource = Callable[
    [Run], Awaitable[tuple[ScannerRun, tuple[Candidate, ...], dict[int, LiquidityProfile]]]
]
Sleeper = Callable[[float], Awaitable[None]]
Reconnector = Callable[[], Awaitable[None]]


class SessionAborted(RuntimeError):
    """The session could not start (e.g. the scanner never answered)."""


@dataclass
class SessionReport:
    run_id: str
    session_date: date
    candidates: int = 0
    outcomes: Counter[str] = field(default_factory=Counter)
    polls: int = 0
    data_errors: int = 0

    def summary(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "session_date": self.session_date.isoformat(),
            "candidates": self.candidates,
            "outcomes": dict(self.outcomes),
            "polls": self.polls,
            "data_errors": self.data_errors,
        }


def orb_strategy_spec() -> StrategySpec:
    """ORB_V1's catalogue entry; the executable rules are ``ORBConfig``."""
    scanner_policy = {
        "instrument": DEFAULT_SCANNER_REQUEST.instrument,
        "location": DEFAULT_SCANNER_REQUEST.location,
        "scan_code": DEFAULT_SCANNER_REQUEST.scan_code,
        "stock_type_filter": DEFAULT_SCANNER_REQUEST.stock_type_filter,
        "number_of_rows": DEFAULT_SCANNER_REQUEST.number_of_rows,
    }
    return StrategySpec(
        name=ORB_STRATEGY_NAME,
        universe=ScannerUniverse(
            scanner_policy_id="ibkr-hot-by-volume",
            scanner_policy_version=1,
            scanner_policy_hash=fingerprint_payload(scanner_policy),
        ),
        conditions=(RuleCondition(feature="close", operator="gte", value=Decimal("10")),),
        rationale=(
            "Long-only 09:30-09:45 opening-range breakout on the IBKR HOT_BY_VOLUME "
            "scan; stop one ATR(14) below entry, 2R target, entries until 12:00 ET. "
            "Authoritative rules: trading/rules/orb.py ORBConfig."
        ),
    )


def momo_strategy_spec() -> StrategySpec:
    """MOMO_HOD_V1's catalogue entry; the executable rules are ``MomoConfig``."""
    return StrategySpec(
        name=MOMO_STRATEGY_NAME,
        universe=ScannerUniverse(
            scanner_policy_id="owner-watchlist-hotlist",
            scanner_policy_version=1,
            scanner_policy_hash=fingerprint_payload({"source": "screener_hotlist", "top": 35}),
        ),
        conditions=(RuleCondition(feature="close", operator="gte", value=Decimal("10")),),
        rationale=(
            "Long-only new high of day on volume: a 5-minute close above the day's "
            "prior high, above VWAP, with elevated bar and day volume, 10:00-15:00 ET; "
            "stop one ATR(14) below entry, 2R target. Authoritative rules: "
            "trading/rules/momo.py MomoConfig."
        ),
    )


async def ensure_strategy(*, owner_id: str, spec: StrategySpec) -> StrategyRecord:
    """Return the owner's strategy named ``spec.name``, creating it in SHADOW."""
    from trading.storage.repository import (
        create_strategy,
        list_strategies,
        transition_strategy,
    )

    existing = [
        record
        for record in await list_strategies(owner_id=owner_id)
        if record.spec.name == spec.name
        and record.state in {StrategyState.SHADOW, StrategyState.PAPER}
    ]
    if existing:
        return max(existing, key=lambda record: record.version)
    record = await create_strategy(spec=spec, owner_id=owner_id, actor=owner_id)
    for target in (StrategyState.BACKTESTING, StrategyState.SHADOW):
        record = await transition_strategy(
            strategy_id=record.strategy_id,
            version=record.version,
            owner_id=owner_id,
            expected_revision=record.revision,
            target=target,
            actor=owner_id,
            reason=f"{spec.name} bootstrap for real-data sessions",
        )
    return record


async def ensure_orb_strategy(*, owner_id: str) -> StrategyRecord:
    """Return the owner's ORB_V1 strategy, creating and promoting it to SHADOW."""
    return await ensure_strategy(owner_id=owner_id, spec=orb_strategy_spec())

class ShadowSessionRunner:
    """One trading day of real-data shadow decisions."""

    def __init__(
        self,
        *,
        market: SessionMarketData,
        read_account: AccountReader,
        runner: RepositoryBackedShadowRunner,
        clock: Callable[[], datetime],
        sleep: Sleeper = asyncio.sleep,
        reconnect: Reconnector | None = None,
        holder_id: str | None = None,
    ) -> None:
        self._market = market
        self._read_account = read_account
        self._runner = runner
        self._clock = clock
        self._sleep = sleep
        self._reconnect = reconnect
        self._holder_id = holder_id or f"session-runner:{uuid4()}"

    async def run(
        self,
        *,
        session: MarketSession,
        strategy: StrategyRecord,
        policy: RiskPolicy,
        source_sha: str,
        policy_fingerprints: dict[str, str],
        run_id: str | None = None,
        candidate_source: CandidateSource | None = None,
        window_start: datetime | None = None,
    ) -> SessionReport:
        """Run one strategy session.

        Without ``candidate_source`` candidates come from the 09:45 IBKR scan;
        with it (e.g. the screener hot list) they are taken when the strategy's
        window opens, and liquidity is seeded from the source.
        """
        session_date = session.session_date
        run = Run(
            id=run_id or f"shadow-{strategy.strategy_id[:8]}-{session_date.isoformat()}",
            owner_id=strategy.owner_id,
            source_sha=source_sha,
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            policy_fingerprints=policy_fingerprints,
            session_date=session_date,
            mode=EngineMode.SHADOW,
        )
        report = SessionReport(run_id=run.id, session_date=session_date)
        liquidity: dict[int, LiquidityProfile] = {}
        if candidate_source is None:
            await self._sleep_until(session.scanner_at)
            scanner_run, candidates = await self._scan(run)
        else:
            if window_start is not None:
                await self._sleep_until(window_start - BAR_INTERVAL)
            scanner_run, candidates, liquidity = await candidate_source(run)
        report.candidates = len(candidates)
        logger.info("%s admitted %d candidates", scanner_run.parameters.get("source", "scanner"),
                    len(candidates))

        async with self._runner.open_session(
            run=run,
            scanner_run=scanner_run,
            strategy=strategy,
            candidates=candidates,
            policy=policy,
            holder_id=self._holder_id,
            now=self._now(),
        ) as shadow:
            latest: dict[str, CandidateInput] = {}
            cutoff = shadow.cutoff
            poll_at = self._first_poll(session, window_start)
            while poll_at < cutoff and shadow.pending():
                await self._sleep_until(poll_at)
                report.polls += 1
                await self._poll(
                    shadow=shadow,
                    session_date=session_date,
                    latest=latest,
                    liquidity=liquidity,
                    report=report,
                )
                poll_at = poll_at + BAR_INTERVAL
            if shadow.pending():
                await self._sleep_until(cutoff)
                portfolio = await self._portfolio(shadow) or self._fallback_portfolio()
                await shadow.finalize(list(latest.values()), portfolio=portfolio, now=self._now())
            report.outcomes.update(
                decision.outcome.value for decision in shadow.decisions.values()
            )
        logger.info("session complete: %s", report.summary())
        return report

    async def _scan(self, run: Run) -> tuple[ScannerRun, tuple[Candidate, ...]]:
        scanner_run_id = f"{run.id}-scan"
        started = self._now()
        last_error: Exception | None = None
        for attempt in range(1, SCANNER_ATTEMPTS + 1):
            try:
                snapshot = await self._market.scanner_snapshot(scanner_run_id=scanner_run_id)
                break
            except Exception as exc:  # noqa: BLE001 - logged, retried, then aborted
                last_error = exc
                logger.warning("scanner attempt %d failed: %s", attempt, type(exc).__name__)
                await self._recover_connection()
                if attempt < SCANNER_ATTEMPTS:
                    await self._sleep(SCANNER_RETRY_DELAY.total_seconds())
        else:
            raise SessionAborted("IBKR scanner did not return a usable snapshot") from last_error

        parameters: dict[str, object] = {
            "scan_code": snapshot.request.scan_code,
            "location": snapshot.request.location,
            "rows": snapshot.request.number_of_rows,
            "raw_results": len(snapshot.records),
            "rejections": dict(
                Counter(
                    record.rejection_reason
                    for record in snapshot.records
                    if record.rejection_reason is not None
                )
            ),
        }
        scanner_run = ScannerRun(
            id=scanner_run_id,
            run_id=run.id,
            parameters=parameters,
            parameters_fingerprint=fingerprint_payload(parameters),
            status="completed",
            started_at=started,
            completed_at=self._now(),
        )
        # Every admitted row (at most 50) becomes a candidate with a decision;
        # price and liquidity rejections are cheap and recorded at first poll.
        candidates = tuple(
            sorted(snapshot.admitted_candidates, key=lambda candidate: candidate.rank)
        )
        return scanner_run, candidates

    async def _poll(
        self,
        *,
        shadow: ShadowRunSession,
        session_date: date,
        latest: dict[str, CandidateInput],
        liquidity: dict[int, LiquidityProfile],
        report: SessionReport,
    ) -> None:
        portfolio = await self._portfolio(shadow)
        if portfolio is None:
            report.data_errors += 1
            return
        for candidate in shadow.pending():
            profile = liquidity.get(candidate.con_id)
            if profile is None:
                try:
                    profile = await self._market.liquidity(
                        con_id=candidate.con_id, session_date=session_date
                    )
                except Exception as exc:  # noqa: BLE001 - candidate stays pending
                    report.data_errors += 1
                    logger.warning("liquidity unavailable for %s: %s", candidate.symbol, exc)
                    await self._recover_connection()
                    continue
                liquidity[candidate.con_id] = profile
            if qualify_candidate(
                candidate,
                reference_price=profile.reference_price,
                average_dollar_volume=profile.average_dollar_volume,
            ):
                # Price/liquidity rejection is decided before any bar request.
                await shadow.assess(
                    CandidateInput(
                        candidate=candidate,
                        bars=(),
                        quote=None,
                        reference_price=profile.reference_price,
                        average_dollar_volume=profile.average_dollar_volume,
                    ),
                    portfolio=portfolio,
                    now=self._now(),
                )
                continue
            try:
                bars = await self._market.completed_bars(con_id=candidate.con_id, now=self._now())
            except Exception as exc:  # noqa: BLE001 - pending until the next poll
                report.data_errors += 1
                logger.warning("bars unavailable for %s: %s", candidate.symbol, exc)
                await self._recover_connection()
                bars = ()
            item = CandidateInput(
                candidate=candidate,
                bars=bars,
                quote=None,
                reference_price=profile.reference_price,
                average_dollar_volume=profile.average_dollar_volume,
            )
            latest[candidate.id] = item
            assessment = await shadow.assess(item, portfolio=portfolio, now=self._now())
            if not assessment.needs_quote:
                continue
            try:
                quote = await self._market.quote(con_id=candidate.con_id)
            except Exception as exc:  # noqa: BLE001 - breakout stays pending
                report.data_errors += 1
                logger.warning("quote unavailable for %s: %s", candidate.symbol, exc)
                await self._recover_connection()
                continue
            quoted = CandidateInput(
                candidate=candidate,
                bars=bars,
                quote=quote,
                reference_price=profile.reference_price,
                average_dollar_volume=profile.average_dollar_volume,
            )
            outcome = await shadow.assess(quoted, portfolio=portfolio, now=self._now())
            if outcome.decision is not None:
                logger.info(
                    "%s decided: %s", candidate.symbol, outcome.decision.outcome.value
                )

    async def _portfolio(self, shadow: ShadowRunSession) -> PortfolioSnapshot | None:
        """Broker truth plus this run's accepted shadow entries as pending exposure."""
        try:
            account = await self._read_account()
        except Exception as exc:  # noqa: BLE001 - no sizing without broker truth
            logger.warning("account snapshot unavailable: %s", exc)
            await self._recover_connection()
            return None
        accepted = tuple(
            sorted(
                {
                    _decision_symbol(shadow, decision)
                    for decision in shadow.decisions.values()
                    if decision.outcome == DecisionOutcome.SHADOW_ACCEPTED
                }
            )
        )
        # Shadow opens no broker positions, so none in the account are its own.
        return portfolio_from_account(
            account, owned_con_ids=frozenset(), pending_symbols=accepted
        )

    def _fallback_portfolio(self) -> PortfolioSnapshot:
        # Only used for the cutoff sweep, which never builds a setup or sizes.
        return PortfolioSnapshot(observed_at=self._now(), net_liquidation=Decimal("1"))

    async def _recover_connection(self) -> None:
        if self._reconnect is None:
            return
        try:
            await self._reconnect()
        except Exception as exc:  # noqa: BLE001 - retried on the next request
            logger.warning("TWS reconnect failed: %s", type(exc).__name__)

    def _first_poll(self, session: MarketSession, window_start: datetime | None = None) -> datetime:
        """First bar close at or after the window opens, and not in the past."""
        first = max(session.first_signal_at, window_start or session.first_signal_at)
        now = self._now()
        while first + POLL_DELAY_AFTER_BAR_CLOSE < now - BAR_INTERVAL:
            first += BAR_INTERVAL
        return first + POLL_DELAY_AFTER_BAR_CLOSE

    def _now(self) -> datetime:
        return self._clock().astimezone(timezone.utc)

    async def _sleep_until(self, moment: datetime) -> None:
        remaining = (moment.astimezone(timezone.utc) - self._now()).total_seconds()
        if remaining > 0:
            await self._sleep(remaining)


def _decision_symbol(shadow: ShadowRunSession, decision: Decision) -> str:
    for candidate in shadow.candidates:
        if candidate.id == decision.candidate_id:
            return candidate.symbol
    raise LookupError("decision does not belong to a session candidate")


__all__ = [
    "MOMO_STRATEGY_NAME",
    "ORB_STRATEGY_NAME",
    "SessionAborted",
    "SessionReport",
    "ShadowSessionRunner",
    "ensure_orb_strategy",
    "ensure_strategy",
    "momo_strategy_spec",
    "orb_strategy_spec",
]
