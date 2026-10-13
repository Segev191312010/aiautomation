"""Composition root for the watchlist universe and the always-on live screener.

The screener connects to the local PAPER TWS with its own client ID and
reads market data only; nothing here can place, modify, or cancel orders.
Each sweep also records the market context (index, breadth and sector ETFs).

``universe import``, ``universe qualify`` and ``screener run`` all connect
with that one client ID, so each holds ``client-<id>.lock`` beside the
database for its whole run and a second one exits with a clear message
instead of fighting over the TWS connection. Outside the sweep window the
screener loop also runs the daily snapshot retention job.

Each cycle: sweep (the hot-35 ranking group first, then the quote-only day
universe and held symbols), then, in the idle rest of the interval, admit
new scan names to the day universe; admission stops
``ADMISSION_MARGIN_SECONDS`` before the next sweep is due, so it never
delays one. The session's first sweep also re-checks the prior closes that
the evening qualification stored (splits overnight).
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
import fcntl
import logging
import os
from pathlib import Path
import time as monotonic_time
from typing import Protocol

from trading.contracts import ShadowRuntimeConfig
from trading.market.day_universe import (
    ADMISSION_MARGIN_SECONDS,
    AdmissionReport,
    AdmissionSource,
    admit_scan_names,
    scan_sightings,
)
from trading.market.screener import (
    ContextETFs,
    PriorCloseRecheck,
    ScanMerge,
    ScanSource,
    ScreenerError,
    SnapshotSource,
    SweepResult,
    recheck_prior_closes,
    run_scans,
    run_sweep,
    universe_con_ids,
)
from trading.market import tws_actor as tws_actor_module
from trading.market.retention import compact_snapshots, next_retention_at, retention_day
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
    runtime_directory,
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
# `screener run --once` admits day-universe names for at most this long.
ONCE_ADMISSION_SECONDS = 60.0
CLIENT_LOCK_TEMPLATE = "client-{client_id}.lock"
_LOCK_FILE_MODE = 0o600

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]


class ScreenerActor(SymbolResolver, SnapshotSource, ScanSource, AdmissionSource, Protocol):
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


class ClientIdInUseError(RuntimeError):
    """Another local process holds the lock of the TWS client ID this command needs."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def client_lock_path(client_id: int) -> Path:
    """``client-<id>.lock`` beside the configured database."""
    return runtime_directory() / CLIENT_LOCK_TEMPLATE.format(client_id=client_id)


def _lock_holder(descriptor: int) -> str:
    try:
        text = os.pread(descriptor, 256, 0).decode("utf-8", "replace").strip()
    except OSError:
        text = ""
    pid, _, purpose = text.partition(" ")
    if not pid.isdigit():
        return "another process"
    return f"`{purpose}` (pid {pid})" if purpose else f"pid {pid}"


