"""Context utilities — stable JSON normalization and deterministic hashing.

Ensures logically identical optimizer contexts always produce the same hash,
even if key order differs or volatile fields (timestamps, cache artifacts) change.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

VOLATILE_FIELDS = frozenset({
    "timestamp", "last_run", "cached_at", "elapsed_ms",
    "last_triggered", "updated_at", "last_recompute",
})


class _StableEncoder(json.JSONEncoder):
    """Replace NaN/Inf with null, fallback non-serializable to str."""
    def default(self, obj: Any) -> Any:
        return str(obj)

    def encode(self, obj: Any) -> str:
        return super().encode(_sanitize_floats(obj))


def _sanitize_floats(obj: Any) -> Any:
    """Recursively replace NaN/Inf floats with None."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_floats(v) for v in obj]
    return obj


def stable_json_dumps(obj: Any) -> str:
    """Deterministic JSON: sorted keys, NaN/Inf replaced with null."""
    return json.dumps(obj, sort_keys=True, cls=_StableEncoder)


def strip_volatile_fields(context: dict) -> dict:
    """Recursively remove volatile fields that cause hash drift."""
    if not isinstance(context, dict):
        return context
    return {
        k: strip_volatile_fields(v) if isinstance(v, dict) else v
        for k, v in context.items()
        if k not in VOLATILE_FIELDS
    }


def normalize_context_json(raw: str) -> str:
    """Parse, strip volatile fields, and produce stable JSON."""
    try:
        parsed = json.loads(raw)
    except Exception:
        return raw
    if isinstance(parsed, dict):
        parsed = strip_volatile_fields(parsed)
    return stable_json_dumps(parsed)


def compute_context_hash(context_json: str) -> str:
    """Normalize then SHA256, truncated to 16 hex chars."""
    normalized = normalize_context_json(context_json)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]
