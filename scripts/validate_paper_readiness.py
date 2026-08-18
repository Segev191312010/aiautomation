#!/usr/bin/env python3
"""Validate PAPER preflight and an optional evidence bundle offline.

This command never opens a network connection and never changes application
configuration. It is intentionally a preflight, not proof that a PAPER soak
was executed; the bundle checks require operator-produced artifacts.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Mapping

PAPER_PORTS = {7497, 4002}
REQUIRED_BUNDLE_FILES = (
    "session.json",
    "metrics.jsonl",
    "signals.jsonl",
    "health.jsonl",
    "restart-check.json",
    "logs.txt",
)


def load_env_file(path: Path) -> dict[str, str]:
    """Read simple KEY=VALUE lines without expanding or printing secrets."""
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"{path}:{number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            raise ValueError(f"{path}:{number}: invalid environment key")
        values[key] = value.strip().strip("'\"")
    return values


def check_paper_environment(env: Mapping[str, str]) -> list[str]:
    """Return safe, actionable failures for a real IBKR PAPER preflight."""
    failures: list[str] = []
    if env.get("AUTOPILOT_MODE", "").upper() != "PAPER":
        failures.append("AUTOPILOT_MODE must be PAPER")
    if env.get("IS_PAPER", "").lower() != "true":
        failures.append("IS_PAPER must be true")
    if env.get("SIM_MODE", "").lower() != "false":
        failures.append("SIM_MODE must be false for the real IBKR PAPER drill")
    try:
        port = int(env.get("IBKR_PORT", ""))
    except ValueError:
        port = None
    if port not in PAPER_PORTS:
        failures.append("IBKR_PORT must be 7497 (TWS) or 4002 (Gateway) for PAPER")
    if env.get("CLAUDE_WORKER_ENABLED", "").lower() != "true":
        failures.append("CLAUDE_WORKER_ENABLED must be true")
    if not env.get("TV_WEBHOOK_SECRET", ""):
        failures.append("TV_WEBHOOK_SECRET must be set (value is never displayed)")
    if env.get("TV_IP_STRICT", "").lower() != "true":
        failures.append("TV_IP_STRICT must be true")
    if not env.get("TV_ALLOWED_IPS", "").strip():
        failures.append("TV_ALLOWED_IPS must contain the approved TradingView egress IPs")
    if env.get("METRICS_EXPOSURE_PROFILE", "").lower() != "isolated":
        failures.append("METRICS_EXPOSURE_PROFILE must be isolated")
    return failures


def check_bundle(path: Path) -> list[str]:
    """Check operator artifacts without interpreting or uploading their data."""
    if not path.is_dir():
        return [f"evidence bundle does not exist: {path}"]
    return [f"missing evidence artifact: {name}" for name in REQUIRED_BUNDLE_FILES if not (path / name).is_file()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="optional .env file to validate")
    parser.add_argument("--bundle", type=Path, help="optional operator evidence bundle directory")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit machine-readable output")
    args = parser.parse_args()
    env = dict(os.environ)
    parse_error = None
    if args.env_file:
        try:
            env.update(load_env_file(args.env_file))
        except (OSError, ValueError) as exc:
            parse_error = str(exc)
    failures = ([parse_error] if parse_error else []) + check_paper_environment(env)
    bundle_failures = check_bundle(args.bundle) if args.bundle else []
    failures.extend(bundle_failures)
    result = {
        "paper_preflight": "PASS" if not failures else "FAIL",
        "bundle": "PASS" if args.bundle and not bundle_failures else ("NOT_CHECKED" if not args.bundle else "FAIL"),
        "live_authorized": False,
        "failures": failures,
    }
    if args.as_json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"PAPER preflight: {result['paper_preflight']}")
        print(f"Evidence bundle: {result['bundle']}")
        print("LIVE authorized: NO")
        for failure in failures:
            print(f"- {failure}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
