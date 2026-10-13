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
from trading.scheduler.sessions import MARKET_TIMEZONE, MarketSession
from trading.sendable_identities import (
    SENDABLE_IDENTITIES,
    SENDABLE_SPEC_MISMATCH,
    SendableIdentityError,
)

logger = logging.getLogger("autotrader.shadow.session")

ORB_STRATEGY_NAME = "ORB_V1"
MOMO_STRATEGY_NAME = "MOMO_HOD_V2"
BAR_INTERVAL = timedelta(minutes=5)
# The least time a poll gets, from its slot to its freshness limit, to fetch
# every pending candidate: what the original 30 s limit left a poll made 5 s
# after the close.
MIN_PASS_BUDGET = timedelta(seconds=25)
SCANNER_ATTEMPTS = 3
SCANNER_RETRY_DELAY = timedelta(seconds=20)
# How often a dynamic run asks its candidate refresh for newly hot symbols.
DEFAULT_REFRESH_INTERVAL = timedelta(minutes=15)
# Log tag for a poll slot reached too late to find fresh bars.
LATE_POLLS_SKIPPED = "late_polls_skipped"


class SessionMarketData(Protocol):
    async def scanner_snapshot(self, *, scanner_run_id: str) -> ScannerSnapshot: ...

    async def completed_bars(self, *, con_id: int, now: datetime) -> tuple[CompletedBar, ...]: ...

    async def quote(self, *, con_id: int) -> QuoteSnapshot: ...

    async def liquidity(self, *, con_id: int, session_date: date) -> LiquidityProfile: ...


AccountReader = Callable[[], Awaitable[AccountSnapshot]]
# Per-strategy bar source; GAP_GO needs pre-market bars merged with RTH history.
BarsFetcher = Callable[[Candidate, datetime], Awaitable[tuple[CompletedBar, ...]]]
CandidateSource = Callable[
    [Run], Awaitable[tuple[ScannerRun, tuple[Candidate, ...], dict[int, LiquidityProfile]]]
]
# ``(run, now) -> (candidates, liquidity)``: symbols that became eligible since
# the run's start, in the run's own scanner run (see ``hotlist_refresh``).
CandidateRefresh = Callable[
    [Run, datetime], Awaitable[tuple[tuple[Candidate, ...], dict[int, LiquidityProfile]]]
]
# Qualifies one conId (cached by the market facade) so its quotes have a tick grid.
ContractLoader = Callable[[int], Awaitable[object]]
Sleeper = Callable[[float], Awaitable[None]]
Reconnector = Callable[[], Awaitable[None]]
# ``(allowed, reason)``: asked once per poll; ``(False, reason)`` skips that
# poll's assessments so candidates stay pending (e.g. MOMO in a risk-off market).
EntryGate = Callable[[], Awaitable[tuple[bool, str]]]


class SessionAborted(RuntimeError):
    """The session could not start (e.g. the scanner never answered)."""


@dataclass
class SessionReport:
    run_id: str
    session_date: date
    candidates: int = 0
    added_candidates: int = 0
    outcomes: Counter[str] = field(default_factory=Counter)
    polls: int = 0
    paused_polls: int = 0
    late_polls_skipped: int = 0
    data_errors: int = 0

    def summary(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "session_date": self.session_date.isoformat(),
            "candidates": self.candidates,
            "added_candidates": self.added_candidates,
            "outcomes": dict(self.outcomes),
            "polls": self.polls,
            "paused_polls": self.paused_polls,
            "late_polls_skipped": self.late_polls_skipped,
            "data_errors": self.data_errors,
        }


@dataclass(frozen=True, slots=True)
class PollSchedule:
    """When one strategy child polls inside every five-minute bar.

    ``offset`` is the poll's delay after each bar close, and ``bar_max_age``
    the freshness limit its bars are validated against, so the run has
    ``bar_max_age - offset`` to fetch every pending candidate (its pass budget).
    """

    offset: timedelta
    bar_max_age: timedelta

    def __post_init__(self) -> None:
        if self.offset <= timedelta(0):
            raise ValueError("a poll must follow the bar close it evaluates")
        if self.bar_max_age >= BAR_INTERVAL:
            # Under one interval, a bar that passes is always the latest completed one.
            raise ValueError("the bar freshness limit must stay under one bar interval")
        if self.bar_max_age - self.offset < MIN_PASS_BUDGET:
            # Otherwise most, or all, of every pass would be refused as stale.
            raise ValueError("the poll offset leaves too little time under the freshness limit")

    @property
    def late_poll_limit(self) -> timedelta:
        """How late a slot may still be polled: while half its pass budget remains."""
        return (self.bar_max_age - self.offset) / 2


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
    """MOMO_HOD_V2's catalogue entry; the executable rules are ``MomoConfig``."""
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
            "prior high, above VWAP, with elevated bar and day volume, 10:00-15:00 ET. "
            "V2 stop: the largest of 1.5x ATR(14), 0.6% of entry, and one tick below "
            "the signal bar's low; 2R target. A signal is skipped, and may retry on a "
            "later bar, when that stop exceeds 1.5% of entry or the entry is more than "
            "4.5 ATR above VWAP. Authoritative rules: trading/rules/momo.py MomoConfig."
        ),
    )


