"""Fail-closed verifier for scanner schemas and phase-ordered artifacts.

This verifier is deliberately offline and standard-library-only.  It never
connects to a broker and refuses any live-authority phase.  The ``pre-t`` mode
is intentionally narrower than operational modes: it validates only frozen
schemas and the non-authorizing hard-limit template.  Reserved post-T instance
paths must not exist in that mode.
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
HARD_LIMITS = Path("docs/release-evidence/manifests/canary-hard-limits-v1.json")
CHAIN_SCHEMA = Path("docs/release-evidence/schemas/scanner-chain-v1.schema.json")
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


def validate_schema_contract(schema: dict[str, Any]) -> None:
    """Validate the pre-T canary schema's immutable safety contract.

    This is intentionally a structural check, not a JSON-Schema evaluator and
    not an authorization check.  It makes the frozen controller envelope
    reviewable offline and fails closed if a later edit weakens a required
    constant or permits additional policy fields.
    """
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise ScannerChainError("canary schema must use JSON Schema 2020-12")
    if schema.get("$id") != "tradebot://scanner-canary-policy-schema-v1":
        raise ScannerChainError("unexpected schema identity")
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ScannerChainError("canary schema must be a closed object")
    required = schema.get("required")
    expected_required = {
        "schema_version", "policy_id", "owner", "risk_operator", "limits",
        "runtime", "signed_at", "expires_at",
    }
    if not isinstance(required, list) or set(required) != expected_required:
        raise ScannerChainError("canary schema required fields drifted")
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ScannerChainError("canary schema properties must be an object")
    if properties.get("schema_version", {}).get("const") != 1:
        raise ScannerChainError("schema version is not immutable")
    policy_id = properties.get("policy_id", {})
    if policy_id.get("pattern") != r"^scanner-canary-v1-[a-z0-9-]+$":
        raise ScannerChainError("policy id pattern drifted")
    limits = properties.get("limits")
    runtime = properties.get("runtime")
    if not isinstance(limits, dict) or limits.get("additionalProperties") is not False:
        raise ScannerChainError("limits must be a closed object")
    if not isinstance(runtime, dict) or runtime.get("additionalProperties") is not False:
        raise ScannerChainError("runtime must be a closed object")
    limit_props = limits.get("properties", {})
    runtime_props = runtime.get("properties", {})
    if limit_props.get("max_entry_orders", {}).get("const") != 1:
        raise ScannerChainError("max_entry_orders ceiling must be exactly one")
    if limit_props.get("max_entry_adapter_calls", {}).get("const") != 1:
        raise ScannerChainError("max_entry_adapter_calls ceiling must be exactly one")
    if limit_props.get("no_short", {}).get("const") is not True:
        raise ScannerChainError("no_short must be immutable true")
    if runtime_props.get("workers", {}).get("const") != 1:
        raise ScannerChainError("workers ceiling must be exactly one")
    for key in ("claude_worker", "claude_live", "tv_write_route", "mcp_order_tool"):
        if runtime_props.get(key, {}).get("const") is not False:
            raise ScannerChainError(f"runtime safety constant {key} must be false")


def validate_chain_schema_contract(schema: dict[str, Any]) -> None:
    """Validate the closed, non-authorizing scanner evidence envelope."""
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise ScannerChainError("scanner chain schema must use JSON Schema 2020-12")
    if schema.get("$id") != "tradebot://scanner-chain-v1":
        raise ScannerChainError("unexpected scanner chain schema identity")
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ScannerChainError("scanner chain schema must be a closed object")
    required = schema.get("required")
    expected_required = {"schema_version", "artifact_type", "authority_granted"}
    if not isinstance(required, list) or set(required) != expected_required:
        raise ScannerChainError("scanner chain required fields drifted")
    properties = schema.get("properties")
    expected_properties = {
        "schema_version", "artifact_type", "authority_granted", "candidate_sha",
        "artifact_hash", "signature_record", "rendering",
    }
    if not isinstance(properties, dict) or set(properties) != expected_properties:
        raise ScannerChainError("scanner chain properties drifted")
    if properties["schema_version"].get("const") != 1:
        raise ScannerChainError("scanner chain schema version is not immutable")
    expected_types = {"P", "S", "Q", "B", "A", "L", "F", "HOLD", "STOP", "R-SUCCESS", "R-NOGO"}
    artifact_types = properties["artifact_type"].get("enum")
    if not isinstance(artifact_types, list) or set(artifact_types) != expected_types:
        raise ScannerChainError("scanner chain artifact types drifted")
    if properties["authority_granted"].get("const") is not False:
        raise ScannerChainError("scanner chain template must not grant authority")
    expected_patterns = {
        "candidate_sha": r"^[0-9a-f]{40}$",
        "artifact_hash": r"^[0-9a-f]{64}$",
        "signature_record": r"^[^/]+\.sig\.json$",
        "rendering": r"^[^/]+\.md$",
    }
    for key, pattern in expected_patterns.items():
        if properties[key].get("pattern") != pattern:
            raise ScannerChainError(f"scanner chain {key} pattern drifted")


def validate_hard_limits(limits: dict[str, Any]) -> None:
    """Validate the non-authorizing immutable ceiling manifest."""
    if limits.get("schema_version") != 1 or limits.get("manifest_id") != "canary-hard-limits-v1":
        raise ScannerChainError("unexpected canary hard-limits identity")
    if limits.get("status") != "non-authorizing-template" or limits.get("authority_granted") is not False:
        raise ScannerChainError("hard limits must remain a non-authorizing template")
    immutable = limits.get("immutable")
    if not isinstance(immutable, dict):
        raise ScannerChainError("hard limits immutable section must be an object")
    expected = {
        "max_entry_intents": 1, "max_entry_orders": 1,
        "max_entry_adapter_calls": 1, "no_short": True, "workers": 1,
        "claude_worker": False, "claude_live": False,
        "tv_write_route": False, "mcp_order_tool": False,
    }
    if immutable != expected:
        raise ScannerChainError("immutable canary safety ceilings drifted")
    deferred = limits.get("deferred_to_signed_q")
    if not isinstance(deferred, list) or len(deferred) != len(set(deferred)) or not all(isinstance(x, str) and x for x in deferred):
        raise ScannerChainError("deferred Q fields must be a unique non-empty list")
    review = limits.get("review")
    if not isinstance(review, dict) or review.get("signed") is not False:
        raise ScannerChainError("hard limits review must remain unsigned")


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
    if schema is not None:
        validate_schema_contract(schema)
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


def validate_signed_bindings(
    *,
    schema: dict[str, Any],
    policy: dict[str, Any],
    soak: dict[str, Any],
    schema_hash: str,
    expected_policy_hash: str,
    expected_soak_hash: str,
    signature_verified: bool,
    now: dt.datetime,
) -> None:
    """Validate the bindings that an authorized Q/P consumer must enforce.

    The checked-in protocol files are deliberately unsigned templates, so this
    function is not called by the pre-T template verifier.  A startup/canary
    controller must call it only after verifying detached signatures through the
    approved trust manifest.  Keeping the binding check explicit prevents a
    valid-looking policy from being used with a different schema, soak protocol,
    or stale signature.
    """
    if not signature_verified:
        raise ScannerChainError("policy/soak detached signature is not verified")
    for label, value in (("schema_hash", schema_hash), ("expected_policy_hash", expected_policy_hash), ("expected_soak_hash", expected_soak_hash)):
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ScannerChainError(f"{label} must be a lowercase SHA-256 digest")
    actual_schema_hash = sha256_json(schema)
    if schema_hash != actual_schema_hash:
        raise ScannerChainError("CANARY_POLICY_SCHEMA_HASH does not match loaded schema")
    if expected_policy_hash != sha256_json(policy):
        raise ScannerChainError("CANARY_POLICY_HASH does not match loaded policy")
    if expected_soak_hash != sha256_json(soak):
        raise ScannerChainError("SOAK_PROTOCOL_HASH does not match loaded soak protocol")
    for artifact, label in ((policy, "canary policy"), (soak, "soak protocol")):
        owner = artifact.get("owner")
        risk = artifact.get("risk_operator")
        if not isinstance(owner, str) or not owner.strip() or owner == "UNASSIGNED":
            raise ScannerChainError(f"{label} owner is unassigned")
        if not isinstance(risk, str) or not risk.strip() or risk == "UNASSIGNED":
            raise ScannerChainError(f"{label} risk operator is unassigned")
        signed = _dt(artifact.get("signed_at"), f"{label}.signed_at")
        expires = _dt(artifact.get("expires_at"), f"{label}.expires_at")
        if expires <= signed or now < signed or now >= expires:
            raise ScannerChainError(f"{label} signature is expired or not yet valid")


def validate_evidence(evidence: dict[str, Any]) -> None:
    for key, expected in REQUIRED_ASSERTIONS.items():
        if evidence.get(key) != expected:
            raise ScannerChainError(f"safety assertion {key} != {expected!r}")
    if evidence.get("final_exposure") not in {"FLAT", "SIGNED_TRANSFER"}:
        raise ScannerChainError("final exposure is not closed")
    if evidence.get("broker_entry_order_terminal") is not True or evidence.get("execution_ids_unique") is not True:
        raise ScannerChainError("broker terminal/unique execution assertions failed")


def _reserved_instance_paths(path: Path) -> tuple[Path, Path, Path]:
    """Return canonical JSON plus its derived rendering and signature paths."""
    return (
        path,
        path.with_suffix(".md"),
        path.with_name(f"{path.stem}.sig.json"),
    )


def _reject_post_t_instances(repo_root: Path) -> None:
    for artifact in (SOAK, CANARY):
        for path in _reserved_instance_paths(repo_root / artifact):
            # ``exists`` is false for a dangling symlink, so test both.  A
            # dangling link at a reserved evidence path is still an unsafe
            # premature instance and must fail closed.
            if path.exists() or path.is_symlink():
                raise ScannerChainError(
                    f"post-T scanner instance is forbidden during pre-T: {path.relative_to(repo_root)}"
                )


def verify_chain(repo_root: Path, *, phase: str = "pre-t", evidence: dict[str, Any] | None = None) -> dict[str, str]:
    if not isinstance(phase, str) or phase not in ALLOWED_PHASES:
        raise ScannerChainError(f"unsupported scanner-chain phase: {phase!r}")
    if phase in LIVE_PHASES:
        raise ScannerChainError("live authority is never permitted by this pre-T verifier")
    schema = _load(repo_root / SCHEMA)
    validate_schema_contract(schema)
    chain_schema = _load(repo_root / CHAIN_SCHEMA)
    validate_chain_schema_contract(chain_schema)
    hard_limits = _load(repo_root / HARD_LIMITS)
    validate_hard_limits(hard_limits)
    if phase == "pre-t":
        if evidence is not None:
            raise ScannerChainError("post-T scanner evidence is forbidden during pre-T")
        _reject_post_t_instances(repo_root)
        return {
            "schema_hash": sha256_json(schema),
            "chain_schema_hash": sha256_json(chain_schema),
            "hard_limits_hash": sha256_json(hard_limits),
        }
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
