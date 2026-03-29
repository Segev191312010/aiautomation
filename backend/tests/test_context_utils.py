"""Tests for context_utils — deterministic hashing and volatile-field stripping."""
import json
from context_utils import (
    stable_json_dumps,
    strip_volatile_fields,
    normalize_context_json,
    compute_context_hash,
    VOLATILE_FIELDS,
)


def test_key_order_invariance():
    """Same data with different key order produces same hash."""
    a = json.dumps({"b": 2, "a": 1})
    b = json.dumps({"a": 1, "b": 2})
    assert compute_context_hash(a) == compute_context_hash(b)


def test_volatile_fields_stripped():
    ctx = {
        "regime": "bull",
        "trade_count": 10,
        "timestamp": "2026-03-24T10:00:00",
        "last_run": "2026-03-24T09:00:00",
        "cached_at": "2026-03-24T08:00:00",
        "elapsed_ms": 1234,
    }
    stripped = strip_volatile_fields(ctx)
    assert "regime" in stripped
    assert "trade_count" in stripped
    for field in VOLATILE_FIELDS:
        assert field not in stripped


def test_volatile_fields_nested():
    ctx = {
        "data": {
            "value": 42,
            "timestamp": "should-be-stripped",
        },
        "regime": "bear",
    }
    stripped = strip_volatile_fields(ctx)
    assert stripped["data"]["value"] == 42
    assert "timestamp" not in stripped["data"]


def test_normalized_json_stability():
    raw1 = '{"b": 2, "a": 1, "timestamp": "2026-01-01"}'
    raw2 = '{"a": 1, "b": 2, "timestamp": "2099-12-31"}'
    # Different key order + different timestamp → same normalized output
    assert normalize_context_json(raw1) == normalize_context_json(raw2)


def test_hash_ignores_volatile_timestamps():
    ctx1 = json.dumps({"regime": "bull", "trade_count": 5, "last_run": "2026-01-01"})
    ctx2 = json.dumps({"regime": "bull", "trade_count": 5, "last_run": "2026-12-31"})
    assert compute_context_hash(ctx1) == compute_context_hash(ctx2)


def test_hash_differs_on_real_data_change():
    ctx1 = json.dumps({"regime": "bull", "trade_count": 5})
    ctx2 = json.dumps({"regime": "bear", "trade_count": 5})
    assert compute_context_hash(ctx1) != compute_context_hash(ctx2)


def test_stable_json_dumps_handles_nan():
    result = stable_json_dumps({"value": float("nan"), "ok": True})
    parsed = json.loads(result)
    assert parsed["value"] is None
    assert parsed["ok"] is True


def test_stable_json_dumps_handles_inf():
    result = stable_json_dumps({"value": float("inf")})
    parsed = json.loads(result)
    assert parsed["value"] is None


def test_invalid_json_passthrough():
    """Non-JSON input gets hashed as-is without crashing."""
    result = compute_context_hash("not valid json {{{")
    assert isinstance(result, str)
    assert len(result) == 16


def test_hash_length():
    assert len(compute_context_hash('{"a": 1}')) == 16
