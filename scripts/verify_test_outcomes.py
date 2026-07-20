#!/usr/bin/env python3
"""Fail-closed validation of machine-readable pytest/Vitest test outcomes.

The report contract is deliberately small: pytest-json-report's ``tests``
array and Vitest's ``testResults``/``assertionResults`` are accepted.  The
allowlist is an authorization record, not a way to hide failures.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

BAD = {"failed", "error", "xpass", "unexpected_pass"}
SKIP = {"skipped", "skip", "pending", "todo", "disabled", "xfail"}


class VerificationError(ValueError):
    pass


def _allowlist(path: Path, today: dt.date) -> dict[str, dict[str, Any]]:
    raw = json.loads(path.read_text())
    if raw.get("schema_version") != 1 or not isinstance(raw.get("entries"), list):
        raise VerificationError("invalid test-outcome allowlist schema")
    out: dict[str, dict[str, Any]] = {}
    for item in raw["entries"]:
        if not isinstance(item, dict) or not isinstance(item.get("test_id"), str):
            raise VerificationError("allowlist entries require test_id")
        key = item["test_id"]
        if key in out:
            raise VerificationError(f"duplicate allowlist test_id: {key}")
        if item.get("status") not in {"skip", "xfail"} or not item.get("owner") or not item.get("reason"):
            raise VerificationError(f"incomplete allowlist entry: {key}")
        try:
            expires = dt.date.fromisoformat(item["expires"])
        except (KeyError, TypeError, ValueError) as exc:
            raise VerificationError(f"invalid expiry for {key}") from exc
        if expires < today:
            raise VerificationError(f"expired allowlist entry: {key}")
        out[key] = item
    return out


def _records(raw: dict[str, Any], framework: str) -> list[tuple[str, str]]:
    if framework == "pytest":
        values = raw.get("tests")
        if not isinstance(values, list):
            raise VerificationError("pytest report lacks tests array")
        result = []
        for item in values:
            if not isinstance(item, dict) or not item.get("nodeid") or not item.get("outcome"):
                raise VerificationError("malformed pytest test record")
            result.append((item["nodeid"], str(item["outcome"]).lower()))
        return result
    values = raw.get("testResults")
    if not isinstance(values, list):
        raise VerificationError("vitest report lacks testResults array")
    result = []
    for suite in values:
        for item in suite.get("assertionResults", []) if isinstance(suite, dict) else []:
            if not isinstance(item, dict) or not item.get("fullName") or not item.get("status"):
                raise VerificationError("malformed Vitest assertion record")
            result.append((item["fullName"], str(item["status"]).lower()))
    return result


def verify_report(report: Path, allowlist: Path, framework: str, today: dt.date | None = None) -> dict[str, Any]:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    allowed = _allowlist(allowlist, today)
    try:
        raw = json.loads(report.read_text())
    except FileNotFoundError as exc:
        raise VerificationError(f"missing report: {report}") from exc
    records = _records(raw, framework)
    if not records:
        raise VerificationError("empty test selection/report")
    errors: list[str] = []
    for test_id, status in records:
        if status in BAD:
            errors.append(f"test failure or XPASS: {test_id} ({status})")
        elif status in SKIP:
            entry = allowed.get(test_id)
            if entry is None or entry["status"] != ("xfail" if status == "xfail" else "skip"):
                errors.append(f"unauthorized {status}: {test_id}")
    if errors:
        raise VerificationError("; ".join(errors))
    return {"framework": framework, "selected": len(records), "status": "PASS"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--allowlist", type=Path, default=Path("docs/release-evidence/manifests/test-outcome-allowlist-v1.json"))
    p.add_argument("--framework", choices=("pytest", "vitest"), required=True)
    args = p.parse_args(argv)
    try:
        print(json.dumps(verify_report(args.report, args.allowlist, args.framework), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
