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

import aiosqlite

from config import cfg
from db.core import init_db
from trading.storage.runtime_fence import SQLitePaperKillStore


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


def main(argv: Sequence[str] | None = None) -> int:
    """Run an explicitly requested local operator command."""

    arguments = _parser().parse_args(argv)
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
