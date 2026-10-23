"""Composition root for one real-data shadow strategy session.

Shadow mode reads IBKR market data and the PAPER account, asks Claude for a
review, and records decisions. It holds no order client and cannot submit.
The supervisor (``trading.operations.supervisor``) decides when it runs.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import logging
import os
from pathlib import Path
import socket
import subprocess
from typing import TYPE_CHECKING, Protocol

from trading.contracts import RiskPolicy, ShadowRuntimeConfig
from trading.intelligence.anthropic_transport import (
    AnthropicMessagesTransport,
    MarketContextProvider,
    build_anthropic_client,
)
from trading.intelligence.prompts import (
    MOMO_MANAGED_REVIEW_PROMPT_ID,
    review_prompt,
    review_prompt_for,
)
from trading.intelligence.provider import TradeProposalProvider
from trading.intelligence.review_provider import ReviewSessionLimits, StructuredAIReviewProvider
from trading.market import bar_cache
from trading.market.ibkr_boundary import IBKRConnectionActor, PaperIBKRMarketData
from trading.market.tws_actor import (
    PAPER_TWS_HOST,
    TWSActorConfig,
    TWSPaperActor,
    documented_line_allocation,
)
from trading.operations.keychain import read_anthropic_api_key
from trading.orchestration import (
    RepositoryBackedShadowRunner,
    ShadowPersistenceConflictError,
    ShadowPipeline,
)
from trading.orchestration.session_runner import (
    BAR_INTERVAL,
    MIN_PASS_BUDGET,
    ap_momo_strategy_spec,
    momo_strategy_spec,
    orb_strategy_spec,
    ensure_strategy,
    EntryGate,
    PollSchedule,
    SessionAborted,
    SessionReport,
    ShadowSessionRunner,
)
from trading.orchestration.watchlist_source import (
    gap_candidates,
    gap_union_candidates,
    hotlist_candidates,
    hotlist_refresh,
)
from trading.policies import ExecutionPolicy, OpeningRangeBreakoutPolicy
from trading.rules.all_session import (
    GAP_REVIEW_V2_PROMPT_ID,
    gap_go_v2_strategy,
    gap_go_v2_strategy_spec,
    orb_v2_strategy,
    orb_v2_strategy_spec,
    vwap_pb_v2_strategy,
    vwap_pb_v2_strategy_spec,
)
from trading.rules.gap_go import (
    GAP_GO_STRATEGY_KIND,
    GAP_REVIEW_PROMPT_ID,
    GapGoConfig,
    GapGoStrategy,
    gap_go_bars,
    gap_go_strategy_spec,
)
from trading.rules.momo import MomoConfig, MomoStrategy
from trading.rules.vwap_pb import (
    VWAP_REVIEW_PROMPT_ID,
    VwapPbConfig,
    VwapPbStrategy,
    vwap_pb_strategy_spec,
)
from trading.rules.strategy import ORBStrategy, StrategyEvaluator, entry_window_utc
from trading.scheduler.sessions import (
    MARKET_TIMEZONE,
    MarketSession,
    market_session,
    next_market_session,
)
from trading.risk.identity_sizing import IDENTITY_SIZE_MULTIPLIER, identity_multiplier
from trading.risk.no_limit_test import NO_LIMIT_TEST_LABEL, run_policy
from trading.sendable_identities import SENDABLE_IDENTITIES, is_sendable_identity

if TYPE_CHECKING:
    from trading.manager.managed import ManagedStrategy
    from trading.market.context import MarketContext

logger = logging.getLogger("autotrader.shadow.service")

# Connect early enough to finish the handshake and preflight before the scan.
CONNECT_LEAD = timedelta(minutes=5)
# ibapi 9.81 reports US-stock volume in round lots of 100 shares. Verified
# 2026-10-12 by the TWS probe: AAPL daily bars returned ~500k against ~50M
# shares actually traded.
IBKR_API_VOLUME_MULTIPLIER = Decimal("100")
REPO_ROOT = Path(__file__).resolve().parents[3]

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def source_sha(repo_root: Path = REPO_ROOT) -> str:
    """The exact code a run used; uncommitted changes are marked, not hidden."""
    try:
        head = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=no"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown-source"
    return f"{head}-dirty" if dirty else head


# No fallback: momo-review-v1 describes the old 1x ATR stop and would mislead
# the reviewer on every V2 setup, so a missing v2 prompt fails the session start.
MOMO_REVIEW_PROMPT_ID = "momo-review-v2"

# Strategy name -> (evaluator factory, catalogue spec factory, review prompt id).
# ORB keeps the configured prompt so its run fingerprints are stable.
STRATEGIES = {
    "orb": (ORBStrategy, orb_strategy_spec, None),
    "momo": (MomoStrategy, momo_strategy_spec, MOMO_REVIEW_PROMPT_ID),
    # Shadow strategies: their catalogue names are not sendable identities, so
    # the paper bridge records but never sends their decisions.
    "vwap": (VwapPbStrategy, vwap_pb_strategy_spec, VWAP_REVIEW_PROMPT_ID),
    "gap": (GapGoStrategy, gap_go_strategy_spec, GAP_REVIEW_PROMPT_ID),
    # All-session simulations (owner decision "Both", 2026-10-13): the V1 rules
    # with entries 09:35-15:30 ET (trading/rules/all_session.py). They send
    # real PAPER orders only once their names are in SENDABLE_IDENTITIES and
    # their keys in SENDABLE_KEYS together (bridge v7b); one without the other
    # is a launch refusal (registry_problems), never a silent shadow run.
    "orb-v2": (orb_v2_strategy, orb_v2_strategy_spec, "orb-review-v1"),
    "vwap-v2": (vwap_pb_v2_strategy, vwap_pb_v2_strategy_spec, VWAP_REVIEW_PROMPT_ID),
    "gap-v2": (gap_go_v2_strategy, gap_go_v2_strategy_spec, GAP_REVIEW_V2_PROMPT_ID),
    # Managed MOMO (shadow, AI day manager): MOMO_HOD_V2's rules, and V2's
    # prompt for its neutral reviews. Never sendable (see AP_MOMO_STRATEGY_NAME).
    "momo-ap": (MomoStrategy, ap_momo_strategy_spec, MOMO_REVIEW_PROMPT_ID),
}
# Registry keys whose spec name is a sendable identity: the children that send
# real PAPER orders through the bridge. Every other key's name must stay out
# of SENDABLE_IDENTITIES (registry_problems refuses the launch otherwise).
SENDABLE_KEYS = frozenset({"momo", "orb-v2", "vwap-v2", "gap-v2"})


def registered_spec_names(
    strategies: Mapping[str, tuple[object, Callable[[], object], object]] = STRATEGIES,
) -> dict[str, str]:
    """Registry key -> the catalogue name its spec factory builds."""
    return {key: str(factory().name) for key, (_evaluator, factory, _prompt) in strategies.items()}


def registry_problems(
    *,
    sendable: tuple[str, ...] = SENDABLE_IDENTITIES,
    multipliers: Mapping[str, Decimal] = IDENTITY_SIZE_MULTIPLIER,
    strategies: Mapping[str, tuple[object, Callable[[], object], object]] = STRATEGIES,
    sendable_keys: frozenset[str] = SENDABLE_KEYS,
) -> list[str]:
    """Every way the registry, the sendable tuple and the size table disagree.

    Empty when: spec names are unique; exactly the ``sendable_keys`` carry a
    sendable name; every sendable name has a size row and every size row is a
    sendable name; every sendable name is registered. Each problem is one
    token without spaces, so a ``LAUNCH_REFUSED ... reason=<token>`` line
    reaches the phone intact. The supervisor refuses to launch on any of them.
    """
    names = registered_spec_names(strategies)
    problems: list[str] = []
    seen: dict[str, str] = {}
    for key, name in names.items():
        if name in seen:
            problems.append(f"duplicate_spec_name={name}:{seen[name]}+{key}")
        seen.setdefault(name, key)
    for key, name in names.items():
        if name in sendable and key not in sendable_keys:
            problems.append(f"sendable_name_under_unlisted_key={key}:{name}")
        if key in sendable_keys and name not in sendable:
            problems.append(f"sendable_key_without_sendable_name={key}:{name}")
    for name in sendable:
        if name not in multipliers:
            problems.append(f"sendable_without_size_multiplier={name}")
        if name not in names.values():
            problems.append(f"sendable_not_registered={name}")
    for name in multipliers:
        if name not in sendable:
            problems.append(f"size_multiplier_not_sendable={name}")
    return problems


# Each registry entry's family: the key every "momo"-style check reads
# (risk_off gate, market context, MomoConfig fingerprint), so managed MOMO
# keeps MOMO's gates. An entry not listed is its own family.
STRATEGY_FAMILIES = {"momo-ap": "momo"}
# Entries run under the AI day manager (trading.manager.managed).
MANAGED_STRATEGIES = frozenset({"momo-ap"})
# "all-gappers": the watchlist gappers plus the day universe's scanner gappers.
UNIVERSES = ("scanner", "watchlist", "gappers", "all-gappers")
# Universes whose runs add symbols that heat up after the window opens. The
# hot list moves all day; gappers are an opening set and ORB keeps its scan.
DYNAMIC_UNIVERSES = frozenset({"watchlist"})
# TWS accepts one connection per client ID. Strategy sessions run concurrently,
# so each gets its own data client: data_client_id + offset (the screener uses
# data_client_id + 2 and the order bridge uses order_client_id).
# Client map with data 17 / orders 18: orb 17, screener 19, momo 20, telegram 21,
# vwap 22, gap 23, momo-ap 24 (research and probes use 25-29). Each V2
# simulation replaces its V1 child in the supervisor's table and reuses that
# client id: the two are never scheduled together. The manager child holds no
# TWS client.
STRATEGY_CLIENT_OFFSET = {
    "orb": 0,
    "momo": 3,
    "vwap": 5,
    "gap": 6,
    "orb-v2": 0,
    "vwap-v2": 5,
    "gap-v2": 6,
    "momo-ap": 7,
}
# A hot-list session fetches bars for up to 35 symbols one after another (~90 s
# live), so the newest completed bar is legitimately older than the 30 s
# single-symbol limit. Anything under one bar interval still guarantees the
# newest bar is the latest completed one.
WATCHLIST_BAR_MAX_AGE = timedelta(seconds=240)
# Managed MOMO polls last (+165 s), after VWAP_PB_V2's whole pass; its bars may
# be up to 280 s old, still under one bar interval (the newest bar is always the
# latest completed one), which leaves a 115 s pass for the hot list.
MANAGED_BAR_MAX_AGE = timedelta(seconds=280)
# IBKR refuses a historical-data request identical to one made within the last
# 15 s by any client of the same TWS (a pacing violation), and children that
# share symbols send identical bar requests. Their polls stay this far apart.
MIN_POLL_SEPARATION = timedelta(seconds=15)
# Strategy -> poll delay after every bar close. Every sendable child polls
# before every shadow child (poll_offset_problems refuses anything else), so a
# pacing collision costs a shadow child's later request, never a real order's.
# Real MOMO polls first; the sendable all-session children (bridge v7b) keep
# the reviewed slots they have polled in since 2026-10-13 (VWAP_PB_V2 reads
# MOMO's own hot list and polls last of the senders, after MOMO's whole pass
# at live request rates); the V1 strategies, registered for the CLI and the
# replay but not scheduled, moved behind them. Each delay also gives IBKR
# time to finalize the closed bar.
STRATEGY_POLL_OFFSETS = {
    "momo": timedelta(seconds=15),
    "orb-v2": timedelta(seconds=75),
    "gap-v2": timedelta(seconds=90),
    "vwap-v2": timedelta(seconds=105),
    "orb": timedelta(seconds=120),
    "gap": timedelta(seconds=135),
    "vwap": timedelta(seconds=150),
    # Managed MOMO reads MOMO's own hot list, after every other child and
    # after VWAP_PB_V2's own pass (+105 s, up to 45 names): an identical
    # request within 15 s of another child's is refused by IBKR. Its 280 s
    # freshness limit leaves a 115 s pass (MANAGED_BAR_MAX_AGE).
    "momo-ap": timedelta(seconds=165),
}
# Registry entry -> whether its five-minute history is read through the
# shared bar cache (trading.market.bar_cache) once BAR_CACHE_ENABLED is True.
# Every entry opts out until the cache has its own review: the table is an
# unregistered draft, and real MOMO's requests stay direct in any case (a
# batch another child fetched is not the reply its own request would get,
# and its decisions are frozen). An entry missing here opts out.
STRATEGY_USES_BAR_CACHE = {
    "orb": False,
    "momo": False,
    "vwap": False,
    "gap": False,
    "orb-v2": False,
    "vwap-v2": False,
    "gap-v2": False,
    "momo-ap": False,
}


def uses_bar_cache(strategy_name: str) -> bool:
    """Whether the registry entry opted into the shared bar cache."""
    return bool(STRATEGY_USES_BAR_CACHE.get(strategy_name, False))


def bar_cache_active(strategy_name: str) -> bool:
    """The cache serves a child only with the flag on and its entry opted in."""
    return bool(bar_cache.BAR_CACHE_ENABLED) and uses_bar_cache(strategy_name)


def strategy_family(strategy_name: str) -> str:
    """The family a registry entry's gates and fingerprints follow."""
    return STRATEGY_FAMILIES.get(strategy_name, strategy_name)


