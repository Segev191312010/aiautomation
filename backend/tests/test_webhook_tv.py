"""Tests for the TradingView webhook ingest router (lane: TV webhook ingest).

Uses a minimal FastAPI app that includes the webhook router(s) and runs
``init_db`` against a unique DB. Run with:

    cd backend && DB_PATH=/tmp/w_tv.db .venv/bin/pytest tests/test_webhook_tv.py -q
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import cfg

# Point at a unique DB for this lane. conftest forces DB_PATH at import time, so
# we override cfg (what db.core reads) explicitly here before init_db runs.
_DB = os.environ.get("DB_PATH", "/tmp/w_tv.db")
cfg.DB_PATH = _DB

from db.core import init_db  # noqa: E402  (after cfg.DB_PATH override)
from auth import create_token  # noqa: E402
from routers.webhook_routes import (  # noqa: E402
    router as webhook_router,
    signals_router,
    TVAlertPayload,
    _verify_tv_secret,
)

_TEST_SECRET = "supersecret-tv-key"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture(scope="module", autouse=True)
async def _init():
    cfg.DB_PATH = _DB
    cfg.TV_WEBHOOK_SECRET = _TEST_SECRET
    cfg.TV_IP_STRICT = False
    cfg.TV_ALLOWED_IPS = ""
    cfg.TV_FRESHNESS_SECONDS = 90
    await init_db()
    yield


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(webhook_router)
    app.include_router(signals_router)
    return app


def _client(host: str = "127.0.0.1") -> TestClient:
    return TestClient(_make_app(), client=(host, 5000))


def _body(**overrides) -> dict:
    body = {
        "symbol": "AAPL",
        "action": "buy",
        "price": 187.5,
        "timenow": _now_iso(),
        "bar_time": _now_iso(),
        "interval": "1D",
        "strategy_order_id": "order-1",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Secret authentication
# ---------------------------------------------------------------------------

def test_missing_secret_401():
    r = _client().post("/api/webhook/tv", json=_body())  # no secret anywhere
    assert r.status_code == 401


def test_wrong_secret_401():
    r = _client().post("/api/webhook/tv", json=_body(secret="totally-wrong-secret"))
    assert r.status_code == 401


def test_secret_via_header_works():
    r = _client().post(
        "/api/webhook/tv",
        json=_body(strategy_order_id="hdr-1"),
        headers={"X-TV-Secret": _TEST_SECRET},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"


def test_secret_via_body_works():
    r = _client().post(
        "/api/webhook/tv",
        json=_body(secret=_TEST_SECRET, strategy_order_id="body-1"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"


# ---------------------------------------------------------------------------
# Secret never leaks
# ---------------------------------------------------------------------------

def test_secret_redacted_in_422():
    # Valid secret so we pass auth, but an invalid symbol triggers a 422 whose
    # body must not contain the raw secret.
    r = _client().post(
        "/api/webhook/tv",
        json=_body(symbol="not valid!", secret=_TEST_SECRET),
    )
    assert r.status_code == 422
    assert _TEST_SECRET not in r.text


def test_secret_redacted_in_model_dump():
    p = TVAlertPayload.model_validate(_body(secret=_TEST_SECRET))
    dumped = p.model_dump()
    assert dumped["secret"] == "[REDACTED]"
    assert _TEST_SECRET not in str(dumped)
    # JSON dump path too
    assert _TEST_SECRET not in p.model_dump_json()


def test_webhook_logs_never_include_raw_body_or_secret(caplog):
    """Access logs may identify the outcome, but never capture webhook data."""
    import json as _json

    marker = "log-privacy-" + _now_iso()
    body = _body(strategy_order_id=marker)
    raw_body = _json.dumps(body, separators=(",", ":"))
    with caplog.at_level("INFO"):
        response = _client().post(
            "/api/webhook/tv",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-TV-Secret": _TEST_SECRET,
            },
        )

    assert response.status_code == 200, response.text
    log_output = caplog.text
    assert raw_body not in log_output
    assert _TEST_SECRET not in log_output


# ---------------------------------------------------------------------------
# IP allowlist
# ---------------------------------------------------------------------------

def test_blocked_ip_403():
    # Strict mode on + IP not in allowlist => 403, before secret/schema checks.
    cfg.TV_IP_STRICT = True
    cfg.TV_ALLOWED_IPS = "52.89.214.238"
    try:
        c = TestClient(_make_app(), client=("203.0.113.9", 5000))
        r = c.post("/api/webhook/tv", json=_body(secret=_TEST_SECRET))
        assert r.status_code == 403
    finally:
        cfg.TV_IP_STRICT = False
        cfg.TV_ALLOWED_IPS = ""


def test_allowed_ip_exact_match_passes():
    cfg.TV_IP_STRICT = True
    cfg.TV_ALLOWED_IPS = "52.89.214.238, 34.212.75.30"
    try:
        c = TestClient(_make_app(), client=("34.212.75.30", 5000))
        r = c.post(
            "/api/webhook/tv",
            json=_body(secret=_TEST_SECRET, strategy_order_id="ip-ok-1"),
        )
        assert r.status_code == 200, r.text
    finally:
        cfg.TV_IP_STRICT = False
        cfg.TV_ALLOWED_IPS = ""


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def test_invalid_symbol_422():
    r = _client().post("/api/webhook/tv", json=_body(symbol="ab", secret=_TEST_SECRET))
    # lowercase fails the ^[A-Z0-9.\-]+$ pattern
    assert r.status_code == 422


def test_action_close_rejected_422():
    r = _client().post("/api/webhook/tv", json=_body(action="close", secret=_TEST_SECRET))
    assert r.status_code == 422


def test_nan_price_422():
    # A non-finite price must be rejected by the field_validator. The standard
    # JSON encoder refuses to serialize NaN, so send a raw body with a bare NaN
    # literal (which Python's json.loads accepts) to exercise the server path.
    import json as _json

    body = _body(secret=_TEST_SECRET)
    raw = _json.dumps({k: v for k, v in body.items() if k != "price"})
    raw = raw[:-1] + ', "price": NaN}'  # inject bare NaN literal
    r = _client().post(
        "/api/webhook/tv",
        content=raw,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422
    assert _TEST_SECRET not in r.text


def test_stale_timenow_422_uses_timenow_not_bar_time():
    # bar_time fresh, timenow stale => must reject (proves freshness keys on timenow).
    stale = "2000-01-01T00:00:00Z"
    r = _client().post(
        "/api/webhook/tv",
        json=_body(secret=_TEST_SECRET, timenow=stale, bar_time=_now_iso()),
    )
    assert r.status_code == 422
    assert "timenow" in r.text.lower()


# ---------------------------------------------------------------------------
# Happy path + idempotency
# ---------------------------------------------------------------------------

def test_happy_path_creates_pending_review_row():
    oid = "happy-" + _now_iso()
    r = _client().post(
        "/api/webhook/tv",
        json=_body(secret=_TEST_SECRET, strategy_order_id=oid),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "queued"
    signal_id = data["signal_id"]

    # Verify the row via the authenticated /api/signals endpoint.
    token = create_token("demo")
    c = _client()
    lr = c.get(
        "/api/signals",
        params={"source": "tv_webhook"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert lr.status_code == 200, lr.text
    rows = lr.json()["signals"]
    match = [s for s in rows if s["id"] == signal_id]
    assert match, "queued signal not found in /api/signals"
    row = match[0]
    assert row["status"] == "pending_review"
    assert row["source"] == "tv_webhook"
    assert row["symbol"] == "AAPL"
    assert row["payload"]["action"] == "buy"
    assert row["payload"]["received_ip"] == "127.0.0.1"


def test_duplicate_event_key_returns_duplicate_same_id():
    oid = "dup-" + _now_iso()
    body = _body(secret=_TEST_SECRET, strategy_order_id=oid, timenow=_now_iso())
    r1 = _client().post("/api/webhook/tv", json=body)
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "queued"
    first_id = r1.json()["signal_id"]

    # Identical payload (same event_key) => duplicate, same signal_id.
    r2 = _client().post("/api/webhook/tv", json=body)
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "duplicate"
    assert r2.json()["signal_id"] == first_id


def test_signals_are_scoped_to_authenticated_user_unless_explicit_admin():
    """A valid token must not turn /api/signals into a cross-tenant feed."""
    alice_id = "alice-signal-" + uuid.uuid4().hex
    bob_id = "bob-signal-" + uuid.uuid4().hex
    alice_signal = "sig-" + uuid.uuid4().hex
    bob_signal = "sig-" + uuid.uuid4().hex
    db = sqlite3.connect(_DB)
    try:
        db.execute(
            "INSERT INTO users (id, email, password_hash, created_at, settings) VALUES (?, ?, ?, ?, ?)",
            (alice_id, f"{alice_id}@local", "unused", _now_iso(), "{}"),
        )
        db.execute(
            "INSERT INTO users (id, email, password_hash, created_at, settings) VALUES (?, ?, ?, ?, ?)",
            (bob_id, f"{bob_id}@local", "unused", _now_iso(), "{}"),
        )
        for signal_id, owner in ((alice_signal, alice_id), (bob_signal, bob_id)):
            db.execute(
                "INSERT INTO direct_candidates (id, user_id, symbol, payload, queued_at, ttl_seconds, status, source) "
                "VALUES (?, ?, 'AAPL', '{}', ?, 300, 'pending_review', 'test')",
                (signal_id, owner, _now_iso()),
            )
        db.commit()
    finally:
        db.close()

    c = _client()
    alice_rows = c.get("/api/signals", headers={"Authorization": f"Bearer {create_token(alice_id)}"})
    assert alice_rows.status_code == 200, alice_rows.text
    assert {row["id"] for row in alice_rows.json()["signals"]} == {alice_signal}

    bob_rows = c.get("/api/signals", headers={"Authorization": f"Bearer {create_token(bob_id)}"})
    assert bob_rows.status_code == 200, bob_rows.text
    assert {row["id"] for row in bob_rows.json()["signals"]} == {bob_signal}

    admin_id = "admin-signal-" + uuid.uuid4().hex
    db = sqlite3.connect(_DB)
    try:
        db.execute(
            "INSERT INTO users (id, email, password_hash, created_at, settings) VALUES (?, ?, ?, ?, ?)",
            (admin_id, f"{admin_id}@local", "unused", _now_iso(), '{"is_admin": true}'),
        )
        db.commit()
    finally:
        db.close()
    admin_rows = c.get("/api/signals", headers={"Authorization": f"Bearer {create_token(admin_id)}"})
    assert admin_rows.status_code == 200, admin_rows.text
    ids = {row["id"] for row in admin_rows.json()["signals"]}
    assert {alice_signal, bob_signal} <= ids


def test_different_strategy_order_id_creates_new_signal():
    ts = _now_iso()
    b1 = _body(secret=_TEST_SECRET, strategy_order_id="diff-A", timenow=ts)
    b2 = _body(secret=_TEST_SECRET, strategy_order_id="diff-B", timenow=ts)
    r1 = _client().post("/api/webhook/tv", json=b1)
    r2 = _client().post("/api/webhook/tv", json=b2)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["status"] == "queued"
    assert r2.json()["status"] == "queued"
    assert r1.json()["signal_id"] != r2.json()["signal_id"]


# ---------------------------------------------------------------------------
# Helper unit checks
# ---------------------------------------------------------------------------

def test_verify_tv_secret_empty_expected_fails():
    saved = cfg.TV_WEBHOOK_SECRET
    cfg.TV_WEBHOOK_SECRET = ""
    try:
        assert _verify_tv_secret("anything", None) is False
        assert _verify_tv_secret(None, "anything") is False
    finally:
        cfg.TV_WEBHOOK_SECRET = saved
