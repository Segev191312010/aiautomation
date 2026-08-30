"""Deterministic identities for idempotent scheduler decisions."""
from __future__ import annotations

from hashlib import sha256
import json
import re


_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


def decision_key(
    *,
    strategy_id: str,
    strategy_version: int,
    snapshot_fingerprint: str,
) -> str:
    """Return the stable key for one strategy version and input snapshot.

    The run identifier is intentionally absent: a restarted scheduler must
    derive the same key for the same immutable strategy/input pair.
    """
    normalized_strategy_id = strategy_id.strip()
    if not normalized_strategy_id:
        raise ValueError("strategy_id must be non-empty")
    if strategy_version < 1:
        raise ValueError("strategy_version must be positive")
    if not _FINGERPRINT.fullmatch(snapshot_fingerprint):
        raise ValueError("snapshot_fingerprint must be a lowercase SHA-256 digest")

    payload = {
        "strategy_id": normalized_strategy_id,
        "strategy_version": strategy_version,
        "snapshot_fingerprint": snapshot_fingerprint,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()
