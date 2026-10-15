"""All-session shadow simulations (owner decision "Both", 2026-10-13).

``ORB_V2``, ``VWAP_PB_V2`` and ``GAP_GO_V2`` are ORB_V1, VWAP_PB_V1 and
GAP_GO_V1 with exactly one change: the entry window is a spec parameter,
``ALL_SESSION_WINDOW`` = signal bars ending 09:35-15:30 ET, and never later than
the close minus 30 minutes (12:30 on a half day). They exist to collect
evidence for every hour of the session; the replay and the nightly tracker
split that evidence by hour bucket, core window against the extra hours.

The V1 rule functions run unchanged: each V2 strategy writes its window into
the V1 config and calls the V1 evaluator, so the rules stay byte-identical by
construction (``WINDOW_FIELDS`` names the only fields that differ, and a
golden test replays recorded bars through both). A rule's own structural
start still binds before the window does: ORB's first signal bar ends at
09:50 (its 09:30-09:45 opening range must be complete) and VWAP_PB's at 09:55
(its four-bar pullback lookback plus the opening bar).

None of these names carries MOMO's prefix (checked when this module loads).
Whether one may send real PAPER orders is decided only by
``trading.sendable_identities.SENDABLE_IDENTITIES`` (bridge v7b grants ORB_V2,
VWAP_PB_V2 and GAP_GO_V2 at reduced size) and checked at launch.

``windowed_strategy`` builds the same rules behind any other window; the
replay's window research uses it to sweep candidate windows rules-only.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from trading.contracts import (
    ORB_STRATEGY_KIND,
    Candidate,
    CompletedBar,
    QuoteSnapshot,
    ScannerUniverse,
    StrategySpec,
    fingerprint_payload,
    require_aware,
)
from trading.rules.gap_go import (
    GAP_GO_RULES_VERSION,
    GAP_GO_STRATEGY_KIND,
    GapGoConfig,
    evaluate_gap_go,
    gap_go_entry_window,
    gap_go_strategy_spec,
)
from trading.rules.momo import (
    MOMO_RULES_VERSION,
    MOMO_STRATEGY_KIND,
    MomoConfig,
    MomoStrategy,
    SessionCloseLookup,
    xnys_session_close,
)
from trading.rules.orb import NEW_YORK, ORBConfig
from trading.rules.strategy import (
    BAR_INTERVAL,
    ORBStrategy,
    StrategyEvaluator,
    StrategyResult,
)
from trading.rules.vwap_pb import (
    VWAP_PB_RULES_VERSION,
    VWAP_PB_STRATEGY_KIND,
    VwapPbConfig,
    VwapPbStrategy,
    vwap_pb_strategy_spec,
)
from trading.sendable_identities import carries_sendable_prefix

UTC = timezone.utc
_REGULAR_OPEN = time(9, 30)
_FIRST_BAR_CLOSE = time(9, 35)
_REGULAR_CLOSE = time(16, 0)
# Any fixed day: wall-clock comparisons and window labels use it.
_ANCHOR_DAY = date(2000, 1, 3)
ORB_V1_RULES_VERSION = "ORB_V1"


@dataclass(frozen=True, slots=True)
class EntryWindow:
    """When a signal bar may end, as a strategy-spec parameter.

    ``start`` and ``cutoff`` are New York wall-clock times on bar boundaries.
    On a session that closes early the cutoff moves in to ``close -
    close_buffer``, so entries keep that much runway before the close.
    """

    start: time
    cutoff: time
    close_buffer: timedelta = timedelta(minutes=30)

    def __post_init__(self) -> None:
        if not _FIRST_BAR_CLOSE <= self.start < self.cutoff <= _REGULAR_CLOSE:
            raise ValueError("an entry window must start before its cutoff inside regular hours")
        for moment in (self.start, self.cutoff):
            if moment.minute % 5 or moment.second or moment.microsecond or moment.tzinfo:
                raise ValueError("entry window bounds must be naive times on five-minute boundaries")
        if self.close_buffer < timedelta(0) or self.close_buffer % BAR_INTERVAL:
            raise ValueError("close_buffer must be a non-negative whole number of bars")

    def cutoff_at(self, session_date: date, *, session_close: datetime | None) -> datetime:
        """The cutoff on ``session_date`` in UTC: the earlier of the cutoff and close - buffer."""
        cutoff = _at(session_date, self.cutoff)
        if session_close is not None:
            cutoff = min(cutoff, require_aware(session_close) - self.close_buffer)
        return max(_at(session_date, self.start), cutoff)

    def cutoff_time(self, session_date: date, *, session_close: datetime | None) -> time:
        """``cutoff_at`` as the New York wall-clock time a V1 config takes."""
        return self.cutoff_at(session_date, session_close=session_close).astimezone(NEW_YORK).time()

    def label(self) -> str:
        return f"{self.start:%H:%M}-{self.cutoff:%H:%M}"

    def payload(self) -> dict[str, object]:
        return {
            "start": f"{self.start:%H:%M}",
            "cutoff": f"{self.cutoff:%H:%M}",
            "close_buffer_minutes": int(self.close_buffer.total_seconds() // 60),
        }

    def fingerprint(self) -> str:
        return fingerprint_payload({"entry_window": self.payload()})


# The owner's all-session window for shadow evidence (spec, "Hours").
ALL_SESSION_WINDOW = EntryWindow(start=time(9, 35), cutoff=time(15, 30))

# Each V1 strategy's own window in the same terms; ``windowed_strategy`` on
# these rebuilds exactly the V1 configuration (a test pins it).
V1_WINDOWS: dict[str, EntryWindow] = {
    ORB_STRATEGY_KIND: EntryWindow(start=time(9, 50), cutoff=time(12, 0), close_buffer=timedelta(0)),
    VWAP_PB_STRATEGY_KIND: EntryWindow(
        start=time(10, 30), cutoff=time(14, 30), close_buffer=timedelta(hours=1)
    ),
    GAP_GO_STRATEGY_KIND: EntryWindow(start=time(9, 35), cutoff=time(10, 30), close_buffer=timedelta(0)),
    MOMO_STRATEGY_KIND: EntryWindow(start=time(10, 0), cutoff=time(15, 0), close_buffer=timedelta(hours=1)),
}

# The only configuration fields a window sets; every other field is the V1 rule.
WINDOW_FIELDS: dict[str, frozenset[str]] = {
    ORB_STRATEGY_KIND: frozenset({"entry_cutoff"}),
    VWAP_PB_STRATEGY_KIND: frozenset({"window_start", "entry_cutoff", "close_buffer"}),
    GAP_GO_STRATEGY_KIND: frozenset({"window_start", "entry_cutoff"}),
    MOMO_STRATEGY_KIND: frozenset({"window_start", "entry_cutoff", "close_buffer"}),
}
# The V1 rule set each kind's rules come from.
BASE_RULES_VERSIONS: dict[str, str] = {
    ORB_STRATEGY_KIND: ORB_V1_RULES_VERSION,
    VWAP_PB_STRATEGY_KIND: VWAP_PB_RULES_VERSION,
    GAP_GO_STRATEGY_KIND: GAP_GO_RULES_VERSION,
    MOMO_STRATEGY_KIND: MOMO_RULES_VERSION,
}
_DEFAULT_CONFIGS: dict[str, object] = {
    ORB_STRATEGY_KIND: ORBConfig(),
    VWAP_PB_STRATEGY_KIND: VwapPbConfig(),
    GAP_GO_STRATEGY_KIND: GapGoConfig(),
    MOMO_STRATEGY_KIND: MomoConfig(),
}

ORB_V2_NAME = "ORB_V2"
VWAP_PB_V2_NAME = "VWAP_PB_V2"
GAP_GO_V2_NAME = "GAP_GO_V2"
ALL_SESSION_STRATEGY_NAMES: tuple[str, ...] = (ORB_V2_NAME, VWAP_PB_V2_NAME, GAP_GO_V2_NAME)
GAP_REVIEW_V2_PROMPT_ID = "gap-review-v2"


def _at(session_date: date, local_time: time) -> datetime:
    return datetime.combine(session_date, local_time, tzinfo=NEW_YORK).astimezone(UTC)


def orb_earliest_signal_end(config: ORBConfig = ORBConfig()) -> time:
    """ORB's first signal bar ends one bar after its opening range closes."""
    return (datetime.combine(_ANCHOR_DAY, config.opening_range_end) + BAR_INTERVAL).time()


