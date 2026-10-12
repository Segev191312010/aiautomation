"""Local, stop-only operator controls for the Autotrader V1 runtime."""
from __future__ import annotations

import argparse
import asyncio
from datetime import date
import json
import logging
from collections.abc import Sequence
from pathlib import Path
import sys
from typing import TYPE_CHECKING

import aiosqlite

from config import cfg
from db.core import init_db
from trading.storage.runtime_fence import SQLitePaperKillStore

if TYPE_CHECKING:
    from trading.market.screener import SweepResult
    from trading.market.universe import ImportReport, QualificationReport


_DEFAULT_STOP_REASON = "operator stop via autotraderctl"


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
    run = shadow_actions.add_parser("run", help="run one shadow session in the foreground")
    run.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="session date (YYYY-MM-DD); defaults to today's or the next session",
    )
    run.add_argument(
        "--new-run",
        metavar="LABEL",
        default=None,
        help="start a separate run for the day instead of resuming the existing one",
    )
    shadow_actions.add_parser("daemon", help="run every shadow session unattended (launchd)")
    probe = shadow_actions.add_parser(
        "probe", help="read-only: show what TWS returns for scanner, bars, quote, account"
    )
    probe.add_argument("--con-id", type=int, default=None, help="probe this conId instead of the top scan row")
    probe.add_argument(
        "--with-claude",
        action="store_true",
        help="also send one synthetic setup to Claude (about $0.02)",
    )
    agent = shadow_actions.add_parser("agent", help="manage the launchd agent that runs the daemon")
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
    return parser


def _shadow_agent(action: str) -> int:
    from trading.operations.launchd import LaunchdInstallError, LaunchdManager

    manager = LaunchdManager.current()
    try:
        if action == "install":
            print(f"LaunchAgent written (not loaded): {manager.install()}")
        elif action == "enable":
            manager.enable()
            print("LaunchAgent enabled: the shadow daemon runs at login and restarts on failure")
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
        await init_db()
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


def _shadow_runtime(arguments: argparse.Namespace) -> int:
    """Real-data shadow session or daemon; logs go to the private shadow log."""
    from auth import DEMO_USER_ID
    from trading.operations.logging import configure_shadow_logging
    from trading.operations.settings import load_shadow_runtime_config
    from trading.operations.shadow_service import (
        run_shadow_daemon,
        run_shadow_session,
        session_to_run,
    )
    from trading.scheduler.sessions import market_session

    logger = configure_shadow_logging()
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(console)

    async def foreground() -> int:
        await init_db()
        config = load_shadow_runtime_config()
        if arguments.date is not None:
            session = market_session(arguments.date)
            if session is None:
                print(f"error: {arguments.date} is not a trading session", file=sys.stderr)
                return 1
        else:
            from datetime import datetime, timezone

            session = session_to_run(datetime.now(timezone.utc))
        report = await run_shadow_session(
            config=config, owner_id=DEMO_USER_ID, session=session, run_label=arguments.new_run
        )
        print(json.dumps(report.summary(), indent=2))
        return 0

    async def daemon() -> int:
        await init_db()
        await run_shadow_daemon(config_loader=load_shadow_runtime_config, owner_id=DEMO_USER_ID)
        return 0

    try:
        return asyncio.run(foreground() if arguments.action == "run" else daemon())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - reported without secrets
        logger.exception("shadow %s failed", arguments.action)
        print(f"error: shadow {arguments.action} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
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
    from datetime import datetime, timezone

    from trading.market.tws_actor import TWSCompetingSessionError
    from trading.operations import screener_service as service
    from trading.operations.settings import load_shadow_runtime_config

    logger = _interactive_screener_logger()
    original_db_path = cfg.DB_PATH
    requested_db_path = getattr(arguments, "db_path", None)
    if requested_db_path is not None:
        cfg.DB_PATH = str(requested_db_path.expanduser())

    async def command() -> int:
        await init_db()
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
        return asyncio.run(command())
    except KeyboardInterrupt:
        return 130
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


def main(argv: Sequence[str] | None = None) -> int:
    """Run an explicitly requested local operator command."""

    arguments = _parser().parse_args(argv)
    if arguments.domain in {"universe", "screener"}:
        return _market_data_command(arguments)
    if arguments.domain == "shadow" and arguments.action in {"run", "daemon"}:
        return _shadow_runtime(arguments)
    if arguments.domain == "shadow" and arguments.action == "agent":
        return _shadow_agent(arguments.agent_action)
    if arguments.domain == "shadow" and arguments.action == "probe":
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