# Families whose reviews see the screener's market context and whose entries
# pause while the market is risk_off. ORB is left out so its runs (and run
# fingerprints) are unchanged.
MARKET_AWARE_FAMILIES = frozenset({"momo"})
MARKET_AWARE_STRATEGIES = frozenset(
    name for name in STRATEGIES if strategy_family(name) in MARKET_AWARE_FAMILIES
)
MARKET_REGIME_RULE = "momo pauses in risk_off"
# The screener writes a context about every 80 s; anything older than this is
# treated as no context (reviews see it as unavailable, the gate stays open).
MARKET_CONTEXT_MAX_AGE = timedelta(minutes=5)


class MarketContextReader(Protocol):
    async def __call__(
        self, session_date: date, *, now: datetime, max_age: timedelta
    ) -> MarketContext | None: ...


async def _read_market_context(
    session_date: date, *, now: datetime, max_age: timedelta
) -> MarketContext | None:
    # Imported on use: the screener's market package owns this read, and a
    # strategy session must start even if that module fails to import (every
    # caller treats an error as "no context").
    from trading.market.context import latest_market_context

    return await latest_market_context(session_date, now=now, max_age=max_age)


def market_context_provider(
    session_date: date, *, clock: Clock, reader: MarketContextReader
) -> MarketContextProvider:
    """The newest fresh context as the review's ``market_context``, else None."""

    async def provide() -> Mapping[str, object] | None:
        context = await reader(session_date, now=clock(), max_age=MARKET_CONTEXT_MAX_AGE)
        return None if context is None else context.to_payload()

    return provide