def vwap_pb_earliest_signal_end(config: VwapPbConfig = VwapPbConfig()) -> time:
    """VWAP_PB's first signal bar needs its pullback lookback plus the opening bar."""
    return (
        datetime.combine(_ANCHOR_DAY, _REGULAR_OPEN) + BAR_INTERVAL * (config.pullback_bars + 1)
    ).time()


def _rules_payload(kind: str) -> dict[str, str]:
    config = _DEFAULT_CONFIGS[kind]
    excluded = WINDOW_FIELDS[kind]
    return {
        item.name: str(getattr(config, item.name))
        for item in fields(config)  # type: ignore[arg-type]
        if item.name not in excluded
    }


def rules_fingerprint(kind: str) -> str:
    """Identity of a kind's V1 rules without their window (evidence identity, part one)."""
    if kind not in _DEFAULT_CONFIGS:
        raise ValueError(f"unknown strategy kind {kind!r}")
    return fingerprint_payload(
        {
            "strategy_kind": kind,
            "base_rules_version": BASE_RULES_VERSIONS[kind],
            "rules": _rules_payload(kind),
        }
    )


class _PerSessionWindowStrategy:
    """A V1 rule set whose config takes a fixed cutoff (ORB, GAP_GO).

    The window's cutoff is resolved against each session's close, so a half
    day's cutoff moves in exactly as for strategies with a calendar lookup.
    """

    __slots__ = ("_kind", "_window", "_session_close", "_configs")

    def __init__(
        self,
        kind: str,
        window: EntryWindow,
        *,
        session_close: SessionCloseLookup = xnys_session_close,
    ) -> None:
        if kind == ORB_STRATEGY_KIND and window.start > orb_earliest_signal_end():
            raise ValueError("ORB's window starts with its first signal bar (09:50 ET) at the latest")
        if kind not in (ORB_STRATEGY_KIND, GAP_GO_STRATEGY_KIND):
            raise ValueError(f"{kind!r} takes its window in a static config")
        self._kind = kind
        self._window = window
        self._session_close = session_close
        self._configs: dict[date, Any] = {}

    @property
    def strategy_kind(self) -> str:
        return self._kind

    @property
    def window(self) -> EntryWindow:
        return self._window

    def config_for(self, session_date: date) -> ORBConfig | GapGoConfig:
        config = self._configs.get(session_date)
        if config is None:
            cutoff = self._window.cutoff_time(
                session_date, session_close=self._session_close(session_date)
            )
            if self._kind == ORB_STRATEGY_KIND:
                config = replace(ORBConfig(), entry_cutoff=cutoff)
            else:
                config = replace(GapGoConfig(), window_start=self._window.start, entry_cutoff=cutoff)
            self._configs[session_date] = config
        return config

    def entry_window(self, session_date: date) -> tuple[datetime, datetime]:
        config = self.config_for(session_date)
        if isinstance(config, ORBConfig):
            return ORBStrategy(config).entry_window(session_date)
        return gap_go_entry_window(session_date, config)

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
        config = self.config_for(session_date)
        if isinstance(config, ORBConfig):
            return ORBStrategy(config).evaluate(
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
            )
        return evaluate_gap_go(
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
            config=config,
        ).as_strategy_result()