@contextmanager
def tws_client_lock(client_id: int, purpose: str) -> Iterator[Path]:
    """Hold the client ID's lock for one whole command; a second holder is refused.

    TWS accepts one connection per client ID. The kernel drops the flock
    when the holding process exits, so a crash never leaves a stale lock.
    """
    path = client_lock_path(client_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(
        path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, _LOCK_FILE_MODE
    )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ClientIdInUseError(
                f"TWS client {client_id} is in use by {_lock_holder(descriptor)}; "
                "`universe import`, `universe qualify` and `screener run` share it and "
                "never run at the same time. Wait for it to finish or stop it, then retry."
            ) from exc
        os.ftruncate(descriptor, 0)
        os.pwrite(descriptor, f"{os.getpid()} {purpose}\n".encode("utf-8"), 0)
        yield path
    finally:
        os.close(descriptor)


def screener_actor_config(config: ShadowRuntimeConfig) -> TWSActorConfig:
    client_id = config.data_client_id + SCREENER_CLIENT_ID_OFFSET
    if client_id in {config.data_client_id, config.order_client_id}:
        raise ScreenerConfigurationError(
            f"screener client id {client_id} collides with the shadow data/order client ids"
        )
    return TWSActorConfig(account=config.paper_account_id, client_id=client_id)


def default_actor_factory(config: TWSActorConfig) -> ScreenerActor:
    return TWSPaperActor(config)


def _build_actor(actor_factory: ActorFactory | None, config: TWSActorConfig) -> ScreenerActor:
    factory = actor_factory or default_actor_factory
    return factory(config)


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
    config = screener_actor_config(runtime_config)
    with tws_client_lock(config.client_id, "universe import"):
        actor = _build_actor(actor_factory, config)
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


def day_universe_scan_codes() -> tuple[str, ...]:
    """Day-universe-only scans: the gap codes the probe has proven (none yet)."""
    return tuple(sorted(tws_actor_module.PROVEN_GAP_SCAN_CODES))


async def admit_day_universe(
    actor: ScreenerActor,
    sweep: SweepResult,
    *,
    session_date: date,
    context_etfs: ContextETFs,
    history_pacer: RequestPacer,
    details_pacer: RequestPacer,
    clock: Clock,
    monotonic: Callable[[], float],
    deadline: float | None,
) -> AdmissionReport:
    """Admit the sweep's scan names (plus any proven gap scan) to the day universe.

    The gap scans run here, after the sweep was persisted, so they never
    touch the hot-35 ranking group or its timing.
    """
    scans = dict(sweep.scans)
    extra_codes = [code for code in day_universe_scan_codes() if code not in scans]
    if extra_codes:
        scans.update(await run_scans(actor, extra_codes))
    exclude = await universe_con_ids() | context_etfs.con_ids
    report = await admit_scan_names(
        actor,
        session_date=session_date,
        sightings=scan_sightings(scans, exclude=exclude),
        volume_multiplier=IBKR_API_VOLUME_MULTIPLIER,
        history_pacer=history_pacer,
        details_pacer=details_pacer,
        clock=clock,
        monotonic=monotonic,
        deadline=deadline,
    )
    if report.admitted or report.rejected or report.failed:
        logger.info(report.summary())
    return report


async def recheck_session_prior_closes(
    actor: ScreenerActor,
    sweep: SweepResult,
    *,
    session_date: date,
    history_pacer: RequestPacer,
    clock: Clock,
) -> PriorCloseRecheck:
    report = await recheck_prior_closes(
        actor,
        session_date=session_date,
        payload=sweep.payload,
        volume_multiplier=IBKR_API_VOLUME_MULTIPLIER,
        pacer=history_pacer,
        clock=clock,
    )
    if report.checked:
        log = logger.warning if report.still_different else logger.info
        log(report.summary())
    return report


async def qualify_session(
    session_date: date,
    *,
    runtime_config: ShadowRuntimeConfig,
    actor_factory: ActorFactory | None = None,
    clock: Clock = _utc_now,
    pacer: RequestPacer | None = None,
) -> QualificationReport:
    config = screener_actor_config(runtime_config)
    with tws_client_lock(config.client_id, "universe qualify"):
        actor = _build_actor(actor_factory, config)
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
    """Qualify (resumably), sweep the current session once, then admit day-universe names."""
    session_date = screener_session_date(clock())
    pacer = history_pacer or RequestPacer(DAILY_HISTORY_MIN_INTERVAL_SECONDS)
    config = screener_actor_config(runtime_config)
    with tws_client_lock(config.client_id, "screener run --once"):
        actor = _build_actor(actor_factory, config)
        await connect_actor(actor)
        try:
            qualification = await _qualify(actor, session_date, clock=clock, pacer=pacer)
            context_etfs = ContextETFs(actor, clock=clock)
            sweep = await run_sweep(
                actor,
                session_date=session_date,
                clock=clock,
                scan_merge=_scan_merge(actor, pacer, enabled=include_scans),
                context_etfs=context_etfs,
                day_universe=include_scans,
            )
            if include_scans:
                await admit_day_universe(
                    actor,
                    sweep,
                    session_date=session_date,
                    context_etfs=context_etfs,
                    history_pacer=pacer,
                    details_pacer=RequestPacer(CONTRACT_DETAILS_MIN_INTERVAL_SECONDS),
                    clock=clock,
                    monotonic=monotonic_time.monotonic,
                    deadline=monotonic_time.monotonic() + ONCE_ADMISSION_SECONDS,
                )
            return qualification, sweep
        finally:
            await actor.disconnect()


async def _run_retention(now: datetime, *, clock: Clock, deadline: datetime) -> None:
    """The daily snapshot retention job; a failure is logged and never stops the screener."""
    try:
        report = await compact_snapshots(now, clock=clock, deadline=deadline)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - housekeeping must never stop the screener
        logger.exception("screener snapshot retention failed")
        return
    logger.info("snapshot retention: %s", report.summary())


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
    retention: bool = True,
) -> int:
    """Sweep every ``interval_seconds`` inside 08:00-16:00 ET on trading days.

    Outside the window the TWS connection is released. Connection failures
    back off up to a minute; a competing IBKR login is reported and retried
    every minute until the owner logs out elsewhere. Returns completed sweeps.

    With ``retention`` the snapshot retention job runs outside the window
    once per day after 16:05 ET; a screener stopped at 16:00 (as the
    supervisor does) catches up before its next window opens, with that
    window's opening as the job's deadline.
    """
    if not 0 < interval_seconds < float("inf"):
        raise ValueError("interval_seconds must be finite and positive")
    config = screener_actor_config(runtime_config)
    with tws_client_lock(config.client_id, "screener run"):
        return await _screener_loop(
            _build_actor(actor_factory, config),
            interval_seconds=interval_seconds,
            clock=clock,
            sleep=sleep,
            monotonic=monotonic,
            max_sweeps=max_sweeps,
            include_scans=include_scans,
            retention=retention,
        )


