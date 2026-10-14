"""Local operator controls for the Autotrader V1 runtime.

PAPER controls are stop-only. The exceptions are the execution engine's:
``execution promote``, the owner's explicit, recorded grant the engine requires
before it may start (trading/execution/promotion.py), and ``execution run
--confirm``, which starts the engine by hand for its supervised drill
(trading/execution/runner.py). The engine is not wired: no supervisor child,
LaunchAgent or schedule runs it.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, time, timezone
import importlib
import json
import logging
from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
import signal
import sys
from typing import TYPE_CHECKING, Any

import aiosqlite

from config import cfg
from trading.operations.schema_gate import (
    TELEGRAM_ASSISTANT_REQUIRES,
    SchemaNotReady,
    refusal_exit,
    require_current_schema,
)
from trading.storage.runtime_fence import SQLitePaperKillStore

if TYPE_CHECKING:
    from trading.market.screener import SweepResult
    from trading.market.universe import ImportReport, QualificationReport
    from trading.storage.migrations import SchemaStatus


_DEFAULT_STOP_REASON = "operator stop via autotraderctl"
# Mirrors trading.operations.shadow_service.STRATEGIES / UNIVERSES (a test pins
# them); kept literal so parsing never imports the TWS and Claude stack.
_STRATEGY_CHOICES = ("orb", "momo", "vwap", "gap", "orb-v2", "vwap-v2", "gap-v2", "momo-ap")
# Managed entries (shadow_service.MANAGED_STRATEGIES) run their family's rules
# under the AI day manager; the rules-only replay covers them as that family.
_MANAGED_STRATEGY_CHOICES = ("momo-ap",)
_REPLAY_STRATEGY_CHOICES = tuple(
    name for name in _STRATEGY_CHOICES if name not in _MANAGED_STRATEGY_CHOICES
)
_UNIVERSE_CHOICES = ("scanner", "watchlist", "gappers", "all-gappers")
# Three weeks of five-minute bars: enough sessions for a first replay sample.
DEFAULT_BACKFILL_DAYS = 21
# The nightly recording keeps the whole replay sample filled: a contract with
# nothing missing sends no request, and one request reaches back to its oldest
# missing session. So the first night stores all three weeks (~30 minutes for
# ~400 contracts, inside the recorder's window) without a manual backfill, and
# a night the recorder missed (TWS down, Mac asleep) is filled in by the next.
RECORD_LOOKBACK_DAYS = DEFAULT_BACKFILL_DAYS
EXIT_INTERRUPTED = 130
# An orderly SIGTERM stop is a clean exit, as in paper_bridge.py: the
# supervisor's window-end stop must not read as a failure on the owner's phone.
EXIT_STOPPED = 0
NOTIFY_SERVICE_MODULE = "trading.operations.notify_service"
TELEGRAM_BOT_MODULE = "trading.operations.telegram_bot"


class _Terminated(BaseException):
    """SIGTERM inside synchronous work; a BaseException so no ``except Exception``
    swallows it on the way out, while every ``finally`` still runs."""


def _clock_time(value: str) -> time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected HH:MM, got {value!r}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autotraderctl")
    parser.add_argument("--db-path", type=Path, default=argparse.SUPPRESS)
    domains = parser.add_subparsers(dest="domain", required=True)

    paper = domains.add_parser("paper", help="control the local PAPER kill switch")
    paper.add_argument("--db-path", type=Path, default=argparse.SUPPRESS)
    actions = paper.add_subparsers(dest="action", required=True)

    stop = actions.add_parser("stop", help="permanently stop new PAPER entries")
    stop.add_argument("--db-path", type=Path, default=argparse.SUPPRESS)
    stop.add_argument("--reason", default=_DEFAULT_STOP_REASON)

    status = actions.add_parser("status", help="show the PAPER kill-switch state")
    status.add_argument("--db-path", type=Path, default=argparse.SUPPRESS)

    shadow = domains.add_parser("shadow", help="real-data shadow sessions (no orders)")
    shadow_actions = shadow.add_subparsers(dest="action", required=True)
    preflight = shadow_actions.add_parser("preflight", help="read-only preparation checks; does not enable orders")
    preflight.add_argument("--json", action="store_true", dest="json_output")
    run = shadow_actions.add_parser("run", help="run one strategy session in the foreground")
    run.add_argument(
        "--strategy", choices=_STRATEGY_CHOICES, default="orb", help="strategy (default orb)"
    )
    run.add_argument(
        "--universe",
        choices=_UNIVERSE_CHOICES,
        default="scanner",
        help="candidates from the 09:45 IBKR scan or the live screener hot list (default scanner)",
    )
    run.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help=(
            "session date (YYYY-MM-DD), refused after the strategy's cutoff; defaults to "
            "today's session while the strategy's entry window is open, else the next"
        ),
    )
    run.add_argument(
        "--new-run",
        metavar="LABEL",
        default=None,
        help="start a separate run for the day instead of resuming the existing one",
    )
    probe = shadow_actions.add_parser(
        "probe", help="read-only: show what TWS returns for scanner, bars, quote, account"
    )
    probe.add_argument("--con-id", type=int, default=None, help="probe this conId instead of the top scan row")
    probe.add_argument(
        "--with-claude",
        action="store_true",
        help="also send one synthetic setup to Claude (about $0.02)",
    )
    probe.add_argument(
        "--scanner-only",
        action="store_true",
        help=(
            "only the read-only scanner, news and market-data-line probe on the probe's own "
            "client id; reads no config, database or Keychain"
        ),
    )
    probe.add_argument("--out", type=Path, default=None, help="--scanner-only: write the JSON report here")
    probe.add_argument("--host", default=None, help="--scanner-only: TWS host (loopback only)")
    probe.add_argument("--port", type=int, default=None, help="--scanner-only: TWS PAPER port")
    probe.add_argument(
        "--client-id",
        type=int,
        default=None,
        help="--scanner-only: TWS client id (default: the probe's own; child ids are refused)",
    )
    probe.add_argument(
        "--fixtures-dir",
        type=Path,
        default=None,
        help="--scanner-only: also write trimmed public test fixtures here",
    )
    probe.add_argument(
        "--from-report",
        type=Path,
        default=None,
        help="--scanner-only --fixtures-dir: rebuild the fixtures from a saved report, without TWS",
    )
    agent = shadow_actions.add_parser(
        "agent", help="manage the LaunchAgent that keeps `autotraderctl run` alive"
    )
    agent.add_argument("agent_action", choices=("install", "enable", "disable", "status"))

    universe = domains.add_parser("universe", help="watchlist universe (market data only, no orders)")
    universe_actions = universe.add_subparsers(dest="action", required=True)
    universe_import = universe_actions.add_parser(
        "import", help="resolve a TradingView watchlist export to IBKR contracts"
    )
    universe_import.add_argument("path", type=Path, help="comma/newline separated EXCHANGE:SYMBOL list")
    universe_qualify = universe_actions.add_parser(
        "qualify", help="price/liquidity qualification of the universe for one session"
    )
    universe_qualify.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="session date (YYYY-MM-DD); defaults to today's or the next session",
    )

    screener = domains.add_parser("screener", help="live screener (market data only, no orders)")
    screener_actions = screener.add_subparsers(dest="action", required=True)
    screener_run = screener_actions.add_parser(
        "run", help="sweep the qualified universe 08:00-16:00 ET on trading days"
    )
    screener_run.add_argument(
        "--interval", type=float, default=60.0, help="seconds between sweep starts (default 60)"
    )
    screener_run.add_argument("--once", action="store_true", help="run one sweep now and exit")
    screener_run.add_argument(
        "--watchlist-only",
        action="store_true",
        help="do not merge movers from IBKR scans (HOT_BY_VOLUME, TOP_PERC_GAIN, MOST_ACTIVE)",
    )

    notify = domains.add_parser("notify", help="Telegram notifications to the owner's phone")
    notify_actions = notify.add_subparsers(dest="action", required=True)
    notify_run = notify_actions.add_parser(
        "run", help="relay the session's signals, orders, exits and child restarts"
    )
    notify_run.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="session date (YYYY-MM-DD); defaults to today's or the next session",
    )
    notify_actions.add_parser("test", help="send one test message")

    telegram = domains.add_parser(
        "telegram", help="the owner's Telegram assistant: data, questions, manual PAPER orders"
    )
    telegram_actions = telegram.add_subparsers(dest="action", required=True)
    telegram_actions.add_parser(
        "run",
        help="answer the pinned owner's messages (foreground; the supervisor keeps it running)",
    )
    telegram_actions.add_parser("whoami", help="show the pinned owner chat id (no secrets)")

    research = domains.add_parser(
        "research", help="strategy research: real five-minute bars and rule-only replay (no orders)"
    )
    research_actions = research.add_subparsers(dest="action", required=True)
    research_backfill = research_actions.add_parser(
        "backfill",
        help="store the newest closed sessions for the qualified universe (read-only TWS client 28)",
    )
    research_backfill.add_argument(
        "--days",
        type=int,
        default=DEFAULT_BACKFILL_DAYS,
        help=f"sessions per contract, 1-60 (default {DEFAULT_BACKFILL_DAYS}); stored ones are skipped",
    )
    research_actions.add_parser(
        "record",
        help=(
            "store the newest closed sessions (the nightly recording: backfill --days"
            f" {RECORD_LOOKBACK_DAYS}; only missing sessions are requested)"
        ),
    )
    research_replay = research_actions.add_parser(
        "replay", help="replay every stored session through the strategies' rules; write reports"
    )
    research_replay.add_argument(
        "--strategy",
        dest="strategies",
        nargs="+",
        choices=_REPLAY_STRATEGY_CHOICES,
        default=None,
        metavar="NAME",
        help=(
            f"strategies to replay, from {' '.join(_REPLAY_STRATEGY_CHOICES)} (default all; gap and"
            " gap-v2 are reported as not replayable: they need pre-market bars)"
        ),
    )
    research_replay.add_argument(
        "--notify",
        action="store_true",
        help="send the summary to Telegram now (otherwise once a week, after the week's last close)",
    )

    execution = domains.add_parser(
        "execution",
        help="the PAPER execution engine: its authority records and its hand-started run (never scheduled)",
    )
    execution.add_argument("--db-path", type=Path, default=argparse.SUPPRESS)
    execution_actions = execution.add_subparsers(dest="action", required=True)
    promote = execution_actions.add_parser(
        "promote", help="record the owner's explicit PAPER grant for one MOMO_HOD strategy and this engine code"
    )
    promote.add_argument("--db-path", type=Path, default=argparse.SUPPRESS)
    promote.add_argument("--strategy", required=True, help="catalogue name, e.g. MOMO_HOD_V2")
    promote.add_argument("--account", required=True, help="the DU paper account")
    promote.add_argument("--reason", required=True, help="why (kept in the append-only record)")
    promote.add_argument("--confirm", action="store_true", help="required: without it nothing is written")
    revoke = execution_actions.add_parser(
        "revoke", help="end every PAPER grant of a strategy on an account (always allowed)"
    )
    revoke.add_argument("--db-path", type=Path, default=argparse.SUPPRESS)
    revoke.add_argument("--strategy", required=True)
    revoke.add_argument("--account", required=True)
    revoke.add_argument("--reason", required=True)
    execution_status = execution_actions.add_parser(
        "status", help="read-only: the engine's code digest and the strategies it may trade"
    )
    execution_status.add_argument("--db-path", type=Path, default=argparse.SUPPRESS)
    execution_status.add_argument("--account", default=None)
    execution_run = execution_actions.add_parser(
        "run", help="run the PAPER execution engine for one session, by hand (the supervised drill only)"
    )
    execution_run.add_argument("--db-path", type=Path, default=argparse.SUPPRESS)
    execution_run.add_argument("--date", type=date.fromisoformat, default=None,
                               help="session date (YYYY-MM-DD); defaults to today in New York")
    execution_run.add_argument("--confirm", action="store_true",
                               help="required: the engine sends PAPER orders for promoted strategies")

    manager = domains.add_parser(
        "manager", help="the AI day manager (shadow) and the owner's autopilot switch"
    )
    manager.add_argument("--db-path", type=Path, default=argparse.SUPPRESS)
    manager_actions = manager.add_subparsers(dest="action", required=True)
    manager_run = manager_actions.add_parser(
        "run", help="write the session's day plans (shadow; no TWS, no orders)"
    )
    manager_run.add_argument(
        "--date", type=date.fromisoformat, default=None, help="session date (default today)"
    )
    for switch_action in ("on", "off"):
        switch = manager_actions.add_parser(
            switch_action,
            help=f"turn autopilot {switch_action} (managed MOMO latches it once per session)",
        )
        switch.add_argument("--db-path", type=Path, default=argparse.SUPPRESS)
        switch.add_argument("--reason", default=None)
    manager_status = manager_actions.add_parser(
        "status", help="the switch, today's latch, the plan in force and the spend"
    )
    manager_status.add_argument("--db-path", type=Path, default=argparse.SUPPRESS)

    database = domains.add_parser(
        "db", help="the database schema: the only place migrations are applied"
    )
    database.add_argument("--db-path", type=Path, default=argparse.SUPPRESS)
    database_actions = database.add_subparsers(dest="action", required=True)
    migrate = database_actions.add_parser(
        "migrate",
        help=(
            "apply pending migrations: exclusive lock, verified backup first, refused inside"
            " the supervised trading day (07:55-18:30 ET)"
        ),
    )
    migrate.add_argument("--db-path", type=Path, default=argparse.SUPPRESS)
    migrate.add_argument(
        "--during-session-i-know",
        action="store_true",
        dest="during_session",
        help="migrate inside the trading day anyway (children restart against the new schema)",
    )
    database_status = database_actions.add_parser(
        "status", help="read-only: applied and pending migrations (exit 75 when not current)"
    )
    database_status.add_argument("--db-path", type=Path, default=argparse.SUPPRESS)

    supervise = domains.add_parser(
        "run",
        help="supervise each trading day's screener, bridge, strategies and notifier (foreground)",
    )
    supervise.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned child timeline on a simulated clock; launch nothing",
    )
    supervise.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="only this session (YYYY-MM-DD); without it every session runs, back to back",
    )
    supervise.add_argument(
        "--clock-start",
        type=_clock_time,
        default=None,
        metavar="HH:MM",
        help="with --dry-run: simulate a supervisor started at this ET time",
    )
    return parser


def _shadow_agent(action: str) -> int:
    from trading.operations.launchd import LaunchdInstallError, LaunchdManager

    manager = LaunchdManager.current()
    try:
        if action == "install":
            print(f"LaunchAgent written (not loaded): {manager.install()}")
        elif action == "enable":
            manager.enable()
            print("LaunchAgent enabled: the supervisor (autotraderctl run) starts at login and is kept alive")
        elif action == "disable":
            manager.disable()
            print("LaunchAgent disabled")
        else:
            print(f"installed: {manager.is_installed()}  loaded: {manager.is_loaded()}")
    except LaunchdInstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


async def _execute(arguments: argparse.Namespace) -> int:
    original_db_path = cfg.DB_PATH
    requested_db_path = getattr(arguments, "db_path", None)
    if requested_db_path is not None:
        cfg.DB_PATH = str(requested_db_path.expanduser())
    try:
        # The kill switch never migrates and never waits for a migration: it
        # writes only the fence row of an existing database, whatever else is
        # pending, so the owner can always stop new PAPER entries.
        database = Path(cfg.DB_PATH).expanduser()
        if not database.is_file():
            print(
                f"error: no database at {database}; create it with `autotraderctl db migrate`",
                file=sys.stderr,
            )
            return 1
        kill_store = SQLitePaperKillStore()
        if arguments.action == "stop":
            await kill_store.stop(reason=arguments.reason)
            print("PAPER kill switch: ACTIVE")
            return 0
        stopped = await kill_store.is_stopped()
        print(f"PAPER kill switch: {'ACTIVE' if stopped else 'INACTIVE'}")
        return 0
    except (OSError, ValueError, RuntimeError, aiosqlite.Error):
        print("error: PAPER control command could not be completed", file=sys.stderr)
        return 1
    finally:
        cfg.DB_PATH = original_db_path


async def _backup_before_promote(database: Path) -> Path:
    """A non-overwriting SQLite backup of the database, taken before PAPER authority is written into it."""
    from trading.storage.migrations import backup_database

    directory = database.parent / "backups"
    directory.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    async with aiosqlite.connect(str(database)) as db:
        return await backup_database(db, directory / f"{database.stem}.before-execution-promote-{stamp}.db")


EXIT_SCHEMA_REFUSED = 78  # EX_CONFIG: promote and revoke never migrate; a retry cannot fix it


async def _execution_command(arguments: argparse.Namespace) -> int:
    """Authority records only: nothing here connects to TWS, sends an order or migrates."""
    from trading.execution.promotion import (
        ExecutionAuthorityError,
        active_strategies,
        engine_code_digest,
        grant_promotion,
        promotion_history,
        require_grantable,
        revoke_promotion,
    )
    from trading.operations.schema_gate import MIGRATE_COMMAND
    from trading.storage.execution_ledger_schema import EXECUTION_LEDGER_MIGRATION
    from trading.storage.migrations import inspect_schema

    if arguments.action == "promote" and not arguments.confirm:
        print("error: promote writes PAPER authority for the execution engine; re-run with --confirm",
              file=sys.stderr)
        return 2
    original_db_path = cfg.DB_PATH
    requested_db_path = getattr(arguments, "db_path", None)
    if requested_db_path is not None:
        cfg.DB_PATH = str(requested_db_path.expanduser())
    try:
        digest = engine_code_digest()
        if arguments.action == "status":
            # Read-only: never creates a database, and one without the ledger migration has no promotions.
            if cfg.DB_PATH != ":memory:" and not Path(cfg.DB_PATH).expanduser().is_file():
                print("no autotrader database: no PAPER promotion")
                return 0
            try:
                records = await promotion_history(account=arguments.account)
            except aiosqlite.OperationalError:
                print(f"execution ledger not installed (migration {EXECUTION_LEDGER_MIGRATION} not applied):"
                      " no PAPER promotion")
                return 0
            print(f"execution engine code: {digest}")
            print("execution engine: not wired; only `execution run --confirm` starts it, by hand (the PAPER"
                  " bridge is the scheduled order sender)")
            accounts = [arguments.account] if arguments.account else sorted({item.account for item in records})
            for account in accounts:
                active = active_strategies(records, account=account, code_digest=digest)
                print(f"{account}: {', '.join(sorted(active)) or 'no active promotion for this code'}")
            if not accounts:
                print("no promotion records")
            return 0
        # promote and revoke write one authority row into the existing ledger and never migrate: the
        # owner's database is migrated only by `autotraderctl db migrate` from the deployed main line.
        verb = "promoted" if arguments.action == "promote" else "revoked"
        if arguments.action == "promote":
            require_grantable(arguments.strategy, arguments.account)  # refused before the database is touched
        database = Path(cfg.DB_PATH).expanduser()
        if cfg.DB_PATH == ":memory:" or not database.is_file():
            print(f"error: no autotrader database at {database} (check --db-path); nothing {verb}",
                  file=sys.stderr)
            return EXIT_SCHEMA_REFUSED
        schema = inspect_schema(database)
        if not schema.current:
            print(f"error: the database schema is not this code's ({schema.describe()}); run"
                  f" `{MIGRATE_COMMAND}` from the deployed main line once it knows migration"
                  f" {EXECUTION_LEDGER_MIGRATION}, then retry; nothing {verb}", file=sys.stderr)
            return EXIT_SCHEMA_REFUSED
        if arguments.action == "promote":
            try:
                backup = await _backup_before_promote(database)
            except (OSError, RuntimeError, aiosqlite.Error) as exc:
                print(f"error: database backup failed ({type(exc).__name__}: {exc}) — nothing promoted",
                      file=sys.stderr)
                return 1
            print(f"database backed up to {backup}")
        if arguments.action == "promote":
            record = await grant_promotion(
                strategy_name=arguments.strategy, account=arguments.account, actor="owner via autotraderctl",
                reason=arguments.reason, code_digest=digest,
            )
            print(f"PAPER promotion recorded: {record.strategy_name} on {record.account} for engine code"
                  f" {digest[:12]} (the engine is not wired; nothing runs it)")
            return 0
        record = await revoke_promotion(
            strategy_name=arguments.strategy, account=arguments.account, actor="owner via autotraderctl",
            reason=arguments.reason,
        )
        print(f"PAPER promotion revoked: {record.strategy_name} on {record.account}")
        return 0
    except ExecutionAuthorityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, aiosqlite.Error):
        print("error: execution command could not be completed", file=sys.stderr)
        return 1
    finally:
        cfg.DB_PATH = original_db_path


async def _cancel_on_sigterm(work: Coroutine[Any, Any, int]) -> int:
    """Await ``work``; SIGTERM cancels it so its finally blocks disconnect TWS.

    Without a handler SIGTERM kills Python outright: no finally block runs,
    the TWS client ID stays occupied, and the shadow-run lease is held for its
    full TTL. The orderly stop exits 0. SIGINT keeps asyncio's own handling.
    """
    loop = asyncio.get_running_loop()
    task = asyncio.ensure_future(work)
    terminated = False

    def terminate() -> None:
        nonlocal terminated
        terminated = True
        task.cancel()

    loop.add_signal_handler(signal.SIGTERM, terminate)
    try:
        return await task
    except asyncio.CancelledError:
        if not terminated:
            raise
        print("stopped by SIGTERM; connections closed", file=sys.stderr, flush=True)
        return EXIT_STOPPED
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


def _shadow_run(arguments: argparse.Namespace) -> int:
    """One real-data strategy session; logs go to the private shadow log."""
    from auth import DEMO_USER_ID
    from trading.operations.logging import configure_shadow_logging
    from trading.operations.settings import load_shadow_runtime_config
    from trading.operations.shadow_service import run_shadow_day, session_for_strategy
    from trading.orchestration.session_runner import SessionAborted

    logger = configure_shadow_logging()
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(console)
    label = f"shadow run --strategy {arguments.strategy}"

    async def session_run() -> int:
        # Pure calendar check first: a closed window never touches the database.
        session = session_for_strategy(
            arguments.strategy, now=datetime.now(timezone.utc), requested=arguments.date
        )
        # Never migrates: a pending migration refuses with exit 75.
        require_current_schema(process=label)
        report = await run_shadow_day(
            config=load_shadow_runtime_config(),
            owner_id=DEMO_USER_ID,
            session=session,
            strategy_name=arguments.strategy,
            universe=arguments.universe,
            run_label=arguments.new_run,
        )
        print(json.dumps(report.summary(), indent=2))
        return 0

    try:
        return asyncio.run(_cancel_on_sigterm(session_run()))
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except SchemaNotReady as exc:
        return refusal_exit(exc)
    except SessionAborted as exc:
        logger.error("%s aborted: %s", label, exc)
        print(f"error: {label}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - reported without secrets
        logger.exception("%s failed", label)
        print(f"error: {label} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _interactive_screener_logger() -> logging.Logger:
    from trading.operations.logging import configure_shadow_logging

    logger = configure_shadow_logging()
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(console)
    return logging.getLogger("autotrader.shadow.screener")


def _print_import(report: ImportReport) -> None:
    print(f"watchlist symbols: {report.parsed} (duplicates skipped: {len(report.duplicates)})")
    print(f"resolved:          {report.resolved}")
    print(f"  via retry without exchange hint: {', '.join(report.used_fallback) or 'none'}")
    print(f"unresolved:        {len(report.unresolved)}")
    for row in report.unresolved:
        label = f"{row.listing_exchange}:{row.symbol}" if row.listing_exchange else row.symbol
        print(f"  {label}: {row.last_error}")
    print(f"removed (no longer in export): {', '.join(report.removed) or 'none'}")


def _print_qualification(report: QualificationReport) -> None:
    print(f"session {report.session_date.isoformat()}: {report.instruments} resolved instruments")
    print(f"  requested now: {report.requested}  already final: {report.skipped}")
    print(f"  qualified: {report.qualified}")
    for reason, count in report.rejected:
        print(f"  not qualified ({reason}): {count}")
    print(f"  transient errors (retried next run): {len(report.errors)}")
    for symbol, reason in report.errors[:20]:
        print(f"    {symbol}: {reason}")


def _print_sweep(result: SweepResult, *, top: int = 10) -> None:
    payload = result.payload
    print(
        f"sweep {payload.session_date} observed {payload.observed_at}: "
        f"universe {payload.universe_size}, qualified {payload.qualified}, "
        f"quoted {payload.quoted}, errors {payload.errors}, {payload.sweep_seconds:.1f} s"
    )
    hot = [row for row in payload.rows if row.hot][:top]
    print(f"{'hot':>3}  {'symbol':<7} {'last':>9} {'chg%':>7} {'gap%':>7} {'rvol':>6} {'score':>7}  source")
    for row in hot:
        print(
            f"{row.hot_rank:>3}  {row.symbol:<7} {row.last or '-':>9} {row.change_pct or '-':>7} "
            f"{row.gap_pct or '-':>7} {row.rvol or '-':>6} {row.score:>7}  {row.source}"
        )
    scan_rows = [row.symbol for row in payload.rows if row.source == "ibkr_scan"]
    print(f"IBKR scan additions: {', '.join(scan_rows) or 'none'}")
    for sample in result.error_samples:
        print(f"  error: {sample}")


def _market_data_command(arguments: argparse.Namespace) -> int:
    """Universe import/qualify and the live screener; market data only."""
    from trading.market.tws_actor import TWSCompetingSessionError
    from trading.operations import screener_service as service
    from trading.operations.settings import load_shadow_runtime_config

    logger = _interactive_screener_logger()
    original_db_path = cfg.DB_PATH
    requested_db_path = getattr(arguments, "db_path", None)
    if requested_db_path is not None:
        cfg.DB_PATH = str(requested_db_path.expanduser())

    async def command() -> int:
        # Never migrates: a pending migration refuses with exit 75.
        require_current_schema(process=f"{arguments.domain} {arguments.action}")
        config = load_shadow_runtime_config()
        if arguments.domain == "universe" and arguments.action == "import":
            _print_import(await service.import_universe(arguments.path, runtime_config=config))
            return 0
        if arguments.domain == "universe":
            session_date = arguments.date or service.screener_session_date(
                datetime.now(timezone.utc)
            )
            _print_qualification(
                await service.qualify_session(session_date, runtime_config=config)
            )
            return 0
        if arguments.once:
            qualification, sweep = await service.run_screener_once(
                runtime_config=config, include_scans=not arguments.watchlist_only
            )
            _print_qualification(qualification)
            _print_sweep(sweep)
            return 0
        await service.run_screener(
            runtime_config=config,
            interval_seconds=arguments.interval,
            include_scans=not arguments.watchlist_only,
        )
        return 0

    label = f"{arguments.domain} {arguments.action}"
    try:
        return asyncio.run(_cancel_on_sigterm(command()))
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except SchemaNotReady as exc:
        return refusal_exit(exc)
    except TWSCompetingSessionError as exc:
        logger.error("%s (%s)", service.COMPETING_SESSION_MESSAGE, exc)
        print(f"error: {service.COMPETING_SESSION_MESSAGE}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - reported without secrets
        logger.exception("%s failed", label)
        print(f"error: {label} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        cfg.DB_PATH = original_db_path


def _notify_command(arguments: argparse.Namespace) -> int:
    """Telegram notifier; its module is loaded only when a notify command runs."""
    try:
        service = importlib.import_module(NOTIFY_SERVICE_MODULE)
    except ImportError as exc:
        print(f"error: the notifier is unavailable ({type(exc).__name__})", file=sys.stderr)
        return 1
    if arguments.action == "test":
        work = service.send_test_message()
    else:
        session_date = arguments.date
        if session_date is None:
            from trading.operations.screener_service import screener_session_date

            session_date = screener_session_date(datetime.now(timezone.utc))
        work = service.run_notifier(session_date=session_date)
    try:
        return asyncio.run(_cancel_on_sigterm(work))
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except Exception as exc:  # noqa: BLE001 - type only: a message could carry the bot token
        print(f"error: notify {arguments.action} failed: {type(exc).__name__}", file=sys.stderr)
        return 1


async def _alert_schema_not_ready(status: SchemaStatus) -> None:
    """Best effort, at most once per day: the refusal stands whatever the alert does."""
    try:
        from trading.operations import schema_alert

        await schema_alert.send_schema_alert(status)
    except Exception:  # noqa: BLE001 - the alert must never turn a refusal into a crash
        pass


def _telegram_command(arguments: argparse.Namespace) -> int:
    """The Telegram assistant; its module is loaded only when a telegram command runs."""
    try:
        service = importlib.import_module(TELEGRAM_BOT_MODULE)
    except ImportError as exc:
        print(f"error: the Telegram assistant is unavailable ({type(exc).__name__})", file=sys.stderr)
        return 1
    if arguments.action == "whoami":
        return service.print_owner()

    async def serve() -> int:
        # The always-on assistant never migrates (reverts 7354b89). The owner's
        # /stop and manual exits matter more than strictness: it starts while
        # only migrations newer than its own tables are pending, and refuses
        # (exit 75, retried) a missing migration it needs or any other schema
        # problem. It runs at every hour, so either way it tells the phone
        # right away, at most once per day.
        try:
            status = require_current_schema(
                process="telegram run", requires=TELEGRAM_ASSISTANT_REQUIRES
            )
        except SchemaNotReady as exc:
            await _alert_schema_not_ready(exc.status)
            return refusal_exit(exc)
        if not status.current:
            await _alert_schema_not_ready(status)
        return await service.run_telegram_assistant()

    try:
        return asyncio.run(_cancel_on_sigterm(serve()))
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except Exception as exc:  # noqa: BLE001 - type only: a message could carry the bot token
        print(f"error: telegram {arguments.action} failed: {type(exc).__name__}", file=sys.stderr)
        return 1


def _interactive_research_logger() -> logging.Logger:
    """Shadow log plus console: a supervised run's progress lands in its child log."""
    from trading.operations.logging import configure_shadow_logging

    logger = configure_shadow_logging()
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(console)
    return logging.getLogger("autotrader.shadow.research")


