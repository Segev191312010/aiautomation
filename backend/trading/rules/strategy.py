"""One boundary for every deterministic entry rule set.

The shadow pipeline knows only ``StrategyEvaluator``. Each implementation owns
its signal, its entry window, and which of its rejections a later poll may
resolve; the pipeline owns which outcomes become terminal decisions.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol, runtime_checkable

from trading.contracts import (
    ORB_STRATEGY_KIND,
    Candidate,
    CompletedBar,
    QuoteSnapshot,
    RuleSetup,
    require_aware,
)
from trading.rules.orb import NEW_YORK, NO_SIGNAL_YET, QUOTE_REQUIRED, ORBConfig, evaluate_orb

BAR_INTERVAL = timedelta(minutes=5)
NO_BARS_SUPPLIED = "no bars supplied"
# Missing or transiently bad market data: the candidate waits for a later poll
# inside the entry window and becomes DATA_BLOCKED at the cutoff.
DATA_REJECTION_PREFIXES: tuple[str, ...] = (
    "bar_",
    "quote_",
    "opening_range_",
    "insufficient_atr_history",
)


def is_data_rejection(reason: str) -> bool:
    return reason == NO_BARS_SUPPLIED or reason.startswith(DATA_REJECTION_PREFIXES)


@dataclass(frozen=True, slots=True)
class StrategyResult:
    """One evaluation: an immutable setup, or the reason there is none.

    ``retryable`` marks a rejection that a later poll inside the entry window
    may resolve, so the candidate stays pending instead of becoming terminal.
    """

    setup: RuleSetup | None
    rejection_reason: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        if self.setup is not None:
            if self.rejection_reason is not None or self.retryable:
                raise ValueError("an accepted strategy result cannot carry a rejection")
        elif self.rejection_reason is None or not self.rejection_reason.strip():
            raise ValueError("a strategy result without a setup requires a rejection reason")

    @property
    def accepted(self) -> bool:
        return self.setup is not None


@runtime_checkable
class StrategyEvaluator(Protocol):
    """Deterministic, long-only entry rules for one strategy kind."""

    @property
    def strategy_kind(self) -> str:
        """The ``RuleSetup.strategy_kind`` of every setup this strategy builds."""
        ...

    def entry_window(self, session_date: date) -> tuple[datetime, datetime]:
        """Return ``(start, cutoff)`` as aware UTC datetimes.

        ``start`` is the earliest moment a signal bar can complete; no setup
        may be built at or after ``cutoff``.
        """
        ...

    def evaluate(
        self,
        *,
        candidate: Candidate,
        bars: Sequence[CompletedBar],
        quote: QuoteSnapshot | None,
        session_date: date,
        run_id: str,
        strategy_id: str,
        strategy_version: int,
        reference_price: Decimal | int | str,
        average_dollar_volume: Decimal | int | str,
        candidate_input_fingerprint: str | None,
        now: datetime,
    ) -> StrategyResult:
        """Evaluate bars first; request a quote (``QUOTE_REQUIRED``) only on a signal."""
        ...


def entry_window_utc(strategy: StrategyEvaluator, session_date: date) -> tuple[datetime, datetime]:
    """The strategy's window, rejected unless timezone-aware and ordered."""
    start, cutoff = strategy.entry_window(session_date)
    start_utc = require_aware(start)
    cutoff_utc = require_aware(cutoff)
    if start_utc > cutoff_utc:
        raise ValueError("strategy entry window must not start after its cutoff")
    return start_utc, cutoff_utc


class ORBStrategy:
    """ORB_V1 behind the strategy boundary; ``evaluate_orb`` decides unchanged."""

    __slots__ = ("_config",)

    def __init__(self, config: ORBConfig = ORBConfig()) -> None:
        self._config = config

    @property
    def strategy_kind(self) -> str:
        return ORB_STRATEGY_KIND

    @property
    def config(self) -> ORBConfig:
        return self._config

    def entry_window(self, session_date: date) -> tuple[datetime, datetime]:
        opening_end = datetime.combine(
            session_date, self._config.opening_range_end, tzinfo=NEW_YORK
        ).astimezone(timezone.utc)
        cutoff = datetime.combine(
            session_date, self._config.entry_cutoff, tzinfo=NEW_YORK
        ).astimezone(timezone.utc)
        return opening_end + BAR_INTERVAL, cutoff

    def evaluate(
        self,
        *,
        candidate: Candidate,
        bars: Sequence[CompletedBar],
        quote: QuoteSnapshot | None,
        session_date: date,
        run_id: str,
        strategy_id: str,
        strategy_version: int,
        reference_price: Decimal | int | str,
        average_dollar_volume: Decimal | int | str,
        candidate_input_fingerprint: str | None,
        now: datetime,
    ) -> StrategyResult:
        result = evaluate_orb(
            candidate=candidate,
            bars=bars,
            quote=quote,
            session_date=session_date,
            run_id=run_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            reference_price=reference_price,
            average_dollar_volume=average_dollar_volume,
            candidate_input_fingerprint=candidate_input_fingerprint,
            now=now,
            config=self._config,
        )
        if result.setup is not None:
            return StrategyResult(setup=result.setup)
        reason = result.rejection_reason or "orb_setup_unavailable"
        before_cutoff = require_aware(now) < self.entry_window(session_date)[1]
        pending = reason in (NO_SIGNAL_YET, QUOTE_REQUIRED) or is_data_rejection(reason)
        return StrategyResult(
            setup=None,
            rejection_reason=reason,
            retryable=before_cutoff and pending,
        )


__all__ = [
    "BAR_INTERVAL",
    "DATA_REJECTION_PREFIXES",
    "NO_BARS_SUPPLIED",
    "ORBStrategy",
    "StrategyEvaluator",
    "StrategyResult",
    "entry_window_utc",
    "is_data_rejection",
]