def risk_off_entry_gate(
    session_date: date, *, clock: Clock, reader: MarketContextReader
) -> EntryGate:
    """Block entries only while a fresh context says ``risk_off``.

    No fresh context leaves entries open: the regime is an extra caution on top
    of the rules and the risk engine, not a precondition for trading.
    """

    async def gate() -> tuple[bool, str]:
        context = await reader(session_date, now=clock(), max_age=MARKET_CONTEXT_MAX_AGE)
        if context is None:
            return True, "no fresh market context"
        if context.regime == "risk_off":
            return False, context.summary
        return True, context.summary

    return gate


def policy_fingerprints(
    config: ShadowRuntimeConfig,
    policy: RiskPolicy,
    *,
    prompt_id: str | None = None,
    strategy_name: str = "orb",
) -> dict[str, str]:
    prompt = review_prompt(prompt_id or config.prompt_id)
    prints = {
        "risk": policy.fingerprint(),
        "opening_range_breakout": OpeningRangeBreakoutPolicy().fingerprint(),
        "execution": ExecutionPolicy().fingerprint(),
        "review_prompt": sha256(prompt.encode("utf-8")).hexdigest(),
        "review_model": sha256(config.model_id.encode("utf-8")).hexdigest(),
        "runtime_config": config.fingerprint(),
    }
    family = strategy_family(strategy_name)
    if family == "momo":
        prints["momo_hod"] = MomoConfig().fingerprint()
    if strategy_name == "vwap":
        prints["vwap_pb"] = VwapPbConfig().fingerprint()
    if strategy_name == "gap":
        prints["gap_go"] = GapGoConfig().fingerprint()
    if family in MARKET_AWARE_FAMILIES:
        prints["market_regime_rule"] = MARKET_REGIME_RULE
    # A windowed simulation names its rules and its window apart, for the paper
    # promotion rule's evidence identity (a V1 config embeds its window).
    if strategy_name in STRATEGIES:
        evidence = getattr(STRATEGIES[strategy_name][0](), "policy_fingerprints", None)
        if callable(evidence):
            prints.update(evidence())
    if strategy_name in MANAGED_STRATEGIES:
        # The evidence identity of a managed run includes the manager's
        # version and the prompts its plans and reviews come from (Design F).
        from trading.intelligence.manager_prompts import DAY_PLAN_PROMPT_ID
        from trading.manager.constants import MANAGER_VERSION

        prints["manager_version"] = MANAGER_VERSION
        prints["manager_prompt_id"] = DAY_PLAN_PROMPT_ID
        prints["managed_review_prompt"] = sha256(
            review_prompt(MOMO_MANAGED_REVIEW_PROMPT_ID).encode("utf-8")
        ).hexdigest()
    return prints


