"""Compose scanner data, strategy rules, AI review, and risk into terminal decisions."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import logging

from trading.contracts import (
    AIReview,
    Candidate,
    CompletedBar,
    Decision,
    DecisionOutcome,
    EngineMode,
    PortfolioSnapshot,
    RiskApproval,
    RiskPolicy,
    RuleSetup,
    QuoteSnapshot,
    StrategyRecord,
    fingerprint_payload,
)
from trading.engine import AutotraderEngine
from trading.intelligence.provider import TradeProposalProvider
from trading.risk.engine import IDENTITY_SIZING_MISSING, SIZE_SCALED_TO_ZERO, require_size_multiplier
from trading.rules.orb import NO_SIGNAL_YET, QUOTE_REQUIRED
from trading.rules.strategy import (
    ORBStrategy,
    StrategyEvaluator,
    entry_window_utc,
    is_data_rejection,
)
from trading.scheduler.decision_keys import decision_key
from trading.sendable_identities import is_sendable_identity
from trading.storage.decision_sizing import DecisionSizing, decision_sizing, record_decision_sizing
from trading.storage.manager_setups import FormedSetup, formed_setup, record_formed_setup
from trading.storage.repository import record_decision


logger = logging.getLogger("autotrader.shadow.pipeline")

DecisionRecorder = Callable[..., Awaitable[None]]
SetupRecorder = Callable[[FormedSetup], Awaitable[object]]
SizingRecorder = Callable[[DecisionSizing], Awaitable[object]]
# Named token of the one failure the pipeline swallows: evidence that could
# not be saved. The decision it follows is already recorded and unchanged.
SETUP_PERSIST_FAILED = "setup_persist_failed"
# The managed-size overlay failed: the setup is sized as if unmanaged (step 1),
# never larger (Principle 3).
MANAGED_SIZE_FAILED = "managed_size_failed"
MANAGED_RECORD_FAILED = "managed_record_failed"
# The decision's effective size multiplier could not be kept beside it
# (trading.storage.decision_sizing); the decision is recorded and unchanged.
SIZING_RECORD_FAILED = "sizing_record_failed"
NO_SCALING = Decimal("1")


class ManagedSizing(Protocol):
    """The AI day manager's verdict hookup for a managed strategy (Principle 2).

    ``size_multiplier`` is the day step times the setup step for a PROPOSE
    review, in [0, 1]; 0 skips the setup. ``after_decision`` records the
    narrowing beside the recorded decision. An unmanaged pipeline has no
    overlay and sizes exactly as before.
    """

    async def size_multiplier(self, *, setup: RuleSetup, review: AIReview) -> Decimal: ...

    async def after_decision(
        self,
        *,
        decision: Decision,
        setup: RuleSetup,
        candidate: Candidate,
        policy: RiskPolicy,
        portfolio: PortfolioSnapshot,
        size_multiplier: Decimal,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class Assessment:
    """Outcome of assessing one candidate at one moment.

    ``decision`` is set only for a terminal, recorded outcome. Otherwise the
    candidate stays pending: ``needs_quote`` asks the caller to fetch a quote
    and assess again immediately; ``pending_reason`` explains any other wait.
    """

    decision: Decision | None
    needs_quote: bool = False
    pending_reason: str | None = None

    @property
    def terminal(self) -> bool:
        return self.decision is not None


@dataclass(frozen=True, slots=True)
class CandidateInput:
    """All immutable inputs needed to assess one scanner candidate."""

    candidate: Candidate
    bars: tuple[CompletedBar, ...]
    quote: QuoteSnapshot | None
    reference_price: Decimal | int | str
    average_dollar_volume: Decimal | int | str
    snapshot_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "bars", tuple(self.bars))

    def fingerprint(self) -> str:
        """Return an idempotency fingerprint for the complete input snapshot."""
        if self.snapshot_fingerprint is not None:
            invalid_digest = (
                not isinstance(self.snapshot_fingerprint, str)
                or len(self.snapshot_fingerprint) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in self.snapshot_fingerprint
                )
            )
            if invalid_digest:
                raise ValueError("snapshot_fingerprint must be a SHA-256 digest")
            return self.snapshot_fingerprint

        bar_fingerprints = []
        for bar in self.bars:
            fingerprint = getattr(bar, "fingerprint", None)
            if not callable(fingerprint):
                raise ValueError("candidate bars must expose fingerprint()")
            bar_fingerprints.append(fingerprint())
        quote_fingerprint = None
        if self.quote is not None:
            fingerprint = getattr(self.quote, "fingerprint", None)
            if not callable(fingerprint):
                raise ValueError("candidate quote must expose fingerprint()")
            quote_fingerprint = fingerprint()
        return fingerprint_payload(
            {
                "candidate": fingerprint_payload(self.candidate),
                "bars": bar_fingerprints,
                "quote": quote_fingerprint,
                "reference_price": str(self.reference_price),
                "average_dollar_volume": str(self.average_dollar_volume),
            }
        )


def entry_cutoff_utc(session_date: date, strategy: StrategyEvaluator) -> datetime:
    """No setup may be built for ``strategy`` at or after this moment."""
    return entry_window_utc(strategy, session_date)[1]


def _session_day(session_date: date | datetime) -> date:
    session_day = session_date.date() if isinstance(session_date, datetime) else session_date
    if not isinstance(session_day, date):
        raise TypeError("session_date must be a date or timezone-aware datetime")
    return session_day


def _blocked_review(*, setup: RuleSetup, reason: str) -> AIReview:
    response_hash = hashlib.sha256(reason.encode("utf-8")).hexdigest()
    return AIReview(
        setup_fingerprint=setup.fingerprint(),
        decision="ABSTAIN",
        rationale="AI review blocked before an execution decision was made.",
        model_id="unavailable",
        prompt_version="unavailable",
        response_hash=response_hash,
        latency_ms=0,
        cost_usd=Decimal("0"),
        failure_reason=reason,
    )


def _blocked_risk(
    *,
    setup: RuleSetup,
    review: AIReview,
    policy: RiskPolicy,
    portfolio: PortfolioSnapshot,
    code: str = "risk_evaluation_failed",
) -> RiskApproval:
    now = datetime.now(timezone.utc)
    return RiskApproval(
        approved=False,
        codes=(code,),
        setup_fingerprint=setup.fingerprint(),
        ai_review_fingerprint=review.fingerprint(),
        policy_fingerprint=policy.fingerprint(),
        portfolio_fingerprint=portfolio.fingerprint,
        expires_at=now + timedelta(seconds=15),
    )


class ShadowPipeline:
    """Assess candidates into terminal decisions with no broker side effects."""

    def __init__(
        self,
        *,
        proposal_provider: TradeProposalProvider,
        decision_recorder: DecisionRecorder = record_decision,
        strategy: StrategyEvaluator = ORBStrategy(),
        mode: EngineMode = EngineMode.SHADOW,
        setup_recorder: SetupRecorder = record_formed_setup,
        sizing: ManagedSizing | None = None,
        identity_multiplier: Decimal | None = NO_SCALING,
        sizing_recorder: SizingRecorder = record_decision_sizing,
    ) -> None:
        """``identity_multiplier`` is the sendable identity's fixed size factor
        (``trading.risk.identity_sizing``), a Decimal in (0, 1]; ``None`` means a
        sendable identity with no row, and every setup is then blocked with
        ``identity_sizing_missing`` (never sized at 1 by default).

        ``sizing_recorder`` keeps every decision's effective multiplier
        (identity factor x managed factor) beside the decision, never in it:
        the decision payload is fingerprinted content."""
        if not isinstance(strategy, StrategyEvaluator):
            raise TypeError("strategy must implement StrategyEvaluator")
        if identity_multiplier is not None:
            if not isinstance(identity_multiplier, Decimal):
                raise TypeError("identity_multiplier must be a Decimal in (0, 1] or None")
            identity_multiplier = require_size_multiplier(identity_multiplier)
        self._proposal_provider = proposal_provider
        self._decision_recorder = decision_recorder
        self._strategy = strategy
        self._mode = mode
        self._setup_recorder = setup_recorder
        self._sizing = sizing
        self._identity_multiplier = identity_multiplier
        self._sizing_recorder = sizing_recorder
        self._identity_missing_reported = False

        async def defer_recording(**_: object) -> None:
            return None

        self._engine = AutotraderEngine(decision_recorder=defer_recording)

    @property
    def proposal_provider(self) -> TradeProposalProvider:
        return self._proposal_provider

    @property
    def strategy(self) -> StrategyEvaluator:
        return self._strategy

    @property
    def mode(self) -> EngineMode:
        return self._mode

    @property
    def setup_recorder(self) -> SetupRecorder:
        return self._setup_recorder

    @property
    def sizing(self) -> ManagedSizing | None:
        return self._sizing

    @property
    def identity_multiplier(self) -> Decimal | None:
        return self._identity_multiplier

    def with_decision_recorder(self, decision_recorder: DecisionRecorder) -> "ShadowPipeline":
        """This pipeline with every constructor argument, recording through ``decision_recorder``.

        The repository runner wraps the recorder with its lease heartbeat. It
        used to rebuild the pipeline field by field and dropped the identity
        size factor, so every identity was sized x1 whatever its row said; the
        copy lives here, beside the constructor it must mirror (a test compares
        it with the constructor's signature).
        """
        return ShadowPipeline(
            proposal_provider=self._proposal_provider,
            decision_recorder=decision_recorder,
            strategy=self._strategy,
            mode=self._mode,
            setup_recorder=self._setup_recorder,
            sizing=self._sizing,
            identity_multiplier=self._identity_multiplier,
            sizing_recorder=self._sizing_recorder,
        )

    def _engine_size(self, multiplier: Decimal) -> dict[str, Decimal]:
        """The engine's ``size_multiplier`` keyword: identity factor x managed factor.

        An unmanaged pipeline with the neutral identity factor calls the engine
        exactly as before the manager and the identities existed (no keyword at
        all); every other case passes one Decimal product in [0, 1].
        """
        identity = self._identity_multiplier
        assert identity is not None  # the None case never reaches the engine
        if self._sizing is None:
            return {} if identity == NO_SCALING else {"size_multiplier": identity}
        return {"size_multiplier": identity * multiplier}

    def _report_identity_missing(self, *, strategy: StrategyRecord, setup: RuleSetup) -> None:
        if self._identity_missing_reported:
            return
        self._identity_missing_reported = True
        logger.critical(
            "%s: %s has no fixed size multiplier; every setup of this run is blocked (setup %s)",
            IDENTITY_SIZING_MISSING,
            strategy.spec.name,
            setup.fingerprint(),
        )

    async def run(
        self,
        *,
        run_id: str,
        strategy: StrategyRecord,
        candidates: Sequence[CandidateInput],
        portfolio: PortfolioSnapshot,
        policy: RiskPolicy,
        session_date: date | datetime,
        now: datetime | None = None,
        existing_decisions: Mapping[str, Decision] | None = None,
    ) -> tuple[Decision, ...]:
        """Assess candidates in input order; pending candidates are omitted."""
        if not run_id.strip():
            raise ValueError("run_id must be non-empty")
        session_day = _session_day(session_date)
        if strategy.owner_id.strip() == "":
            raise ValueError("strategy owner_id must be non-empty")

        candidate_ids = [item.candidate.id for item in candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("a shadow run cannot assess one candidate more than once")

        existing = existing_decisions or {}
        admitted_symbols = frozenset(
            item.candidate.symbol
            for item in candidates
            if item.candidate.admission_status == "admitted"
        )
        results: list[Decision] = []
        for item in candidates:
            assessment = await self.assess_one(
                run_id=run_id,
                strategy=strategy,
                item=item,
                portfolio=portfolio,
                policy=policy,
                session_date=session_day,
                admitted_symbols=admitted_symbols,
                now=now,
                prior=existing.get(item.candidate.id),
            )
            if assessment.decision is not None:
                results.append(assessment.decision)
        return tuple(results)

    async def assess_one(
        self,
        *,
        run_id: str,
        strategy: StrategyRecord,
        item: CandidateInput,
        portfolio: PortfolioSnapshot,
        policy: RiskPolicy,
        session_date: date | datetime,
        admitted_symbols: frozenset[str],
        now: datetime | None = None,
        prior: Decision | None = None,
    ) -> Assessment:
        """Assess one candidate; record and return a decision only when terminal.

        Before the strategy's entry cutoff, missing or transiently bad data and
        retryable rejections leave the candidate pending so a later poll can
        still see its signal. At or after the cutoff every outcome is terminal
        and no new setup is built, so no entry can be created after the cutoff.
        """
        session_day = _session_day(session_date)
        observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if prior is not None:
            # One terminal decision per candidate: a recorded outcome is final
            # even though later polls observe newer bars and quotes.
            if (
                prior.run_id != run_id
                or prior.candidate_id != item.candidate.id
                or prior.strategy_id != strategy.strategy_id
                or prior.strategy_version != strategy.version
            ):
                raise ValueError("existing decision belongs to a different run or strategy")
            return Assessment(decision=prior)

        cutoff_passed = observed >= entry_cutoff_utc(session_day, self._strategy)
        snapshot_fingerprint = item.fingerprint()
        key = decision_key(
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            snapshot_fingerprint=snapshot_fingerprint,
        )

        async def terminal(outcome: DecisionOutcome) -> Assessment:
            decision = self._terminal_decision(
                run_id=run_id,
                strategy=strategy,
                candidate=item.candidate,
                key=key,
                outcome=outcome,
            )
            await self._record(decision, strategy.owner_id)
            await self._record_sizing(decision=decision, strategy=strategy, managed_factor=NO_SCALING)
            return Assessment(decision=decision)

        result = self._strategy.evaluate(
            candidate=item.candidate,
            bars=item.bars,
            quote=None if cutoff_passed else item.quote,
            session_date=session_day,
            run_id=run_id,
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            reference_price=item.reference_price,
            average_dollar_volume=item.average_dollar_volume,
            candidate_input_fingerprint=snapshot_fingerprint,
            now=observed,
        )
        if result.setup is None:
            reason = result.rejection_reason or ""
            if reason == QUOTE_REQUIRED:
                if cutoff_passed:
                    # The signal was never priced inside the entry window.
                    return await terminal(DecisionOutcome.DATA_BLOCKED)
                return Assessment(decision=None, needs_quote=True, pending_reason=reason)
            if is_data_rejection(reason):
                if not cutoff_passed:
                    return Assessment(decision=None, pending_reason=reason)
                return await terminal(DecisionOutcome.DATA_BLOCKED)
            if not cutoff_passed and (reason == NO_SIGNAL_YET or result.retryable):
                return Assessment(decision=None, pending_reason=reason)
            return await terminal(DecisionOutcome.RULE_REJECTED)

        setup = result.setup
        self._require_strategy_setup(
            setup=setup,
            run_id=run_id,
            candidate=item.candidate,
            cutoff_passed=cutoff_passed,
        )
        try:
            review = await self._proposal_provider.propose_trade(setup=setup)
            if (
                not isinstance(review, AIReview)
                or review.setup_fingerprint != setup.fingerprint()
            ):
                raise ValueError("AI review did not preserve setup identity")
        except Exception as exc:
            reason = "provider_unavailable"
            if isinstance(exc, ValueError):
                reason = "invalid_provider_review"
            review = _blocked_review(setup=setup, reason=reason)

        multiplier = await self._managed_multiplier(setup=setup, review=review)
        try:
            if self._identity_multiplier is None:
                # A sendable identity without a size row: blocked, never sized at 1.
                self._report_identity_missing(strategy=strategy, setup=setup)
                decision = self._terminal_decision(
                    run_id=run_id,
                    strategy=strategy,
                    candidate=item.candidate,
                    key=key,
                    outcome=DecisionOutcome.RISK_BLOCKED,
                    setup=setup,
                    review=review,
                    risk=_blocked_risk(
                        setup=setup,
                        review=review,
                        policy=policy,
                        portfolio=portfolio,
                        code=IDENTITY_SIZING_MISSING,
                    ),
                )
            elif multiplier == 0:
                # The manager's steps skip this setup: a deterministic risk
                # block, never a resize upward.
                decision = self._terminal_decision(
                    run_id=run_id,
                    strategy=strategy,
                    candidate=item.candidate,
                    key=key,
                    outcome=DecisionOutcome.RISK_BLOCKED,
                    setup=setup,
                    review=review,
                    risk=_blocked_risk(
                        setup=setup,
                        review=review,
                        policy=policy,
                        portfolio=portfolio,
                        code=SIZE_SCALED_TO_ZERO,
                    ),
                )
            else:
                decision = await self._engine.assess(
                    mode=self._mode,
                    strategy=strategy,
                    setup=setup,
                    review=review,
                    portfolio=portfolio,
                    policy=policy,
                    admitted_symbols=admitted_symbols,
                    **self._engine_size(multiplier),
                )
        except Exception:
            if review.decision != "PROPOSE":
                decision = self._terminal_decision(
                    run_id=run_id,
                    strategy=strategy,
                    candidate=item.candidate,
                    key=key,
                    outcome=DecisionOutcome.AI_BLOCKED,
                    setup=setup,
                    review=_blocked_review(setup=setup, reason="review_invalid"),
                )
            else:
                risk = _blocked_risk(
                    setup=setup,
                    review=review,
                    policy=policy,
                    portfolio=portfolio,
                )
                decision = self._terminal_decision(
                    run_id=run_id,
                    strategy=strategy,
                    candidate=item.candidate,
                    key=key,
                    outcome=DecisionOutcome.RISK_BLOCKED,
                    setup=setup,
                    review=review,
                    risk=risk,
                )
        else:
            if multiplier != 0 and self._identity_multiplier is not None:
                decision = Decision.model_validate(
                    {
                        **decision.model_dump(mode="json"),
                        "decision_key": key,
                    }
                )
        await self._record(decision, strategy.owner_id)
        # Evidence after the decision, never before it: the decision's inputs,
        # outcome and write time are exactly those of a pipeline without it.
        await self._persist_setup(
            setup=setup, strategy=strategy, item=item, session_date=session_day
        )
        await self._record_managed(
            decision=decision,
            setup=setup,
            item=item,
            policy=policy,
            portfolio=portfolio,
            multiplier=multiplier,
        )
        await self._record_sizing(decision=decision, strategy=strategy, managed_factor=multiplier)
        return Assessment(decision=decision)

    async def _managed_multiplier(self, *, setup: RuleSetup, review: AIReview) -> Decimal:
        """The overlay's multiplier for a PROPOSE review; 1 without an overlay.

        Any failure or out-of-range answer sizes the setup as unmanaged (1),
        the neutral fallback, and is logged CRITICAL: never larger than 1.
        """
        if self._sizing is None or review.decision != "PROPOSE":
            return NO_SCALING
        try:
            multiplier = await self._sizing.size_multiplier(setup=setup, review=review)
            if not isinstance(multiplier, Decimal) or not multiplier.is_finite():
                raise ValueError("size multiplier must be a finite Decimal")
            if not Decimal("0") <= multiplier <= NO_SCALING:
                raise ValueError(f"size multiplier {multiplier} is outside [0, 1]")
        except Exception:  # noqa: BLE001 - neutral sizing, see docstring
            logger.critical(
                "%s: setup %s sized as unmanaged", MANAGED_SIZE_FAILED, setup.fingerprint(),
                exc_info=True,
            )
            return NO_SCALING
        return multiplier

    async def _record_managed(
        self,
        *,
        decision: Decision,
        setup: RuleSetup,
        item: CandidateInput,
        policy: RiskPolicy,
        portfolio: PortfolioSnapshot,
        multiplier: Decimal,
    ) -> None:
        if self._sizing is None:
            return
        try:
            await self._sizing.after_decision(
                decision=decision,
                setup=setup,
                candidate=item.candidate,
                policy=policy,
                portfolio=portfolio,
                size_multiplier=multiplier,
            )
        except Exception:  # noqa: BLE001 - evidence must never block or alter a decision
            logger.error(
                "%s: run %s setup %s", MANAGED_RECORD_FAILED, setup.run_id, setup.fingerprint(),
                exc_info=True,
            )

    async def record_data_blocked(
        self,
        *,
        run_id: str,
        strategy: StrategyRecord,
        candidate: Candidate,
    ) -> Decision:
        """Terminal outcome for a candidate whose data never arrived in the window."""
        key = decision_key(
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            snapshot_fingerprint=fingerprint_payload(
                {"candidate": fingerprint_payload(candidate), "data": "unavailable"}
            ),
        )
        decision = self._terminal_decision(
            run_id=run_id,
            strategy=strategy,
            candidate=candidate,
            key=key,
            outcome=DecisionOutcome.DATA_BLOCKED,
        )
        await self._record(decision, strategy.owner_id)
        await self._record_sizing(decision=decision, strategy=strategy, managed_factor=NO_SCALING)
        return decision

    async def _record(self, decision: Decision, owner_id: str) -> None:
        await self._decision_recorder(decision=decision, owner_id=owner_id)

    async def _record_sizing(
        self, *, decision: Decision, strategy: StrategyRecord, managed_factor: Decimal
    ) -> None:
        """Keep the decision's effective multiplier beside it; a failure is logged, never raised.

        ``managed_factor`` is the factor this decision was sized with: the
        overlay's answer for a PROPOSE review (0 skips the setup), else 1.
        The identity factor is this pipeline's own; ``None`` (a sendable
        identity with no row) is recorded as no multiplier at all.

        The write is bounded by ``SIZING_RECORD_DEADLINE_S`` (N15a MEDIUM-1): a
        stalled sidecar must never hold up the next candidate. After a timeout
        or an OS error the sidecar is off for this pipeline (the run): logged
        once, then never tried again, so no further write can stall or pile up.
        """
        import asyncio

        from trading.storage import decision_sizing as sidecar

        if getattr(self, "_sizing_disabled", False):
            return
        try:
            name = strategy.spec.name
            row = decision_sizing(
                decision,
                strategy_name=name,
                sendable=is_sendable_identity(name),
                identity_factor=self._identity_multiplier,
                managed_factor=managed_factor,
            )
            async with asyncio.timeout(sidecar.SIZING_RECORD_DEADLINE_S):
                await self._sizing_recorder(row)
        except Exception as exc:  # noqa: BLE001 - evidence must never block or alter a decision
            logger.error(
                "%s: run %s decision %s", SIZING_RECORD_FAILED, decision.run_id, decision.id,
                exc_info=True,
            )
            if isinstance(exc, OSError):  # TimeoutError is an OSError
                self._sizing_disabled = True
                logger.critical(
                    "%s: decision-sizing sidecar disabled for this run %s after %s; decisions are unaffected",
                    sidecar.SIZING_SIDECAR_DISABLED, decision.run_id, type(exc).__name__,
                )

    async def _persist_setup(
        self,
        *,
        setup: RuleSetup,
        strategy: StrategyRecord,
        item: CandidateInput,
        session_date: date,
    ) -> None:
        """Save the formed setup; any failure is logged and never reaches a decision."""
        try:
            row = formed_setup(
                setup=setup,
                strategy=strategy,
                candidate=item.candidate,
                bars=item.bars,
                session_date=session_date,
            )
            await self._setup_recorder(row)
        except Exception:  # noqa: BLE001 - evidence must never block or alter a decision
            logger.error(
                "%s: run %s candidate %s setup %s",
                SETUP_PERSIST_FAILED,
                setup.run_id,
                setup.candidate_id,
                setup.fingerprint(),
                exc_info=True,
            )

    def _require_strategy_setup(
        self,
        *,
        setup: RuleSetup,
        run_id: str,
        candidate: Candidate,
        cutoff_passed: bool,
    ) -> None:
        # Strategies are pluggable, so the window and identity are re-checked
        # here before a setup can reach review, risk, or a recorded decision.
        if cutoff_passed:
            raise ValueError("strategy built a setup at or after its entry cutoff")
        if (
            setup.strategy_kind != self._strategy.strategy_kind
            or setup.run_id != run_id
            or setup.candidate_id != candidate.id
            or setup.symbol != candidate.symbol
        ):
            raise ValueError("strategy setup does not belong to the assessed candidate")

    @staticmethod
    def _terminal_decision(
        *,
        run_id: str,
        strategy: StrategyRecord,
        candidate: Candidate,
        key: str,
        outcome: DecisionOutcome,
        setup: RuleSetup | None = None,
        review: AIReview | None = None,
        risk: RiskApproval | None = None,
    ) -> Decision:
        return Decision(
            run_id=run_id,
            candidate_id=candidate.id,
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            decision_key=key,
            outcome=outcome,
            setup_fingerprint=setup.fingerprint() if setup else None,
            ai_review=review,
            risk=risk,
        )
