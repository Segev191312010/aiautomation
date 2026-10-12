"""Composition root for the watchlist universe and the always-on live screener.

The screener connects to the local PAPER TWS with its own client ID and
reads market data only; nothing here can place, modify, or cancel orders.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timezone
import logging
from pathlib import Path
import time as monotonic_time
from typing import Protocol

from trading.contracts import ShadowRuntimeConfig
from trading.market.screener import (
    ScanMerge,
    ScanSource,
    ScreenerError,
    SnapshotSource,
    SweepResult,
    run_sweep,
)
from trading.market.tws_actor import (
    TWSActorConfig,
    TWSActorError,
    TWSCompetingSessionError,
    TWSConnectionError,
    TWSPaperActor,
)
from trading.market.universe import (
    MAX_WATCHLIST_BYTES,
    ImportReport,
    QualificationReport,
    RequestPacer,
    SymbolResolver,
    WatchlistFormatError,
    import_watchlist,
    parse_watchlist,
    qualify_universe,
)
from trading.operations.shadow_service import IBKR_API_VOLUME_MULTIPLIER
from trading.scheduler.sessions import MARKET_TIMEZONE, market_session, next_market_session

logger = logging.getLogger("autotrader.shadow.screener")

# TWS allows one connection per client ID; the screener sits beside the
# shadow data (data_client_id) and order (order_client_id) connections.
SCREENER_CLIENT_ID_OFFSET = 2
WINDOW_OPENS = time(8, 0)
WINDOW_CLOSES = time(16, 0)
COMPETING_SESSION_MESSAGE = "IBKR login active elsewhere (phone/web) — log out there"
COMPETING_SESSION_RETRY_SECONDS = 60.0
RECONNECT_BACKOFF_SECONDS = (5.0, 10.0, 20.0, 40.0, 60.0)
# macOS stops the monotonic clock during system sleep, so long waits are cut
# into chunks and re-checked against wall-clock time.
MAX_SLEEP_CHUNK_SECONDS = 300.0
CONTRACT_DETAILS_MIN_INTERVAL_SECONDS = 0.05
# Historical-data requests are paced at one per second.
DAILY_HISTORY_MIN_INTERVAL_SECONDS = 1.0
MIN_SWEEP_PAUSE_SECONDS = 1.0

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]


class ScreenerActor(SymbolResolver, SnapshotSource, ScanSource, Protocol):
    """The market-data surface of the TWS actor the screener depends on."""

    @property
    def connected(self) -> bool: ...

    @property
    def last_error(self) -> str | None: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...


ActorFactory = Callable[[TWSActorConfig], ScreenerActor]


class ScreenerConfigurationError(ValueError):
    """The screener's TWS identity would collide with another connection."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def screener_actor_config(config: ShadowRuntimeConfig) -> TWSActorConfig:
    client_id = config.data_client_id + SCREENER_CLIENT_ID_OFFSET
    if client_id in {config.data_client_id, config.order_client_id}:
        raise ScreenerConfigurationError(
            f"screener client id {client_id} collides with the shadow data/order client ids"
        )
    return TWSActorConfig(account=config.paper_account_id, client_id=client_id)


def default_actor_factory(config: TWSActorConfig) -> ScreenerActor:
    return TWSPaperActor(config)


def _build_actor(
    actor_factory: ActorFactory | None, runtime_config: ShadowRuntimeConfig
) -> ScreenerActor:
    factory = actor_factory or default_actor_factory
    return factory(screener_actor_config(runtime_config))


def screener_session_date(now: datetime) -> date:
    """Today's ET session date, or the next session on closed days."""
    local = now.astimezone(MARKET_TIMEZONE).date()
    if market_session(local) is not None:
        return local
    return next_market_session(local).session_date


def window_session_date(now: datetime) -> date | None:
    """The session being screened if ``now`` is inside 08:00-16:00 ET on a trading day."""
    local = now.astimezone(MARKET_TIMEZONE)
    if market_session(local.date()) is None:
        return None
    if WINDOW_OPENS <= local.time() < WINDOW_CLOSES:
        return local.date()
    return None


