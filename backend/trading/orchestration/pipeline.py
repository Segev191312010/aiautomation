"""Compose scanner data, ORB rules, AI review, and risk into terminal decisions."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib

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
from trading.rules.orb import NO_SIGNAL_YET, QUOTE_REQUIRED, ORBConfig, evaluate_orb
from trading.scheduler.decision_keys import decision_key
from trading.storage.repository import record_decision


DecisionRecorder = Callable[..., Awaitable[None]]
_DATA_REJECTION_PREFIXES = (
    "bar_",
    "quote_",
    "opening_range_",
    "insufficient_atr_history",
)


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


def _is_data_rejection(reason: str) -> bool:
    return reason == "no bars supplied" or reason.startswith(_DATA_REJECTION_PREFIXES)


def entry_cutoff_utc(session_date: date, config: ORBConfig) -> datetime:
    from trading.rules.orb import NEW_YORK

    return datetime.combine(session_date, config.entry_cutoff, tzinfo=NEW_YORK).astimezone(
        timezone.utc
    )


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
) -> RiskApproval:
    now = datetime.now(timezone.utc)
    return RiskApproval(
        approved=False,
        codes=("risk_evaluation_failed",),
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
        orb_config: ORBConfig = ORBConfig(),
        mode: EngineMode = EngineMode.SHADOW,
    ) -> None:
        self._proposal_provider = proposal_provider
        self._decision_recorder = decision_recorder
        self._orb_config = orb_config
        self._mode = mode

        async def defer_recording(**_: object) -> None:
            return None

        self._engine = AutotraderEngine(decision_recorder=defer_recording)

    @property
    def proposal_provider(self) -> TradeProposalProvider:
        return self._proposal_provider

    @property
    def orb_config(self) -> ORBConfig:
        return self._orb_config

    @property
    def mode(self) -> EngineMode:
        return self._mode

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

        Before the entry cutoff, missing or transiently bad data leaves the
        candidate pending so a later poll can still see its breakout. At or
        after the cutoff every outcome is terminal and no new setup is built,
        so no entry can be created after the cutoff.
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

        cutoff_passed = observed >= entry_cutoff_utc(session_day, self._orb_config)
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
            return Assessment(decision=decision)

        orb_result = evaluate_orb(
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
            config=self._orb_config,
        )
        if orb_result.setup is None:
            reason = orb_result.rejection_reason or ""
            if reason == NO_SIGNAL_YET:
                return Assessment(decision=None, pending_reason=reason)
            if reason == QUOTE_REQUIRED:
                if cutoff_passed:
                    # The breakout was never priced inside the entry window.
                    return await terminal(DecisionOutcome.DATA_BLOCKED)
                return Assessment(decision=None, needs_quote=True, pending_reason=reason)
            if _is_data_rejection(reason):
                if not cutoff_passed:
                    return Assessment(decision=None, pending_reason=reason)
                return await terminal(DecisionOutcome.DATA_BLOCKED)
            return await terminal(DecisionOutcome.RULE_REJECTED)

        setup = orb_result.setup
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

        try:
            decision = await self._engine.assess(
                mode=self._mode,
                strategy=strategy,
                setup=setup,
                review=review,
                portfolio=portfolio,
                policy=policy,
                admitted_symbols=admitted_symbols,
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
            decision = Decision.model_validate(
                {
                    **decision.model_dump(mode="json"),
                    "decision_key": key,
                }
            )
        await self._record(decision, strategy.owner_id)
        return Assessment(decision=decision)

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
        return decision

    async def _record(self, decision: Decision, owner_id: str) -> None:
        await self._decision_recorder(decision=decision, owner_id=owner_id)

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
