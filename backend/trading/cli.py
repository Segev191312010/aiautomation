"""Local, stop-only operator controls for the Autotrader V1 runtime."""
from __future__ import annotations

import argparse
import asyncio
import json
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

    shadow = domains.add_parser("shadow", help="inspect local shadow preparation")
    shadow_actions = shadow.add_subparsers(dest="action", required=True)
    preflight = shadow_actions.add_parser("preflight", help="read-only preparation checks; does not enable orders")
    preflight.add_argument("--json", action="store_true", dest="json_output")
    return parser


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


def main(argv: Sequence[str] | None = None) -> int:
    """Run an explicitly requested local PAPER control command."""

    arguments = _parser().parse_args(argv)
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
