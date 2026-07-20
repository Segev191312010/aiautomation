#!/usr/bin/env python3
"""Verify that every direct IBKR side-effect/read call is inventoried.

This is intentionally an AST-only, fail-closed check.  It does not import the
application (or connect to a broker), and ignores virtual environments and
tests.  A newly added direct broker call must be reviewed and added to the
versioned inventory with its exact source line.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

METHODS = frozenset({
    "placeOrder", "cancelOrder", "qualifyContractsAsync", "reqTickersAsync",
    "openTrades", "connectAsync", "disconnect",
})


def _chain(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


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
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method = node.func.attr
            chain = _chain(node.func)
            # Restrict to known broker object names.  This prevents unrelated
            # WebSocket/database ``disconnect`` methods from being flagged.
            if method not in METHODS or not any(
                token in chain.lower() for token in ("ibkr", "_ib", "ib.")
            ):
                continue
            rel = path.relative_to(root).as_posix()
            found.append({"path": rel, "line": node.lineno, "method": method, "callee": chain})
    return sorted(found, key=lambda x: (str(x["path"]), int(x["line"]), str(x["callee"])))


def verify(root: Path, manifest_path: Path) -> tuple[list[dict[str, object]], list[str]]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("unsupported broker inventory schema_version")
    entries = data.get("calls")
    if not isinstance(entries, list):
        raise ValueError("manifest calls must be a list")
    normalized = []
    for entry in entries:
        if not isinstance(entry, dict) or not all(k in entry for k in ("path", "line", "method", "callee")):
            raise ValueError("each call requires path, line, method, and callee")
        normalized.append({"path": str(entry["path"]), "line": int(entry["line"]),
                           "method": str(entry["method"]), "callee": str(entry["callee"])})
    actual = discover(root)
    expected = sorted(normalized, key=lambda x: (x["path"], x["line"], x["callee"]))
    errors: list[str] = []
    if len(expected) != len(set(tuple(sorted(x.items())) for x in expected)):
        errors.append("manifest contains duplicate call entries")
    missing = [x for x in actual if x not in expected]
    stale = [x for x in expected if x not in actual]
    errors.extend("unlisted broker call: " + json.dumps(x, sort_keys=True) for x in missing)
    errors.extend("stale inventory entry: " + json.dumps(x, sort_keys=True) for x in stale)
    return actual, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    manifest = (args.manifest or root / "docs/release-evidence/manifests/broker-call-inventory-v1.json").resolve()
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
    print(f"PASS: {len(actual)} direct broker calls inventoried")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