async def _screener_loop(
    actor: ScreenerActor,
    *,
    interval_seconds: float,
    clock: Clock,
    sleep: Sleeper,
    monotonic: Callable[[], float],
    max_sweeps: int | None,
    include_scans: bool,
    retention: bool,
) -> int:
    history_pacer = RequestPacer(DAILY_HISTORY_MIN_INTERVAL_SECONDS)
    details_pacer = RequestPacer(CONTRACT_DETAILS_MIN_INTERVAL_SECONDS)
    scan_merge = _scan_merge(actor, history_pacer, enabled=include_scans)
    # conIds are stable across reconnects, so the ETFs resolve once per process.
    context_etfs = ContextETFs(actor, clock=clock)
    qualified_dates: set[date] = set()
    rechecked_dates: set[date] = set()
    retained_for: date | None = None
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
                if retention:
                    due = retention_day(now)
                    if retained_for != due:
                        # Marked before running: a failing job waits for tomorrow.
                        retained_for = due
                        await _run_retention(now, clock=clock, deadline=wake)
                        continue
                    wake = min(wake, next_retention_at(now))
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
                sweep = await run_sweep(
                    actor,
                    session_date=session_date,
                    clock=clock,
                    scan_merge=scan_merge,
                    context_etfs=context_etfs,
                    day_universe=include_scans,
                )
                sweeps += 1
                if session_date not in rechecked_dates:
                    await recheck_session_prior_closes(
                        actor,
                        sweep,
                        session_date=session_date,
                        history_pacer=history_pacer,
                        clock=clock,
                    )
                    rechecked_dates.add(session_date)
                if include_scans:
                    # The idle rest of the interval; never past the next sweep.
                    await admit_day_universe(
                        actor,
                        sweep,
                        session_date=session_date,
                        context_etfs=context_etfs,
                        history_pacer=history_pacer,
                        details_pacer=details_pacer,
                        clock=clock,
                        monotonic=monotonic,
                        deadline=started + interval_seconds - ADMISSION_MARGIN_SECONDS,
                    )
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
    "CLIENT_LOCK_TEMPLATE",
    "COMPETING_SESSION_MESSAGE",
    "SCREENER_CLIENT_ID_OFFSET",
    "ClientIdInUseError",
    "ScreenerActor",
    "ScreenerConfigurationError",
    "client_lock_path",
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
    "tws_client_lock",
    "window_session_date",
]
