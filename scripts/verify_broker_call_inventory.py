#!/usr/bin/env python3
"""Verify the versioned inventory of direct IBKR call sites.

Discovery is receiver based rather than method allowlist based: every call on
the module singleton (``ibkr``), the underlying ``IB`` object, or a local
``ib`` alias is found even when its method is new.  Each discovered call must
then have a reviewed classification and disposition in the manifest.  Unknown
methods fail closed until this verifier's policy is deliberately extended.

The check is static only.  An inventory PASS means that source locations and
their current dispositions are complete; it does not grant broker, C9, paper,
or live authority.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Final


SCHEMA_VERSION: Final = 2
CLASSIFICATIONS: Final = frozenset({"read", "side-effect", "connection", "admin"})

# A method may have more than one disposition when the safety obligation is
# call-site-specific (notably placeOrder).  Every side-effect disposition is
# intentionally blocking until the reviewed C9 adapter exists.
METHOD_POLICY: Final[dict[str, tuple[str, frozenset[str]]]] = {
    "IB": ("admin", frozenset({"local-client-construction"})),
    "IBKRClient": ("admin", frozenset({"local-client-construction"})),
    "set_broadcast": ("admin", frozenset({"local-callback-wiring"})),
    "make_stock_contract": ("admin", frozenset({"local-contract-construction"})),
    "connect": ("connection", frozenset({"connection-lifecycle"})),
    "connectAsync": ("connection", frozenset({"connection-lifecycle"})),
    "disconnect": ("connection", frozenset({"connection-lifecycle"})),
    "start_reconnect_loop": ("connection", frozenset({"connection-lifecycle"})),
    "is_connected": ("connection", frozenset({"connection-state-read"})),
    "isConnected": ("connection", frozenset({"connection-state-read"})),
    "get_account_summary": ("read", frozenset({"account-scoped-state-read"})),
    "accountSummaryAsync": ("read", frozenset({"account-scoped-state-read"})),
    "accountValues": ("read", frozenset({"account-scoped-state-read"})),
    "managedAccounts": ("read", frozenset({"account-routing-read"})),
    "get_positions": ("read", frozenset({"account-scoped-state-read"})),
    "portfolio": ("read", frozenset({"account-scoped-state-read"})),
    "positions": ("read", frozenset({"account-scoped-state-read"})),
    "openTrades": ("read", frozenset({"account-scoped-reconciliation-read"})),
    "qualifyContractsAsync": ("read", frozenset({"contract-qualification-read"})),
    "reqTickersAsync": ("read", frozenset({"market-data-read"})),
    "reqHistoricalDataAsync": ("read", frozenset({"market-data-read"})),
    "reqScannerDataAsync": ("read", frozenset({"market-data-read"})),
    "reqMktData": ("admin", frozenset({"market-data-subscription-lifecycle"})),
    "cancelMktData": ("admin", frozenset({"market-data-subscription-lifecycle"})),
    "reqRealTimeBars": ("admin", frozenset({"market-data-subscription-lifecycle"})),
    "cancelRealTimeBars": ("admin", frozenset({"market-data-subscription-lifecycle"})),
    "placeOrder": (
        "side-effect",
        frozenset({
            "blocked-pending-c9-entry-gate",
            "blocked-pending-c9-replacement-state-machine",
            "blocked-pending-c9-uniform-stop-adapter",
        }),
    ),
    "cancelOrder": (
        "side-effect",
        frozenset({"blocked-pending-c9-account-scoped-cancel-reconcile"}),
    ),
}

IDENTITY_FIELDS: Final = ("path", "line", "column", "method", "callee")
REVIEW_FIELDS: Final = ("classification", "disposition")


def _chain(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _is_direct_broker_callee(chain: str) -> bool:
    """Return whether *chain* is a direct call on the IBKR boundary.

    ``ibkr`` covers both the public singleton and ``ibkr.ib``.  ``self._ib``
    and local ``ib`` cover calls inside the adapter and the legacy typed-IB
    risk helper.  Constructors are included as administrative touchpoints.
    """
    return (
        chain in {"IB", "IBKRClient"}
        or chain.startswith("ibkr.")
        or chain.startswith("self._ib.")
        or chain.startswith("ib.")
    )


def _identity(entry: dict[str, object]) -> tuple[object, ...]:
    return tuple(entry[field] for field in IDENTITY_FIELDS)


def _sort_key(entry: dict[str, object]) -> tuple[object, ...]:
    return (
        str(entry["path"]),
        int(entry["line"]),
        int(entry["column"]),
        str(entry["callee"]),
    )


def discover(root: Path) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    backend = root / "backend"
    if not backend.is_dir():
        raise ValueError(f"missing backend directory: {backend}")
    for path in sorted(backend.rglob("*.py")):
        if any(part in {".venv", "venv", "tests", "__pycache__"} for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            raise ValueError(f"cannot parse {path}: {exc}") from exc
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            chain = _chain(node.func)
            if not _is_direct_broker_callee(chain):
                continue
            method = chain.rsplit(".", 1)[-1]
            rel = path.relative_to(root).as_posix()
            found.append({
                "path": rel,
                "line": node.lineno,
                "column": node.col_offset,
                "method": method,
                "callee": chain,
            })
    return sorted(found, key=_sort_key)


def _load_entries(manifest_path: Path) -> list[dict[str, object]]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"broker inventory schema_version must be {SCHEMA_VERSION}")
    entries = data.get("calls")
    if not isinstance(entries, list):
        raise ValueError("manifest calls must be a list")
    normalized: list[dict[str, object]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"call {index} must be an object")
        required = set(IDENTITY_FIELDS + REVIEW_FIELDS)
        if set(entry) != required:
            missing = sorted(required - set(entry))
            extra = sorted(set(entry) - required)
            raise ValueError(f"call {index} fields mismatch: missing={missing}, extra={extra}")
        path = entry["path"]
        line = entry["line"]
        column = entry["column"]
        method = entry["method"]
        callee = entry["callee"]
        classification = entry["classification"]
        disposition = entry["disposition"]
        if not isinstance(path, str) or not path.startswith("backend/"):
            raise ValueError(f"call {index} path must be a backend-relative string")
        path_parts = Path(path).parts
        if Path(path).is_absolute() or ".." in path_parts or Path(path).as_posix() != path:
            raise ValueError(f"call {index} path is unsafe: {path!r}")
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            raise ValueError(f"call {index} line must be a positive integer")
        if isinstance(column, bool) or not isinstance(column, int) or column < 0:
            raise ValueError(f"call {index} column must be a non-negative integer")
        if not isinstance(method, str) or not method:
            raise ValueError(f"call {index} method must be a non-empty string")
        if not isinstance(callee, str) or not callee:
            raise ValueError(f"call {index} callee must be a non-empty string")
        if classification not in CLASSIFICATIONS:
            raise ValueError(f"call {index} has invalid classification: {classification!r}")
        if not isinstance(disposition, str) or not disposition:
            raise ValueError(f"call {index} disposition must be a non-empty string")
        normalized.append({field: entry[field] for field in IDENTITY_FIELDS + REVIEW_FIELDS})
    return normalized


def verify(root: Path, manifest_path: Path) -> tuple[list[dict[str, object]], list[str]]:
    expected = sorted(_load_entries(manifest_path), key=_sort_key)
    actual = discover(root)
    errors: list[str] = []

    expected_identities = [_identity(entry) for entry in expected]
    if len(expected_identities) != len(set(expected_identities)):
        errors.append("manifest contains duplicate call identities")

    actual_by_identity = {_identity(entry): entry for entry in actual}
    expected_by_identity = {_identity(entry): entry for entry in expected}
    missing = [entry for entry in actual if _identity(entry) not in expected_by_identity]
    stale = [entry for entry in expected if _identity(entry) not in actual_by_identity]
    errors.extend("unlisted broker call: " + json.dumps(entry, sort_keys=True) for entry in missing)
    errors.extend(
        "stale inventory entry: "
        + json.dumps({field: entry[field] for field in IDENTITY_FIELDS}, sort_keys=True)
        for entry in stale
    )

    for entry in actual:
        policy = METHOD_POLICY.get(str(entry["method"]))
        if policy is None:
            errors.append(
                "unclassified broker method (extend METHOD_POLICY): "
                + json.dumps(entry, sort_keys=True)
            )
            continue
        reviewed = expected_by_identity.get(_identity(entry))
        if reviewed is None:
            continue
        required_classification, allowed_dispositions = policy
        if reviewed["classification"] != required_classification:
            errors.append(
                f"classification mismatch for {entry['path']}:{entry['line']}:{entry['column']} "
                f"{entry['callee']}: expected {required_classification}, "
                f"got {reviewed['classification']}"
            )
        if reviewed["disposition"] not in allowed_dispositions:
            errors.append(
                f"disposition mismatch for {entry['path']}:{entry['line']}:{entry['column']} "
                f"{entry['callee']}: allowed {sorted(allowed_dispositions)}, "
                f"got {reviewed['disposition']}"
            )
    return actual, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    manifest = (
        args.manifest
        or root / "docs/release-evidence/manifests/broker-call-inventory-v1.json"
    ).resolve()
    try:
        actual, errors = verify(root, manifest)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    if errors:
        print("FAIL: broker call inventory mismatch", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    counts = {classification: 0 for classification in sorted(CLASSIFICATIONS)}
    entries = _load_entries(manifest)
    for entry in entries:
        counts[str(entry["classification"])] += 1
    summary = ", ".join(f"{name}={count}" for name, count in counts.items())
    print(f"PASS: {len(actual)} direct broker calls inventoried ({summary})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