def windowed_strategy(
    kind: str,
    window: EntryWindow,
    *,
    session_close: SessionCloseLookup = xnys_session_close,
) -> StrategyEvaluator:
    """``kind``'s V1 rules behind ``window`` (each rule's structural start still binds)."""
    if kind in (ORB_STRATEGY_KIND, GAP_GO_STRATEGY_KIND):
        return _PerSessionWindowStrategy(kind, window, session_close=session_close)
    if kind == VWAP_PB_STRATEGY_KIND:
        config = replace(
            VwapPbConfig(),
            window_start=max(window.start, vwap_pb_earliest_signal_end()),
            entry_cutoff=window.cutoff,
            close_buffer=window.close_buffer,
        )
        return VwapPbStrategy(config, session_close=session_close)
    if kind == MOMO_STRATEGY_KIND:
        config = replace(
            MomoConfig(),
            window_start=window.start,
            entry_cutoff=window.cutoff,
            close_buffer=window.close_buffer,
        )
        return MomoStrategy(config, session_close=session_close)
    raise ValueError(f"unknown strategy kind {kind!r}")


class AllSessionStrategy:
    """One V2 simulation: a V1 rule set behind ``ALL_SESSION_WINDOW``."""

    __slots__ = ("_name", "_kind", "_inner")

    def __init__(
        self,
        name: str,
        kind: str,
        *,
        session_close: SessionCloseLookup = xnys_session_close,
    ) -> None:
        # A V2 simulation never wears MOMO's name: MOMO_HOD* is MOMO's rule set
        # and its legacy prefix guard. Whether a V2 name may send real PAPER
        # orders is decided by trading.sendable_identities.SENDABLE_IDENTITIES
        # and refused at launch by shadow_service.registry_problems, not here.
        if carries_sendable_prefix(name):
            raise ValueError(f"{name!r} carries MOMO's name; V2 simulations never do (PAPER bridge rule set)")
        self._name = name
        self._kind = kind
        self._inner = windowed_strategy(kind, ALL_SESSION_WINDOW, session_close=session_close)

    @property
    def strategy_kind(self) -> str:
        return self._kind

    @property
    def rules_version(self) -> str:
        return self._name

    @property
    def window(self) -> EntryWindow:
        return ALL_SESSION_WINDOW

    @property
    def inner(self) -> StrategyEvaluator:
        return self._inner

    def entry_window(self, session_date: date) -> tuple[datetime, datetime]:
        return self._inner.entry_window(session_date)

    def evaluate(self, **kwargs: Any) -> StrategyResult:
        return self._inner.evaluate(**kwargs)

    def policy_fingerprints(self) -> dict[str, str]:
        """The run's evidence identity parts: V1 rules without their window, plus the window."""
        return {
            "rules_version": self._name,
            "rules": rules_fingerprint(self._kind),
            "entry_window": ALL_SESSION_WINDOW.fingerprint(),
        }