def _run_sync_until_sigterm(work: Callable[[], int]) -> int:
    """Run synchronous ``work``; SIGTERM unwinds it (its finally blocks and atomic
    writes clean up) and is an orderly stop, as ``_cancel_on_sigterm`` is for
    async work. SIGINT keeps Python's own KeyboardInterrupt."""

    def terminate(_signum: int, _frame: object) -> None:
        raise _Terminated

    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        return work()
    except _Terminated:
        print("stopped by SIGTERM", file=sys.stderr, flush=True)
        return EXIT_STOPPED
    finally:
        signal.signal(signal.SIGTERM, previous)


def _research_command(arguments: argparse.Namespace) -> int:
    """Research bars and replay. ``backfill`` and ``record`` read TWS PAPER market
    data on client 28 (they refuse a non-paper login); ``replay`` reads the
    research store and, after a full replay, runs the nightly evidence
    (trading.research.nightly: it writes only the signal-outcome table, and only
    on a database at this code's schema). Nothing here can place an order."""
    logger = _interactive_research_logger()
    label = f"research {arguments.action}"
    if arguments.action == "replay":
        from trading.research.replay import run_replay_cli

        mode = "now" if arguments.notify else "weekly"
        try:
            return _run_sync_until_sigterm(
                lambda: run_replay_cli(arguments.strategies or [], notify=mode)
            )
        except KeyboardInterrupt:
            return EXIT_INTERRUPTED
        except Exception as exc:  # noqa: BLE001 - research text carries no secrets
            logger.exception("%s failed", label)
            print(f"error: {label} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    from trading.research.recorder import run_backfill_cli

    # The recorder has no date of its own: it always stores the newest closed
    # sessions, and the nightly lookback refills any night that was missed.
    days = RECORD_LOOKBACK_DAYS if arguments.action == "record" else arguments.days
    original_db_path = cfg.DB_PATH
    requested_db_path = getattr(arguments, "db_path", None)
    if requested_db_path is not None:
        cfg.DB_PATH = str(requested_db_path.expanduser())
    try:
        # It only reads the universe tables, but every database child waits
        # for `db migrate` alike; a missed night is filled by the next one.
        require_current_schema(process=label)
        return asyncio.run(_cancel_on_sigterm(run_backfill_cli(days)))
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except SchemaNotReady as exc:
        return refusal_exit(exc)
    except Exception as exc:  # noqa: BLE001 - research text carries no secrets
        logger.exception("%s failed", label)
        print(f"error: {label} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        cfg.DB_PATH = original_db_path


def _manager_command(arguments: argparse.Namespace) -> int:
    """``manager run`` (the supervised child) and ``manager on|off|status`` (the switch)."""
    from trading.manager import service

    if arguments.action != "run":
        original = cfg.DB_PATH
        requested = getattr(arguments, "db_path", None)
        if requested is not None:
            cfg.DB_PATH = str(requested.expanduser())
        try:
            database = Path(cfg.DB_PATH).expanduser()
            if not database.is_file():
                print(
                    f"error: no database at {database}; create it with `autotraderctl db migrate`",
                    file=sys.stderr,
                )
                return 1
            return asyncio.run(
                service.switch_command(
                    arguments.action,
                    database=str(database),
                    reason=getattr(arguments, "reason", None),
                )
            )
        except (OSError, ValueError, RuntimeError, aiosqlite.Error) as exc:
            print(f"error: manager {arguments.action} failed: {type(exc).__name__}", file=sys.stderr)
            return 1
        finally:
            cfg.DB_PATH = original

    from trading.operations.logging import configure_shadow_logging

    logger = configure_shadow_logging()
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(console)
    label = "manager run"
    session = service.session_for(arguments.date, now=datetime.now(timezone.utc))
    if session is None:
        print(f"error: {label}: not an XNYS trading session", file=sys.stderr)
        return 1

    async def plan_day() -> int:
        # Never migrates: a pending migration refuses with exit 75.
        require_current_schema(process=label)
        summary = await service.run_manager(
            session=session, client_factory=service.anthropic_client_factory
        )
        print(json.dumps(summary, indent=2))
        return 0

    try:
        return asyncio.run(_cancel_on_sigterm(plan_day()))
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except SchemaNotReady as exc:
        return refusal_exit(exc)
    except Exception as exc:  # noqa: BLE001 - reported without secrets
        logger.exception("%s failed", label)
        print(f"error: {label} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _database_command(arguments: argparse.Namespace) -> int:
    """``db migrate`` / ``db status``: the explicit, locked, backed-up schema change."""
    from trading.operations import db_admin

    database = Path(cfg.DB_PATH)
    requested_db_path = getattr(arguments, "db_path", None)
    if requested_db_path is not None:
        database = requested_db_path.expanduser()
    if arguments.action == "status":
        return db_admin.run_status(database)
    return db_admin.run_migrate(database, allow_during_session=arguments.during_session)


def _supervise_command(arguments: argparse.Namespace) -> int:
    """``autotraderctl run``: the foreground supervisor, or its simulated timeline."""
    from trading.operations import supervisor

    if not arguments.dry_run:
        return supervisor.run_supervisor(session_date=arguments.date)
    try:
        report = supervisor.dry_run_report(
            session_date=arguments.date, clock_start=arguments.clock_start
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(report)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run an explicitly requested local operator command."""

    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.domain == "run":
        if arguments.clock_start is not None and not arguments.dry_run:
            parser.error("--clock-start requires --dry-run")
        if getattr(arguments, "db_path", None) is not None:
            parser.error("--db-path does not apply to run; its children use the canonical database")
        return _supervise_command(arguments)
    if arguments.domain == "db":
        return _database_command(arguments)
    if arguments.domain == "manager":
        return _manager_command(arguments)
    if arguments.domain == "notify":
        return _notify_command(arguments)
    if arguments.domain == "telegram":
        if getattr(arguments, "db_path", None) is not None:
            parser.error("--db-path does not apply to telegram; the assistant uses the canonical database")
        return _telegram_command(arguments)
    if arguments.domain == "research":
        if arguments.action == "replay" and getattr(arguments, "db_path", None) is not None:
            parser.error("--db-path does not apply to research replay; it reads the research store")
        return _research_command(arguments)
    if arguments.domain in {"universe", "screener"}:
        return _market_data_command(arguments)
    if arguments.domain == "execution" and arguments.action == "run":
        from trading.execution.runner import main as run_engine

        return run_engine(arguments.date, confirm=arguments.confirm, db_path=getattr(arguments, "db_path", None))
    if arguments.domain == "execution":
        return asyncio.run(_execution_command(arguments))
    if arguments.domain == "shadow" and arguments.action == "run":
        return _shadow_run(arguments)
    if arguments.domain == "shadow" and arguments.action == "agent":
        return _shadow_agent(arguments.agent_action)
    if arguments.domain == "shadow" and arguments.action == "probe":
        scanner_options = ("out", "host", "port", "client_id", "fixtures_dir", "from_report")
        if arguments.scanner_only:
            if arguments.con_id is not None or arguments.with_claude:
                parser.error("--con-id and --with-claude do not apply to --scanner-only")
            from trading.operations.scanner_probe import run_cli as run_scanner_probe

            return run_scanner_probe(
                **{name: getattr(arguments, name) for name in scanner_options}
            )
        given = [name for name in scanner_options if getattr(arguments, name) is not None]
        if given:
            parser.error(f"--{given[0].replace('_', '-')} requires --scanner-only")
        from trading.operations.probe import probe_tws
        from trading.operations.settings import load_shadow_runtime_config

        report = asyncio.run(
            probe_tws(
                load_shadow_runtime_config(),
                con_id=arguments.con_id,
                include_ai=arguments.with_claude,
            )
        )
        print(json.dumps(report, indent=2, default=str))
        return 0
    if arguments.domain == "shadow":
        from trading.operations.readiness import check_shadow_preflight

        report = asyncio.run(check_shadow_preflight())
        if arguments.json_output:
            print(json.dumps(report, indent=2))
        else:
            print("Local preparation only — not a completed broker preflight")
            for check in report["checks"]:
                print(f"{check['name']}: {check['status']} — {check['detail']}")
            print("Orders: BLOCKED")
        return 2
    return asyncio.run(_execute(arguments))


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