def next_window_open(now: datetime) -> datetime:
    local = now.astimezone(MARKET_TIMEZONE)
    if market_session(local.date()) is not None and local.time() < WINDOW_OPENS:
        return datetime.combine(local.date(), WINDOW_OPENS, tzinfo=MARKET_TIMEZONE)
    session = next_market_session(local.date())
    return datetime.combine(session.session_date, WINDOW_OPENS, tzinfo=MARKET_TIMEZONE)


async def sleep_until(moment: datetime, *, clock: Clock, sleep: Sleeper) -> None:
    while (remaining := (moment - clock()).total_seconds()) > 0:
        await sleep(min(remaining, MAX_SLEEP_CHUNK_SECONDS))


async def connect_actor(actor: ScreenerActor) -> None:
    """Connect, surfacing TWS's own reason (e.g. client ID already in use)."""
    try:
        await actor.connect()
    except TWSConnectionError as exc:
        detail = actor.last_error
        if detail and detail not in str(exc):
            raise TWSConnectionError(f"{exc} ({detail})") from exc
        raise


def read_watchlist(path: Path) -> str:
    resolved = path.expanduser()
    if not resolved.is_file():
        raise WatchlistFormatError(f"watchlist export not found: {resolved}")
    if resolved.stat().st_size > MAX_WATCHLIST_BYTES:
        raise WatchlistFormatError("watchlist export exceeds the 1 MiB limit")
    return resolved.read_text(encoding="utf-8-sig")


async def import_universe(
    path: Path,
    *,
    runtime_config: ShadowRuntimeConfig,
    actor_factory: ActorFactory | None = None,
    clock: Clock = _utc_now,
    pacer: RequestPacer | None = None,
) -> ImportReport:
    text = read_watchlist(path)
    # Reject an unreadable export before opening a TWS connection.
    parse_watchlist(text)
    actor = _build_actor(actor_factory, runtime_config)
    await connect_actor(actor)
    try:
        return await import_watchlist(
            actor,
            text,
            clock=clock,
            pacer=pacer or RequestPacer(CONTRACT_DETAILS_MIN_INTERVAL_SECONDS),
        )
    finally:
        await actor.disconnect()


async def _qualify(
    actor: ScreenerActor,
    session_date: date,
    *,
    clock: Clock,
    pacer: RequestPacer | None,
) -> QualificationReport:
    return await qualify_universe(
        actor,
        session_date=session_date,
        volume_multiplier=IBKR_API_VOLUME_MULTIPLIER,
        clock=clock,
        pacer=pacer or RequestPacer(DAILY_HISTORY_MIN_INTERVAL_SECONDS),
    )


def _scan_merge(actor: ScreenerActor, pacer: RequestPacer, *, enabled: bool) -> ScanMerge | None:
    if not enabled:
        return None
    return ScanMerge(
        source=actor, volume_multiplier=IBKR_API_VOLUME_MULTIPLIER, history_pacer=pacer
    )


async def qualify_session(
    session_date: date,
    *,
    runtime_config: ShadowRuntimeConfig,
    actor_factory: ActorFactory | None = None,
    clock: Clock = _utc_now,
    pacer: RequestPacer | None = None,
) -> QualificationReport:
    actor = _build_actor(actor_factory, runtime_config)
    await connect_actor(actor)
    try:
        return await _qualify(actor, session_date, clock=clock, pacer=pacer)
    finally:
        await actor.disconnect()


async def run_screener_once(
    *,
    runtime_config: ShadowRuntimeConfig,
    actor_factory: ActorFactory | None = None,
    clock: Clock = _utc_now,
    history_pacer: RequestPacer | None = None,
    include_scans: bool = True,
) -> tuple[QualificationReport, SweepResult]:
    """Qualify (resumably) and sweep the current session once."""
    session_date = screener_session_date(clock())
    pacer = history_pacer or RequestPacer(DAILY_HISTORY_MIN_INTERVAL_SECONDS)
    actor = _build_actor(actor_factory, runtime_config)
    await connect_actor(actor)
    try:
        qualification = await _qualify(actor, session_date, clock=clock, pacer=pacer)
        sweep = await run_sweep(
            actor,
            session_date=session_date,
            clock=clock,
            scan_merge=_scan_merge(actor, pacer, enabled=include_scans),
        )
        return qualification, sweep
    finally:
        await actor.disconnect()