def _require_strategy(strategy_name: str, universe: str = "scanner") -> None:
    if strategy_name not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy_name!r}")
    if universe not in UNIVERSES:
        raise ValueError(f"unknown universe {universe!r}")


def strategy_entry_window(strategy_name: str, session_date: date) -> tuple[datetime, datetime]:
    """The strategy's own ``(start, cutoff)`` in UTC; MOMO's moves in on half days."""
    _require_strategy(strategy_name)
    return entry_window_utc(STRATEGIES[strategy_name][0](), session_date)


def poll_offset_problems(offsets: Mapping[str, timedelta]) -> list[str]:
    """Every way ``offsets`` breaks the polling contract; empty when it holds."""
    problems: list[str] = []
    if set(offsets) != set(STRATEGIES):
        problems.append("poll offsets must name exactly the strategy registry")
    if any(not timedelta(0) < offset < BAR_INTERVAL for offset in offsets.values()):
        problems.append("every poll offset must fall inside its bar")
    ordered = sorted(offsets.items(), key=lambda item: item[1])
    for (earlier, early_at), (later, late_at) in zip(ordered, ordered[1:]):
        if late_at - early_at < MIN_POLL_SEPARATION:
            problems.append(f"{earlier} and {later} poll {late_at - early_at} apart")
    sendable = {
        name: name in STRATEGIES and STRATEGIES[name][1]().name in SENDABLE_IDENTITIES
        for name in offsets
    }
    sendable_at = [offsets[name] for name, sends in sendable.items() if sends]
    shadow_at = [offsets[name] for name, sends in sendable.items() if not sends]
    if sendable_at and shadow_at and max(sendable_at) >= min(shadow_at):
        problems.append("every sendable strategy must poll before every shadow strategy")
    return problems