def orb_v2_strategy() -> AllSessionStrategy:
    return AllSessionStrategy(ORB_V2_NAME, ORB_STRATEGY_KIND)


def vwap_pb_v2_strategy() -> AllSessionStrategy:
    return AllSessionStrategy(VWAP_PB_V2_NAME, VWAP_PB_STRATEGY_KIND)


def gap_go_v2_strategy() -> AllSessionStrategy:
    return AllSessionStrategy(GAP_GO_V2_NAME, GAP_GO_STRATEGY_KIND)


ALL_SESSION_FACTORIES: dict[str, Callable[[], AllSessionStrategy]] = {
    ORB_V2_NAME: orb_v2_strategy,
    VWAP_PB_V2_NAME: vwap_pb_v2_strategy,
    GAP_GO_V2_NAME: gap_go_v2_strategy,
}

_WINDOW_TEXT = (
    "Entries: signal bars ending 09:35-15:30 ET (close minus 30 minutes on a half day)"
)


def orb_v2_strategy_spec() -> StrategySpec:
    """ORB_V2's catalogue entry: ORB_V1's scan and rules, the all-session window."""
    # Imported on use: the orchestration layer builds ORB_V1's scanner policy.
    from trading.orchestration.session_runner import orb_strategy_spec

    return StrategySpec(
        name=ORB_V2_NAME,
        universe=orb_strategy_spec().universe,
        conditions=orb_strategy_spec().conditions,
        rationale=(
            "Shadow only, never sent. ORB_V1's rules unchanged (long-only 09:30-09:45 "
            "opening-range breakout on the IBKR HOT_BY_VOLUME scan; stop one ATR(14) below "
            f"entry, 2R target). {_WINDOW_TEXT}; the first signal bar ends 09:50. "
            "Authoritative rules: trading/rules/all_session.py and trading/rules/orb.py ORBConfig."
        ),
    )