async def run_screener(
    *,
    runtime_config: ShadowRuntimeConfig,
    interval_seconds: float = 60.0,
    actor_factory: ActorFactory | None = None,
    clock: Clock = _utc_now,
    sleep: Sleeper = asyncio.sleep,
    monotonic: Callable[[], float] = monotonic_time.monotonic,
    max_sweeps: int | None = None,
    include_scans: bool = True,
) -> int:
    """Sweep every ``interval_seconds`` inside 08:00-16:00 ET on trading days.

    Outside the window the TWS connection is released. Connection failures
    back off up to a minute; a competing IBKR login is reported and retried
    every minute until the owner logs out elsewhere. Returns completed sweeps.
    """
    if not 0 < interval_seconds < float("inf"):
        raise ValueError("interval_seconds must be finite and positive")
    actor = _build_actor(actor_factory, runtime_config)
    history_pacer = RequestPacer(DAILY_HISTORY_MIN_INTERVAL_SECONDS)
    scan_merge = _scan_merge(actor, history_pacer, enabled=include_scans)
    qualified_dates: set[date] = set()
    failures = 0
    sweeps = 0
    try:
        while max_sweeps is None or sweeps < max_sweeps:
            now = clock()
            session_date = window_session_date(now)
            if session_date is None:
                if actor.connected:
                    await actor.disconnect()
                    logger.info("outside the screener window; TWS connection released")
                wake = next_window_open(now)
                logger.info("screener idle until %s", wake.isoformat())
                await sleep_until(wake, clock=clock, sleep=sleep)
                continue
            started = monotonic()
            try:
                if not actor.connected:
                    await actor.disconnect()
                    await connect_actor(actor)
                    logger.info("screener connected to TWS")
                failures = 0
                if session_date not in qualified_dates:
                    report = await _qualify(actor, session_date, clock=clock, pacer=history_pacer)
                    qualified_dates.add(session_date)
                    logger.info(
                        "universe qualified for %s: %d qualified, %d transient errors",
                        session_date.isoformat(),
                        report.qualified,
                        len(report.errors),
                    )
                await run_sweep(
                    actor, session_date=session_date, clock=clock, scan_merge=scan_merge
                )
                sweeps += 1
                if max_sweeps is not None and sweeps >= max_sweeps:
                    break
            except asyncio.CancelledError:
                raise
            except TWSCompetingSessionError as exc:
                logger.error(
                    "%s; retrying in %.0f s (%s)",
                    COMPETING_SESSION_MESSAGE,
                    COMPETING_SESSION_RETRY_SECONDS,
                    exc,
                )
                await sleep(COMPETING_SESSION_RETRY_SECONDS)
                continue
            except TWSConnectionError as exc:
                delay = RECONNECT_BACKOFF_SECONDS[min(failures, len(RECONNECT_BACKOFF_SECONDS) - 1)]
                failures += 1
                logger.warning("TWS connection failed: %s; reconnecting in %.0f s", exc, delay)
                await actor.disconnect()
                await sleep(delay)
                continue
            except (ScreenerError, TWSActorError) as exc:
                # The feed stopped answering; reconnect on the next cycle.
                logger.error("screener sweep failed: %s", exc)
                await actor.disconnect()
            except Exception:  # noqa: BLE001 - one bad cycle never stops the daemon
                logger.exception("screener cycle failed")
            elapsed = monotonic() - started
            await sleep(max(MIN_SWEEP_PAUSE_SECONDS, interval_seconds - elapsed))
    finally:
        await actor.disconnect()
    return sweeps


__all__ = [
    "COMPETING_SESSION_MESSAGE",
    "SCREENER_CLIENT_ID_OFFSET",
    "ScreenerActor",
    "ScreenerConfigurationError",
    "connect_actor",
    "default_actor_factory",
    "import_universe",
    "next_window_open",
    "qualify_session",
    "read_watchlist",
    "run_screener",
    "run_screener_once",
    "screener_actor_config",
    "screener_session_date",
    "sleep_until",
    "window_session_date",
]
