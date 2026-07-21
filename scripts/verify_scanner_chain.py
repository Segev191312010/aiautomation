"""Fail-closed verifier for pre-T scanner policy/soak chain artifacts.

This verifier is deliberately offline and standard-library-only.  It never
connects to a broker and refuses any live-authority phase.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path("docs/release-evidence/protocols")
CANARY = ROOT / "scanner-canary-v1.json"
SOAK = ROOT / "scanner-soak-v1.json"
SCHEMA = ROOT / "scanner-canary-policy-schema-v1.json"
LIVE_PHASES = frozenset({"live", "submit", "paper-live"})
ALLOWED_PHASES = frozenset({"pre-t", "paper-startup", "paper-soak", "canary-authorization", "live-canary", "release-closeout", *LIVE_PHASES})
REQUIRED_ASSERTIONS = {
    "reserved_intents": 1, "entry_orders": 1, "entry_adapter_calls": 1,
    "unauthorized_orders": 0, "entry_authority": "OFF",
    "working_canary_orders": 0, "unresolved_interventions": 0,
}


class ScannerChainError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ScannerChainError(f"missing or unsafe artifact: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScannerChainError(f"invalid JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise ScannerChainError(f"artifact must be an object: {path}")
    return value


def _dt(value: Any, key: str) -> dt.datetime:
    if not isinstance(value, str):
        raise ScannerChainError(f"{key} must be an ISO timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScannerChainError(f"{key} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ScannerChainError(f"{key} must include timezone")
    return parsed


def validate_canary(policy: dict[str, Any], *, schema: dict[str, Any] | None = None, require_simulation: bool = True) -> str:
    required = {"schema_version", "policy_id", "owner", "risk_operator", "limits", "runtime", "signed_at", "expires_at"}
    missing = required - policy.keys()
    if missing:
        raise ScannerChainError(f"canary policy missing fields: {sorted(missing)}")
    if policy["schema_version"] != 1 or not isinstance(policy["policy_id"], str) or not policy["policy_id"].startswith("scanner-canary-v1-"):
        raise ScannerChainError("unsupported canary policy schema/id")
    for key in ("owner", "risk_operator"):
        if not isinstance(policy[key], str) or not policy[key].strip():
            raise ScannerChainError(f"{key} must be a non-empty identity")
    limits = policy["limits"]
    runtime = policy["runtime"]
    if not isinstance(limits, dict) or not isinstance(runtime, dict):
        raise ScannerChainError("limits and runtime must be objects")
    if limits.get("max_entry_orders") != 1 or limits.get("max_entry_adapter_calls") != 1:
        raise ScannerChainError("entry limits must be exactly one")
    if limits.get("no_short") is not True or runtime.get("workers") != 1:
        raise ScannerChainError("non-overridable safety constants violated")
    if not isinstance(runtime.get("sim_mode"), bool):
        raise ScannerChainError("runtime sim_mode must be an explicit boolean")
    if require_simulation and runtime["sim_mode"] is not True:
        raise ScannerChainError("pre-T scanner policy must remain simulation-only")
    for key in ("claude_worker", "claude_live", "tv_write_route", "mcp_order_tool"):
        if runtime.get(key) is not False:
            raise ScannerChainError(f"runtime safety flag {key} must be false")
    signed = _dt(policy["signed_at"], "signed_at")
    expires = _dt(policy["expires_at"], "expires_at")
    if expires <= signed:
        raise ScannerChainError("policy must expire after signing")
    if schema is not None and schema.get("$id") != "tradebot://scanner-canary-policy-schema-v1":
        raise ScannerChainError("unexpected schema identity")
    return sha256_json(policy)


def validate_soak(soak: dict[str, Any]) -> str:
    required = {"schema_version", "protocol_id", "eligible_sessions", "minimum_eligible_decisions", "max_failures", "max_duplicates", "max_expiries", "max_orphans", "max_reconciliation_mismatches", "signed"}
    missing = required - soak.keys()
    if missing:
        raise ScannerChainError(f"soak protocol missing fields: {sorted(missing)}")
    if soak.get("schema_version") != 1 or soak.get("protocol_id") != "scanner-soak-v1":
        raise ScannerChainError("unsupported soak protocol")
    if soak.get("eligible_sessions") != 15 or soak.get("minimum_eligible_decisions") != 100:
        raise ScannerChainError("soak baseline must be explicit (15 sessions/100 decisions)")
    for key in ("max_failures", "max_duplicates", "max_expiries", "max_orphans", "max_reconciliation_mismatches"):
        if soak.get(key) != 0:
            raise ScannerChainError(f"soak {key} must be zero")
    if soak.get("signed") is not True:
        raise ScannerChainError("soak protocol is not signed")
    return sha256_json(soak)


def validate_evidence(evidence: dict[str, Any]) -> None:
    for key, expected in REQUIRED_ASSERTIONS.items():
        if evidence.get(key) != expected:
            raise ScannerChainError(f"safety assertion {key} != {expected!r}")
    if evidence.get("final_exposure") not in {"FLAT", "SIGNED_TRANSFER"}:
        raise ScannerChainError("final exposure is not closed")
    if evidence.get("broker_entry_order_terminal") is not True or evidence.get("execution_ids_unique") is not True:
        raise ScannerChainError("broker terminal/unique execution assertions failed")


def verify_chain(repo_root: Path, *, phase: str = "pre-t", evidence: dict[str, Any] | None = None) -> dict[str, str]:
    if not isinstance(phase, str) or phase not in ALLOWED_PHASES:
        raise ScannerChainError(f"unsupported scanner-chain phase: {phase!r}")
    if phase in LIVE_PHASES:
        raise ScannerChainError("live authority is never permitted by this pre-T verifier")
    root = repo_root / ROOT
    schema = _load(repo_root / SCHEMA)
    soak = _load(repo_root / SOAK)
    policy = _load(repo_root / CANARY)
    if evidence is not None:
        validate_evidence(evidence)
    return {"schema_hash": sha256_json(schema), "soak_hash": validate_soak(soak), "policy_hash": validate_canary(policy, schema=schema, require_simulation=phase == "pre-t")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--phase", default="pre-t")
    args = parser.parse_args()
    try:
        print(json.dumps(verify_chain(args.repo_root.resolve(), phase=args.phase), sort_keys=True))
    except ScannerChainError as exc:
        print(f"FAIL CLOSED: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