def vwap_pb_v2_strategy_spec() -> StrategySpec:
    """VWAP_PB_V2's catalogue entry: VWAP_PB_V1's hot list and rules, the all-session window."""
    base = vwap_pb_strategy_spec()
    return StrategySpec(
        name=VWAP_PB_V2_NAME,
        universe=base.universe,
        conditions=base.conditions,
        rationale=(
            "Shadow only, never sent. VWAP_PB_V1's rules unchanged (long-only VWAP pullback "
            "reclaim on a trend day; stop 0.1 ATR(14) under the pullback low, skipped outside "
            f"0.5%-1.5% of entry; 2R target). {_WINDOW_TEXT}; the first signal bar ends 09:55. "
            "Authoritative rules: trading/rules/all_session.py and trading/rules/vwap_pb.py "
            "VwapPbConfig."
        ),
    )


# The scanner half of GAP_GO_V2's universe: names the IBKR gap scans held for
# the day (autotrader_manager_day_universe), next to the watchlist gappers.
GAP_UNION_POLICY = {
    "source": "screener_snapshot",
    "watchlist_gappers": True,
    "scanner_gappers": "day_universe",
    "min_gap_pct": "3",
}


def gap_go_v2_strategy_spec() -> StrategySpec:
    """GAP_GO_V2's catalogue entry: watchlist and scanner gappers, the all-session window."""
    base = gap_go_strategy_spec()
    return StrategySpec(
        name=GAP_GO_V2_NAME,
        universe=ScannerUniverse(
            scanner_policy_id="screener-gappers-with-day-universe",
            scanner_policy_version=1,
            scanner_policy_hash=fingerprint_payload(GAP_UNION_POLICY),
        ),
        conditions=base.conditions,
        rationale=(
            "Shadow only, never sent. GAP_GO_V1's rules unchanged (the first 5-minute bar opens "
            "at least 3% above the prior close after pre-market volume of at least 2% of average "
            "daily shares; the first close above the higher of the pre-market high and the first "
            "bar's high signals; stop the higher of the first bar's low and one ATR(14) below "
            f"entry, skipped outside 0.5%-2.0%; 2R target). {_WINDOW_TEXT}. Universe: watchlist "
            "gappers and scanner gappers. Authoritative rules: trading/rules/all_session.py and "
            "trading/rules/gap_go.py GapGoConfig."
        ),
    )


ALL_SESSION_SPECS: dict[str, Callable[[], StrategySpec]] = {
    ORB_V2_NAME: orb_v2_strategy_spec,
    VWAP_PB_V2_NAME: vwap_pb_v2_strategy_spec,
    GAP_GO_V2_NAME: gap_go_v2_strategy_spec,
}