def bar_freshness_limit(strategy_name: str, universe: str) -> timedelta:
    """How old a run's newest completed bar may be when it arrives.

    Hot-list and gapper runs fetch up to 35 symbols one after another and
    allow ``WATCHLIST_BAR_MAX_AGE``. A scanner run keeps exactly the budget its
    30 s limit gave a poll 5 s after the close, now counted from its own slot.
    """
    _require_strategy(strategy_name, universe)
    if universe == "scanner":
        return STRATEGY_POLL_OFFSETS[strategy_name] + MIN_PASS_BUDGET
    if strategy_name in MANAGED_STRATEGIES:
        return MANAGED_BAR_MAX_AGE
    return WATCHLIST_BAR_MAX_AGE


def poll_schedule(strategy_name: str, universe: str) -> PollSchedule:
    """The run's poll slot and freshness limit; refuses a broken offset table.

    Refused before any database write, Keychain read or TWS connection.
    """
    _require_strategy(strategy_name, universe)
    problems = poll_offset_problems(STRATEGY_POLL_OFFSETS)
    if problems:
        logger.critical("poll_offsets_invalid: %s", "; ".join(problems), stack_info=True)
        raise SessionAborted("poll_offsets_invalid: " + "; ".join(problems))
    return PollSchedule(
        offset=STRATEGY_POLL_OFFSETS[strategy_name],
        bar_max_age=bar_freshness_limit(strategy_name, universe),
    )


def _window_closed(strategy_name: str, session_date: date, cutoff: datetime) -> SessionAborted:
    local_cutoff = cutoff.astimezone(MARKET_TIMEZONE)
    return SessionAborted(
        f"{strategy_name} entry window for {session_date.isoformat()} closed at "
        f"{local_cutoff:%H:%M} ET"
    )


def session_for_strategy(
    strategy_name: str, *, now: datetime, requested: date | None = None
) -> MarketSession:
    """The session a run uses, judged by the strategy's own cutoff (not ORB's 12:00).

    A requested date is used as given and refused once its cutoff has passed;
    without one, today's session is used while its window is open, else the next.
    """
    if requested is not None:
        session = market_session(requested)
        if session is None:
            raise SessionAborted(f"{requested.isoformat()} is not an XNYS trading session")
        cutoff = strategy_entry_window(strategy_name, requested)[1]
        if now >= cutoff:
            raise _window_closed(strategy_name, requested, cutoff)
        return session
    today = market_session(now.astimezone(MARKET_TIMEZONE).date())
    if today is not None and now < strategy_entry_window(strategy_name, today.session_date)[1]:
        return today
    return next_market_session(now)


def fresh_run_label(now: datetime) -> str:
    """``r<HHMM>`` (ET) for a run started because the day's run could not resume."""
    return f"r{now.astimezone(MARKET_TIMEZONE):%H%M}"