async def ensure_strategy(*, owner_id: str, spec: StrategySpec) -> StrategyRecord:
    """Return the owner's strategy named ``spec.name``, creating it in SHADOW.

    A sendable identity is reused only while its stored content hash is the
    hash of the spec this code builds: the PAPER bridge sends by stored name
    alone, so a record whose content differs must never collect decisions.
    """
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
        reused = max(existing, key=lambda record: record.version)
        # content_hash is the stored column; StrategyRecord has already
        # checked it equals the fingerprint of the stored spec_json.
        if spec.name in SENDABLE_IDENTITIES and reused.content_hash != spec.fingerprint():
            logger.critical(
                "%s: %s record %s v%d stores spec %s but this code builds %s; "
                "refusing to reuse it",
                SENDABLE_SPEC_MISMATCH,
                spec.name,
                reused.strategy_id,
                reused.version,
                reused.content_hash,
                spec.fingerprint(),
                stack_info=True,
            )
            raise SendableIdentityError(
                SENDABLE_SPEC_MISMATCH,
                f"{spec.name} record {reused.strategy_id} v{reused.version} does not "
                "hold the spec this code builds",
            )
        return reused
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
        poll_schedule: PollSchedule,
        sleep: Sleeper = asyncio.sleep,
        reconnect: Reconnector | None = None,
        holder_id: str | None = None,
        entry_gate: EntryGate | None = None,
        entry_gate_name: str = "entries",
        bars_fetcher: BarsFetcher | None = None,
        contract_loader: ContractLoader | None = None,
    ) -> None:
        """``poll_schedule`` places every poll inside its bar (a required
        choice: children sharing symbols must not poll together).
        ``entry_gate`` may pause assessments poll by poll; ``entry_gate_name``
        labels its log lines (``<name> paused: <reason>``). ``contract_loader``
        runs before every quote request, so candidates that joined without a
        loaded contract (a refresh, or a resumed run) still quote on their tick
        grid."""
        if not isinstance(poll_schedule, PollSchedule):
            raise TypeError("poll_schedule must be a PollSchedule")
        self._poll_schedule = poll_schedule
        self._market = market
        self._read_account = read_account
        self._runner = runner
        self._clock = clock
        self._sleep = sleep
        self._reconnect = reconnect
        self._holder_id = holder_id or f"session-runner:{uuid4()}"
        self._entry_gate = entry_gate
        self._entry_gate_name = entry_gate_name
        self._bars_fetcher = bars_fetcher
        self._contract_loader = contract_loader
        self._entries_paused = False
        self._gate_failing = False

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
        candidate_refresh: CandidateRefresh | None = None,
        refresh_interval: timedelta = DEFAULT_REFRESH_INTERVAL,
    ) -> SessionReport:
        """Run one strategy session.

        Without ``candidate_source`` candidates come from the 09:45 IBKR scan;
        with it (e.g. the screener hot list) they are taken when the strategy's
        window opens, and liquidity is seeded from the source.

        ``candidate_refresh`` makes the run dynamic: once ``refresh_interval``
        has passed, the next poll first adds the symbols that became eligible
        since and assesses them with the rest. The run keeps polling, even
        with nothing pending, and stays open until the cutoff sweep; a
        restart resumes every candidate added before.
        """
        if candidate_refresh is not None:
            if candidate_source is None:
                raise ValueError("candidate_refresh extends a candidate_source run")
            if refresh_interval <= timedelta(0):
                raise ValueError("refresh_interval must be positive")
        dynamic = candidate_refresh is not None
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
            dynamic=dynamic,
        ) as shadow:
            if dynamic:
                # Includes every candidate a restarted run had already added.
                logger.info("run %s holds %d candidates", run.id, len(shadow.candidates))
            report.candidates = len(shadow.candidates)
            latest: dict[str, CandidateInput] = {}
            cutoff = shadow.cutoff
            refreshed_at = self._now()
            poll_at = self._first_poll(session, report, window_start)
            # A dynamic run polls on with nothing pending: a refresh may still add.
            while poll_at < cutoff and (dynamic or shadow.pending()):
                await self._sleep_until(poll_at)
                if candidate_refresh is not None and self._now() - refreshed_at >= refresh_interval:
                    refreshed_at = self._now()
                    await self._refresh(
                        shadow=shadow,
                        refresh=candidate_refresh,
                        liquidity=liquidity,
                        report=report,
                    )
                if shadow.pending():
                    report.polls += 1
                    if await self._entries_allowed():
                        await self._poll(
                            shadow=shadow,
                            session_date=session_date,
                            latest=latest,
                            liquidity=liquidity,
                            report=report,
                        )
                    else:
                        # Paused candidates stay pending; the cutoff sweep below
                        # still makes every one of them terminal.
                        report.paused_polls += 1
                poll_at = self._due_poll(poll_at + BAR_INTERVAL, report)
            # Only the sweep completes a dynamic run, even with nothing pending.
            if dynamic or shadow.pending():
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
                if self._bars_fetcher is not None:
                    bars = await self._bars_fetcher(candidate, self._now())
                else:
                    bars = await self._market.completed_bars(
                        con_id=candidate.con_id, now=self._now()
                    )
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
                if self._contract_loader is not None:
                    await self._contract_loader(candidate.con_id)
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

    async def _refresh(
        self,
        *,
        shadow: ShadowRunSession,
        refresh: CandidateRefresh,
        liquidity: dict[int, LiquidityProfile],
        report: SessionReport,
    ) -> None:
        """Add newly eligible symbols; a failed refresh keeps the current set."""
        try:
            candidates, profiles = await refresh(shadow.run, self._now())
        except Exception as exc:  # noqa: BLE001 - retried at the next interval
            logger.warning("candidate refresh failed: %s", exc)
            return
        try:
            added = await shadow.add_candidates(candidates, now=self._now())
        except ValueError as exc:
            # Malformed refresh output admits nothing. Lease loss and storage
            # conflicts propagate: like a failed assessment they end the session.
            logger.error("candidate refresh rejected: %s", exc)
            return
        if not added:
            return
        for candidate in added:
            profile = profiles.get(candidate.con_id)
            if profile is not None:
                liquidity[candidate.con_id] = profile
        report.added_candidates += len(added)
        report.candidates = len(shadow.candidates)
        logger.info(
            "refresh added %d candidates: %s",
            len(added),
            ", ".join(candidate.symbol for candidate in added),
        )

    async def _entries_allowed(self) -> bool:
        """Ask the entry gate; each pause, resume or gate failure is logged once.

        The gate only adds caution on top of the rules and the risk engine, so
        a gate that raises or answers malformed never blocks entries.
        """
        if self._entry_gate is None:
            return True
        name = self._entry_gate_name
        try:
            answer = await self._entry_gate()
            allowed, reason = answer
            if not isinstance(allowed, bool):
                raise TypeError("entry gate must answer (bool, str)")
        except Exception as exc:  # noqa: BLE001 - fail open, see docstring
            if not self._gate_failing:
                logger.warning(
                    "%s entry gate failed (%s); entries allowed until it recovers",
                    name,
                    type(exc).__name__,
                )
            self._gate_failing = True
            allowed, reason = True, f"entry gate failed ({type(exc).__name__})"
        else:
            if self._gate_failing:
                logger.info("%s entry gate recovered", name)
            self._gate_failing = False
        paused = not allowed
        if paused != self._entries_paused:
            self._entries_paused = paused
            logger.warning("%s %s: %s", name, "paused" if paused else "resumed", reason)
        return allowed

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

    def _first_poll(
        self,
        session: MarketSession,
        report: SessionReport,
        window_start: datetime | None = None,
    ) -> datetime:
        """This child's slot after the window's first bar close, or the slot due now.

        A strategy with its own window (GAP_GO opens at 09:35) polls from that
        window; scan-driven ORB keeps the session's first signal bar (09:50).
        """
        first = window_start if window_start is not None else session.first_signal_at
        return self._due_poll(first + self._poll_schedule.offset, report)

    def _due_poll(self, slot: datetime, report: SessionReport) -> datetime:
        """``slot`` if it is still ahead or only a little late; else the next slot.

        Bars are refused once older than ``bar_max_age``, and a slot reached
        late (a restart mid-bar, a slow pass) starts its pass with bars that
        much older. It still runs while half its pass budget remains; later,
        most of its requests would be refused as stale (2026-10-12: MOMO began
        a pass 78 s after its slot and 28 of its 35 bar requests were refused),
        so the run waits for the next bar's slot instead.
        """
        now = self._now()
        if slot >= now:
            return slot
        # Slots before the current bar's belong to history, not to this pass.
        slot += BAR_INTERVAL * ((now - slot) // BAR_INTERVAL)
        late = now - slot
        if late <= self._poll_schedule.late_poll_limit:
            return slot
        report.late_polls_skipped += 1
        logger.warning(
            "skipping the %s ET poll [%s]: %d s late, past the %d s a late poll may start",
            slot.astimezone(MARKET_TIMEZONE).strftime("%H:%M:%S"),
            LATE_POLLS_SKIPPED,
            late.total_seconds(),
            self._poll_schedule.late_poll_limit.total_seconds(),
        )
        return slot + BAR_INTERVAL

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
    "DEFAULT_REFRESH_INTERVAL",
    "LATE_POLLS_SKIPPED",
    "MIN_PASS_BUDGET",
    "MOMO_STRATEGY_NAME",
    "ORB_STRATEGY_NAME",
    "CandidateRefresh",
    "ContractLoader",
    "EntryGate",
    "PollSchedule",
    "SessionAborted",
    "SessionReport",
    "ShadowSessionRunner",
    "ensure_orb_strategy",
    "ensure_strategy",
    "momo_strategy_spec",
    "orb_strategy_spec",
]
