#!/usr/bin/env python3
"""Strict, offline validator for the pre-T canary fence design record."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/release-evidence/manifests/canary-fence-design-v1.json"
STATES = {"UNUSED", "CONSUMED_BY", "REVOKED_BY"}


class DesignError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DesignError(message)


def validate_design(doc: dict[str, Any]) -> None:
    _require(doc.get("schema") == "canary-fence-design-v1", "wrong schema")
    _require(doc.get("status") == "DESIGN_ONLY_NO_RUNTIME_AUTHORITY", "design must grant no runtime authority")
    store = doc.get("store", {})
    _require(store.get("engine") == "PostgreSQL", "store must select PostgreSQL")
    for key in ("separate_from_trading_db", "independent_failure_domain", "independent_storage_and_backup", "credentials_and_operator_role_separate"):
        _require(store.get(key) is True, f"store.{key} must be true")
    _require(store.get("durability") == "COMMIT_WAIT_FOR_FLUSH", "durability must wait for WAL flush")
    _require(store.get("availability_policy") == "unavailable_is_fail_closed", "outage must fail closed")
    tables = doc.get("tables", {})
    auth = tables.get("canary_authorizations", {})
    _require(auth.get("primary_key") == ["release_id", "a_nonce"], "authorization key must be release_id,a_nonce")
    cols = auth.get("columns", {})
    _require("state" in cols and all(s in cols["state"] for s in STATES), "authorization state check must enumerate all states")
    _require("terminal_state_constraint" in auth and "consumed_tuple_constraint" in auth and "revoked_tuple_constraint" in auth, "terminal tuple constraints missing")
    fence = tables.get("restore_fence", {})
    _require(fence.get("primary_key") == ["singleton"], "restore fence must have singleton key")
    _require("generation_rule" in fence and "increase" in fence["generation_rule"], "generation must be monotonic")
    ops = doc.get("operations", {})
    for name in ("consume", "revoke"):
        op = ops.get(name, {})
        _require(op.get("isolation") == "SERIALIZABLE", f"{name} must be SERIALIZABLE")
        _require("FOR UPDATE" in op.get("lock", ""), f"{name} must lock authorization row")
        _require("UNUSED" in op.get("transition", "") and ("CONSUMED_BY" in op.get("transition", "") or "REVOKED_BY" in op.get("transition", "")), f"{name} transition missing")
    _require(ops.get("consume", {}).get("commit", "").startswith("single transaction"), "consume must commit atomically")
    _require("consume and revoke serialize" in ops.get("revoke", {}).get("race_rule", ""), "consume/revoke race rule missing")
    restore = doc.get("restore_protocol", {})
    _require(restore.get("interface") == "scripts/restore_trading_db.py", "restore interface must be canonical")
    _require(restore.get("raw_db_restore_authority") is False, "raw DB restore authority must be false")
    order = restore.get("required_order", [])
    _require(isinstance(order, list) and "bump_restore_generation" in order and "replace_or_open_trading_db" in order, "restore order is incomplete")
    _require(order.index("bump_restore_generation") < order.index("replace_or_open_trading_db"), "generation bump must precede DB replacement")
    _require("RECOVERY_ONLY" in restore.get("generation_mismatch", ""), "generation mismatch must force recovery-only")
    failures = doc.get("failure_rules", {})
    for key in ("terminal_states_never_revert", "external_state_is_authoritative_over_db_mirror", "live_trading_authority_granted"):
        _require(failures.get(key) is (False if key == "live_trading_authority_granted" else True), f"failure rule {key} invalid")
    _require(doc.get("review", {}).get("required_before") == "T", "design review is required before T")


def main(path: Path = DEFAULT) -> int:
    try:
        validate_design(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, DesignError) as exc:
        print(f"canary fence design: FAIL: {exc}")
        return 1
    print(f"canary fence design: PASS: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(__import__("sys").argv[1]) if len(__import__("sys").argv) > 1 else DEFAULT))