async def run_shadow_session(
    *,
    config: ShadowRuntimeConfig,
    owner_id: str,
    session: MarketSession,
    clock: Clock = _utc_now,
    sleep: Sleeper = asyncio.sleep,
    run_label: str | None = None,
    strategy_name: str = "orb",
    universe: str = "scanner",
) -> SessionReport:
    """Run one real-data session end to end against the local PAPER TWS.

    ``run_label`` starts a separate run for the same day (e.g. after a changed
    scanner request); without it a restart resumes the day's existing run.
    ``universe="watchlist"`` takes candidates from the live screener's hot list
    when the strategy's entry window opens instead of the 09:45 IBKR scan, and
    adds symbols that join the hot list later in the window.
    """
    _require_strategy(strategy_name, universe)
    evaluator_factory, spec_factory, strategy_prompt = STRATEGIES[strategy_name]
    evaluator: StrategyEvaluator = evaluator_factory()
    window_start, window_cutoff = entry_window_utc(evaluator, session.session_date)
    # Refuse before any database write, Keychain read, or TWS connection: a
    # closed window, a broken poll-offset table (poll_schedule raises), or a
    # review prompt that is missing or written for another strategy kind
    # (review_prompt_for raises).
    if clock() >= window_cutoff:
        raise _window_closed(strategy_name, session.session_date, window_cutoff)
    schedule = poll_schedule(strategy_name, universe)
    prompt_id = strategy_prompt or config.prompt_id
    system_prompt = review_prompt_for(prompt_id, strategy_kind=evaluator.strategy_kind)
    spec = spec_factory()
    strategy = await ensure_strategy(owner_id=owner_id, spec=spec)
    # The identity's fixed size factor, from the catalogue name this run
    # registers; None (a sendable name with no row) blocks every setup.
    size_factor = identity_multiplier(spec.name, sendable=is_sendable_identity(spec.name))
    # The no-limit PAPER test (L-T1): a sendable identity decides under the test
    # policy (no accepted-decisions cap that can bind), every shadow name under
    # the default. The run records the policy's fingerprint, so test runs are
    # their own evidence identity, never the size-up track record's.
    policy = run_policy(spec.name)
    if policy != RiskPolicy():
        logger.info(
            "no-limit test %s: %s decides under risk policy %s v%d (max_open_positions %d)",
            NO_LIMIT_TEST_LABEL, spec.name, policy.id, policy.version, policy.max_open_positions,
        )
    run_id = f"shadow-{strategy.strategy_id[:8]}-{session.session_date.isoformat()}"
    if run_label:
        run_id = f"{run_id}-{run_label}"
    # Live 2026-10-12: a review took 9.6 s against a 10 s limit; a timeout is an
    # ABSTAIN, so the limit is sized to the model's observed latency.
    limits = ReviewSessionLimits(
        timeout_seconds=20.0,
        max_calls=config.max_ai_calls,
        max_cost_usd=config.max_ai_cost_usd,
    )
    market_aware = strategy_family(strategy_name) in MARKET_AWARE_FAMILIES
    api_key = read_anthropic_api_key()
    provider = StructuredAIReviewProvider(
        transport=AnthropicMessagesTransport(
            client=build_anthropic_client(
                api_key=api_key, timeout_seconds=limits.timeout_seconds
            ),
            system_prompt=system_prompt,
            context_provider=(
                market_context_provider(
                    session.session_date, clock=clock, reader=_read_market_context
                )
                if market_aware
                else None
            ),
        ),
        model_id=config.model_id,
        prompt_version=prompt_id,
        limits=limits,
        session_id=run_id,
    )
    review_provider: TradeProposalProvider = provider
    controller = None
    if strategy_name in MANAGED_STRATEGIES:
        controller, review_provider = await _managed_session(
            strategy_name=strategy_name,
            evaluator=evaluator,
            session=session,
            config=config,
            neutral=provider,
            neutral_prompt_id=prompt_id,
            limits=limits,
            run_id=run_id,
            api_key=api_key,
            clock=clock,
        )
    actor = TWSPaperActor(
        TWSActorConfig(
            account=config.paper_account_id,
            client_id=config.data_client_id + STRATEGY_CLIENT_OFFSET[strategy_name],
        ),
        capacity_checker=documented_line_allocation,
    )
    data_actor: IBKRConnectionActor = actor
    if bar_cache_active(strategy_name):
        # Wraps the history request only; the facade below validates a cached
        # batch exactly as a direct reply, under this run's own freshness limit.
        data_actor = bar_cache.CachedBarsActor(
            actor,
            cache=bar_cache.BarCache(writer=strategy_name, clock=clock, sleep=sleep),
            session_date=session.session_date,
            clock=clock,
        )
        logger.info("%s reads five-minute history through the shared bar cache", strategy_name)
    market = PaperIBKRMarketData(
        actor=data_actor,
        host=PAPER_TWS_HOST,
        volume_multiplier=IBKR_API_VOLUME_MULTIPLIER,
        # The limit the runner schedules its polls against is the one enforced.
        bar_max_age=schedule.bar_max_age,
    )

    async def watchlist_source(run):
        select = {"gappers": gap_candidates, "all-gappers": gap_union_candidates}.get(
            universe, hotlist_candidates
        )
        scanner_run, candidates, liquidity = await select(run, now=clock())
        for candidate in candidates:
            # Quotes are priced on each contract's own tick grid.
            await market.load_contract(candidate.con_id)
        return scanner_run, candidates, liquidity

    async def load_contract(con_id: int) -> object:
        # Refreshed and resumed candidates never pass through watchlist_source;
        # the market facade caches, so loaded contracts cost nothing here.
        return await market.load_contract(con_id)

    async def gap_bars(candidate, now):
        # RTH history (2 D) for ATR plus today's all-hours bars for the gap.
        regular = await market.completed_bars(con_id=candidate.con_id, now=now)
        extended = await market.extended_bars(con_id=candidate.con_id, now=now)
        return gap_go_bars(regular, extended, session_date=session.session_date)

    async def reconnect() -> None:
        if actor.connected:
            return
        await actor.disconnect()
        await actor.connect()
        logger.info("reconnected to TWS")

    dynamic = universe in DYNAMIC_UNIVERSES
    risk_off_gate = (
        risk_off_entry_gate(session.session_date, clock=clock, reader=_read_market_context)
        if market_aware
        else None
    )
    entry_gate = controller.entry_gate(risk_off_gate) if controller is not None else risk_off_gate
    connect_at = session.scanner_at if universe == "scanner" else window_start
    await _sleep_until(connect_at - CONNECT_LEAD, clock=clock, sleep=sleep)
    await actor.connect()
    try:
        preflight = await actor.preflight(required_data_lines=config.max_candidates)
        if not preflight.ready:
            raise SessionAborted("TWS preflight failed: " + "; ".join(preflight.reasons))
        runner = ShadowSessionRunner(
            market=market,
            read_account=actor.account_snapshot,
            runner=RepositoryBackedShadowRunner(
                pipeline=ShadowPipeline(
                    proposal_provider=review_provider,
                    strategy=evaluator,
                    sizing=controller,
                    identity_multiplier=size_factor,
                ),
                clock=clock,
            ),
            clock=clock,
            poll_schedule=schedule,
            sleep=sleep,
            reconnect=reconnect,
            holder_id=f"{socket.gethostname()}:{os.getpid()}",
            entry_gate=entry_gate,
            entry_gate_name=strategy_name,
            bars_fetcher=gap_bars if evaluator.strategy_kind == GAP_GO_STRATEGY_KIND else None,
            contract_loader=load_contract if dynamic else None,
            candidate_filter=controller.admits if controller is not None else None,
        )
        report = await runner.run(
            session=session,
            strategy=strategy,
            policy=policy,
            source_sha=source_sha(),
            policy_fingerprints=policy_fingerprints(
                config, policy, prompt_id=prompt_id, strategy_name=strategy_name
            ),
            run_id=run_id,
            candidate_source=watchlist_source if universe != "scanner" else None,
            window_start=window_start if universe != "scanner" else None,
            candidate_refresh=hotlist_refresh if dynamic else None,
        )
        if controller is not None:
            try:
                await controller.record_unfocused(run_id=report.run_id, owner_id=owner_id)
            except Exception:  # noqa: BLE001 - evidence never fails a finished session
                logger.error("could not mark the unfocused candidates", exc_info=True)
        usage = provider.usage
        logger.info(
            "session %s finished: %s; AI calls=%d cost=$%s",
            session.session_date.isoformat(),
            report.summary(),
            usage.calls,
            usage.cost_usd,
        )
    finally:
        await actor.disconnect()
    await idle_until_cutoff(
        strategy_name, window_cutoff, dynamic=dynamic, clock=clock, sleep=sleep
    )
    return report


