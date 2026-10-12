"""Composition root for real-data shadow sessions and the unattended daemon.

Shadow mode reads IBKR market data and the PAPER account, asks Claude for a
review, and records decisions. It holds no order client and cannot submit.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import logging
import os
from pathlib import Path
import socket
import subprocess

from trading.contracts import RiskPolicy, ShadowRuntimeConfig
from trading.intelligence.anthropic_transport import (
    AnthropicMessagesTransport,
    build_anthropic_client,
)
from trading.intelligence.prompts import review_prompt, review_prompt_for
from trading.intelligence.review_provider import ReviewSessionLimits, StructuredAIReviewProvider
from trading.market.ibkr_boundary import PaperIBKRMarketData
from trading.market.tws_actor import (
    PAPER_TWS_HOST,
    TWSActorConfig,
    TWSPaperActor,
    documented_line_allocation,
)
from trading.operations.keychain import read_anthropic_api_key
from trading.orchestration import RepositoryBackedShadowRunner, ShadowPipeline
from trading.orchestration.session_runner import (
    momo_strategy_spec,
    orb_strategy_spec,
    ensure_strategy,
    SessionAborted,
    SessionReport,
    ShadowSessionRunner,
)
from trading.orchestration.watchlist_source import hotlist_candidates
from trading.policies import ExecutionPolicy, OpeningRangeBreakoutPolicy
from trading.rules.momo import MomoConfig, MomoStrategy
from trading.rules.strategy import ORBStrategy, StrategyEvaluator
from trading.scheduler.sessions import (
    MARKET_TIMEZONE,
    MarketSession,
    market_session,
    next_market_session,
)

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


# Strategy name -> (evaluator factory, catalogue spec factory, review prompt id).
# ORB keeps the configured prompt so its existing run fingerprints are stable.
STRATEGIES = {
    "orb": (ORBStrategy, orb_strategy_spec, None),
    "momo": (MomoStrategy, momo_strategy_spec, "momo-review-v1"),
}
# TWS accepts one connection per client ID. Strategy sessions run concurrently,
# so each gets its own data client: data_client_id + offset (the screener uses
# data_client_id + 2 and the order bridge uses order_client_id).
STRATEGY_CLIENT_OFFSET = {"orb": 0, "momo": 3}
# A hot-list session fetches bars for up to 35 symbols one after another (~90 s
# live), so the newest completed bar is legitimately older than the 30 s
# single-symbol limit. Anything under one bar interval still guarantees the
# newest bar is the latest completed one.
WATCHLIST_BAR_MAX_AGE = timedelta(seconds=240)


def policy_fingerprints(
    config: ShadowRuntimeConfig, policy: RiskPolicy, *, prompt_id: str | None = None
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
    if prompt_id == "momo-review-v1":
        prints["momo_hod"] = MomoConfig().fingerprint()
    return prints


def session_to_run(now: datetime) -> MarketSession:
    """Today's session while its entry window is still open, else the next one."""
    today = market_session(now.astimezone(MARKET_TIMEZONE).date())
    if today is not None and now < today.entry_cutoff_at:
        return today
    return next_market_session(now)


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
    when the strategy's entry window opens instead of the 09:45 IBKR scan.
    """
    if strategy_name not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy_name!r}")
    if universe not in {"scanner", "watchlist"}:
        raise ValueError(f"unknown universe {universe!r}")
    evaluator_factory, spec_factory, strategy_prompt = STRATEGIES[strategy_name]
    evaluator: StrategyEvaluator = evaluator_factory()
    prompt_id = strategy_prompt or config.prompt_id
    strategy = await ensure_strategy(owner_id=owner_id, spec=spec_factory())
    policy = RiskPolicy()
    run_id = f"shadow-{strategy.strategy_id[:8]}-{session.session_date.isoformat()}"
    if run_label:
        run_id = f"{run_id}-{run_label}"
    window_start, window_cutoff = evaluator.entry_window(session.session_date)
    # Live 2026-10-12: a review took 9.6 s against a 10 s limit; a timeout is an
    # ABSTAIN, so the limit is sized to the model's observed latency.
    limits = ReviewSessionLimits(
        timeout_seconds=20.0,
        max_calls=config.max_ai_calls,
        max_cost_usd=config.max_ai_cost_usd,
    )
    api_key = read_anthropic_api_key()
    provider = StructuredAIReviewProvider(
        transport=AnthropicMessagesTransport(
            client=build_anthropic_client(
                api_key=api_key, timeout_seconds=limits.timeout_seconds
            ),
            system_prompt=review_prompt_for(prompt_id, strategy_kind=evaluator.strategy_kind),
        ),
        model_id=config.model_id,
        prompt_version=prompt_id,
        limits=limits,
        session_id=run_id,
    )
    actor = TWSPaperActor(
        TWSActorConfig(
            account=config.paper_account_id,
            client_id=config.data_client_id + STRATEGY_CLIENT_OFFSET[strategy_name],
        ),
        capacity_checker=documented_line_allocation,
    )
    market = PaperIBKRMarketData(
        actor=actor,
        host=PAPER_TWS_HOST,
        volume_multiplier=IBKR_API_VOLUME_MULTIPLIER,
        **({"bar_max_age": WATCHLIST_BAR_MAX_AGE} if universe == "watchlist" else {}),
    )

    async def watchlist_source(run):
        scanner_run, candidates, liquidity = await hotlist_candidates(run, now=clock())
        for candidate in candidates:
            # Quotes are priced on each contract's own tick grid.
            await market.load_contract(candidate.con_id)
        return scanner_run, candidates, liquidity

    async def reconnect() -> None:
        if actor.connected:
            return
        await actor.disconnect()
        await actor.connect()
        logger.info("reconnected to TWS")

    connect_at = session.scanner_at if universe == "scanner" else window_start
    if clock() >= window_cutoff:
        raise SessionAborted(f"{strategy_name} entry window already closed for today")
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
                pipeline=ShadowPipeline(proposal_provider=provider, strategy=evaluator),
                clock=clock,
            ),
            clock=clock,
            sleep=sleep,
            reconnect=reconnect,
            holder_id=f"{socket.gethostname()}:{os.getpid()}",
        )
        report = await runner.run(
            session=session,
            strategy=strategy,
            policy=policy,
            source_sha=source_sha(),
            policy_fingerprints=policy_fingerprints(config, policy, prompt_id=prompt_id),
            run_id=run_id,
            candidate_source=watchlist_source if universe == "watchlist" else None,
            window_start=window_start if universe == "watchlist" else None,
        )
        usage = provider.usage
        logger.info(
            "session %s finished: %s; AI calls=%d cost=$%s",
            session.session_date.isoformat(),
            report.summary(),
            usage.calls,
            usage.cost_usd,
        )
        return report
    finally:
        await actor.disconnect()


async def run_shadow_daemon(
    *,
    config_loader: Callable[[], ShadowRuntimeConfig],
    owner_id: str,
    clock: Clock = _utc_now,
    sleep: Sleeper = asyncio.sleep,
    max_sessions: int | None = None,
) -> None:
    """Run every trading session forever; one failed day never stops the next."""
    completed = 0
    while max_sessions is None or completed < max_sessions:
        session = session_to_run(clock())
        logger.info("next session %s", session.session_date.isoformat())
        try:
            # Reload each day so config edits apply without restarting the daemon.
            config = config_loader()
            await run_shadow_session(
                config=config, owner_id=owner_id, session=session, clock=clock, sleep=sleep
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("session %s failed", session.session_date.isoformat())
        completed += 1
        await _sleep_until(session.entry_cutoff_at, clock=clock, sleep=sleep)


async def _sleep_until(moment: datetime, *, clock: Clock, sleep: Sleeper) -> None:
    remaining = (moment.astimezone(timezone.utc) - clock()).total_seconds()
    if remaining > 0:
        await sleep(remaining)


__all__ = [
    "IBKR_API_VOLUME_MULTIPLIER",
    "policy_fingerprints",
    "run_shadow_daemon",
    "run_shadow_session",
    "session_to_run",
    "source_sha",
]