# Review prompt for GAP_GO_V2 setups, registered as ``gap-review-v2``.
# gap-review-v1 states GAP_GO_V1's 09:35-10:30 window, which a V2 setup at
# 13:00 would contradict. Once a recorded review names this id the text must
# never change; new wording needs a new prompt id.
GAP_REVIEW_V2 = """\
You review one long-only gap-and-go setup (strategy_kind "gap_go", rules \
GAP_GO_V2: GAP_GO_V1's rules with an all-session entry window) for a US common \
stock during regular trading hours. Deterministic code has already detected \
the signal: the session's first five-minute bar opened at least 3% above the \
prior close, pre-market volume (04:00 to 09:30 ET) was at least 2% of the \
stock's average daily share volume, and the latest completed five-minute bar, \
ending between 09:35 and 15:30 ET (12:30 on a half day), is the first bar of \
the session to close above the trigger, the higher of the pre-market high and \
the first five-minute bar's high. A later re-break never signals, so a signal \
late in the session means the stock held below the trigger until then. Risk \
sizing, account limits, and order submission are owned by deterministic code \
and are not your concern.

Code computed the entry limit as the higher of the signal bar's close and the \
ask, rounded up to the tick and at most 0.2% above that close. The stop is the \
higher of the first five-minute bar's low and the entry minus one ATR(14) on \
five-minute bars, rounded down to the tick, and the target sits twice the \
rounded stop distance above the entry (2R). Code skips any signal whose stop \
distance is below 0.5% or above 2.0% of the entry, so every setup you see \
passed both checks.

This setup has no opening range. Its levels are reference values only: gap_pct \
is the first bar's open above the prior close, in percent; premarket_high is \
the highest pre-market trade; premarket_volume is the shares traded before the \
open; first_bar_high and first_bar_low are the first five-minute bar's range; \
trigger is the breakout level; stop_distance is entry_limit minus stop_price, \
per share.

The request may also carry market_context, a snapshot of the broad market at \
review time: percent changes on the day for SPY, QQQ, IWM and DIA, breadth \
(the share of stocks trading up, advancers, decliners, and the median change), \
the leading and lagging sectors, a regime of risk_on, neutral or risk_off, and \
a one-line summary. Treat a weak or risk-off market as a reason for caution: \
when the setup is marginal, ABSTAIN. When market_context is absent or \
{"available": false}, judge the setup on its own.

Decide PROPOSE if the setup is a reasonable trade as specified, or ABSTAIN if \
anything about it looks unsound: for example a gap so large that the target \
needs an implausible further move, an entry stretched far above the trigger, \
pre-market volume too thin to support the gap, a first bar whose range dwarfs \
the stop distance, a target the remaining session leaves too little time to \
reach, or internally inconsistent prices or levels. When the evidence is \
ambiguous, ABSTAIN.

You cannot change any price, quantity, symbol, or order field. Respond only with \
the requested JSON: decision, confidence between 0 and 1, and a short rationale \
that names the specific values behind your decision."""


def _never_momo_named(names: Sequence[str]) -> None:
    """Load-time tripwire: an all-session name carrying MOMO's prefix is a bug.

    Until bridge v7 this also refused any name in SENDABLE_IDENTITIES; the owner's
    grant (2026-10-14/15) makes ORB_V2, VWAP_PB_V2 and GAP_GO_V2 sendable, so the
    sendable set is now checked against the registry at launch instead.
    """
    for name in names:
        if carries_sendable_prefix(name):
            raise RuntimeError(f"all-session simulation {name!r} carries MOMO's name")


_never_momo_named(ALL_SESSION_STRATEGY_NAMES)


__all__ = [
    "ALL_SESSION_FACTORIES",
    "ALL_SESSION_SPECS",
    "ALL_SESSION_STRATEGY_NAMES",
    "ALL_SESSION_WINDOW",
    "BASE_RULES_VERSIONS",
    "GAP_GO_V2_NAME",
    "GAP_REVIEW_V2",
    "GAP_REVIEW_V2_PROMPT_ID",
    "GAP_UNION_POLICY",
    "ORB_V1_RULES_VERSION",
    "ORB_V2_NAME",
    "V1_WINDOWS",
    "VWAP_PB_V2_NAME",
    "WINDOW_FIELDS",
    "AllSessionStrategy",
    "EntryWindow",
    "gap_go_v2_strategy",
    "gap_go_v2_strategy_spec",
    "orb_earliest_signal_end",
    "orb_v2_strategy",
    "orb_v2_strategy_spec",
    "rules_fingerprint",
    "vwap_pb_earliest_signal_end",
    "vwap_pb_v2_strategy",
    "vwap_pb_v2_strategy_spec",
    "windowed_strategy",
]