async def idle_until_cutoff(
    strategy_name: str,
    cutoff: datetime,
    *,
    dynamic: bool,
    clock: Clock,
    sleep: Sleeper,
) -> None:
    """A run that finished before its cutoff stays alive until then (TWS closed).

    A non-dynamic run returns as soon as every candidate is terminal (or when
    it had none), and the supervisor restarts any clean exit before the
    child's planned finish: an all-session simulation with nothing left to
    assess would restart all day. Exiting at the cutoff is its planned finish.
    A dynamic run only returns at its cutoff sweep, so it never waits here.
    """
    if dynamic:
        return
    now = clock()
    if now >= cutoff:
        return
    logger.info(
        "%s: every candidate is decided; idle until the %s ET cutoff (TWS closed)",
        strategy_name,
        cutoff.astimezone(MARKET_TIMEZONE).strftime("%H:%M"),
    )
    await _sleep_until(cutoff, clock=clock, sleep=sleep)


async def _managed_session(
    *,
    strategy_name: str,
    evaluator: StrategyEvaluator,
    session: MarketSession,
    config: ShadowRuntimeConfig,
    neutral: StructuredAIReviewProvider,
    neutral_prompt_id: str,
    limits: ReviewSessionLimits,
    run_id: str,
    api_key: str,
    clock: Clock,
) -> tuple[ManagedStrategy, TradeProposalProvider]:
    """The day manager's controller (it latches the switch) and the managed reviewer.

    Imported on use: an unmanaged session never loads the manager.
    """
    from trading.intelligence.managed_review import ClaudeManagedReviewer, ManagedReviewProvider
    from trading.manager.managed import ManagedStrategy

    managed_prompt = review_prompt_for(
        MOMO_MANAGED_REVIEW_PROMPT_ID, strategy_kind=evaluator.strategy_kind
    )
    controller = await ManagedStrategy.open(
        session_date=session.session_date, family=strategy_family(strategy_name), clock=clock
    )
    reviewer = ClaudeManagedReviewer(
        client=build_anthropic_client(api_key=api_key, timeout_seconds=limits.timeout_seconds),
        model_id=config.model_id,
        prompt_id=MOMO_MANAGED_REVIEW_PROMPT_ID,
        system_prompt=managed_prompt,
        limits=limits,
        session_id=run_id,
        context_provider=market_context_provider(
            session.session_date, clock=clock, reader=_read_market_context
        ),
    )
    provider = ManagedReviewProvider(
        neutral=neutral,
        managed=reviewer,
        context_for=controller.review_context,
        on_verdict=controller.on_verdict,
        limits=limits,
        neutral_model_id=config.model_id,
        neutral_prompt_id=neutral_prompt_id,
        managed_model_id=config.model_id,
        managed_prompt_id=MOMO_MANAGED_REVIEW_PROMPT_ID,
    )
    return controller, provider


async def run_shadow_day(
    *,
    config: ShadowRuntimeConfig,
    owner_id: str,
    session: MarketSession,
    strategy_name: str = "orb",
    universe: str = "scanner",
    run_label: str | None = None,
    clock: Clock = _utc_now,
    sleep: Sleeper = asyncio.sleep,
) -> SessionReport:
    """Resume the day's run; a run that cannot resume starts one fresh run.

    A restarted watchlist session resumes its dynamic run with every stored
    candidate plus the current hot list. Scanner and gappers sessions
    re-select on restart (a new scan or snapshot), which conflicts with the
    stored run by construction, as does any run whose code or policy changed.
    The stored run is never rewritten: exactly one retry runs under
    ``r<HHMM>`` (ET).
    """
    try:
        return await run_shadow_session(
            config=config,
            owner_id=owner_id,
            session=session,
            clock=clock,
            sleep=sleep,
            run_label=run_label,
            strategy_name=strategy_name,
            universe=universe,
        )
    except ShadowPersistenceConflictError as exc:
        label = fresh_run_label(clock())
        # A run that cannot resume never will (its selection, code or policy
        # differs from the stored run); a fresh labelled run avoids a restart
        # loop until the strategy's cutoff.
        if label == run_label:
            raise
        logger.warning(
            "%s %s run cannot resume (%s); starting a fresh run %s",
            strategy_name,
            universe,
            exc,
            label,
        )
    return await run_shadow_session(
        config=config,
        owner_id=owner_id,
        session=session,
        clock=clock,
        sleep=sleep,
        run_label=label,
        strategy_name=strategy_name,
        universe=universe,
    )


async def _sleep_until(moment: datetime, *, clock: Clock, sleep: Sleeper) -> None:
    remaining = (moment.astimezone(timezone.utc) - clock()).total_seconds()
    if remaining > 0:
        await sleep(remaining)


__all__ = [
    "DYNAMIC_UNIVERSES",
    "IBKR_API_VOLUME_MULTIPLIER",
    "MANAGED_BAR_MAX_AGE",
    "MANAGED_STRATEGIES",
    "MARKET_AWARE_FAMILIES",
    "MARKET_AWARE_STRATEGIES",
    "MARKET_CONTEXT_MAX_AGE",
    "MARKET_REGIME_RULE",
    "MIN_POLL_SEPARATION",
    "MOMO_REVIEW_PROMPT_ID",
    "SENDABLE_KEYS",
    "STRATEGIES",
    "STRATEGY_FAMILIES",
    "STRATEGY_POLL_OFFSETS",
    "STRATEGY_USES_BAR_CACHE",
    "UNIVERSES",
    "WATCHLIST_BAR_MAX_AGE",
    "bar_cache_active",
    "bar_freshness_limit",
    "fresh_run_label",
    "idle_until_cutoff",
    "market_context_provider",
    "policy_fingerprints",
    "poll_offset_problems",
    "poll_schedule",
    "registered_spec_names",
    "registry_problems",
    "risk_off_entry_gate",
    "run_shadow_day",
    "run_shadow_session",
    "session_for_strategy",
    "source_sha",
    "strategy_entry_window",
    "strategy_family",
    "uses_bar_cache",
]
