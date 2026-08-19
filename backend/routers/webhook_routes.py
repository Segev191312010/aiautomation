"""TradingView webhook ingest — /api/webhook/*

PUBLIC router (no auth dependency). TradingView cannot send an Authorization
bearer header, so authenticity is established by a shared HMAC secret (sent in
the JSON body or the ``X-TV-Secret`` header) plus optional source-IP allowlisting
and a freshness window. Modeled on the public ``/api/auth/token`` route pattern.

Inbound TV alerts are de-duplicated by a stable ``event_key`` so a redelivered
webhook (TradingView retries on non-2xx) never re-queues a second candidate.
Accepted alerts are persisted to ``direct_candidates`` with status
``pending_review`` and source ``tv_webhook`` for human/AI review downstream.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    Field,
    SecretStr,
    ValidationError,
    field_serializer,
    field_validator,
)

from auth import get_current_user
from config import cfg
from db.core import get_db, transaction

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhook", tags=["webhook"])

# Separate authenticated router for operator-facing signal listing. Both are
# exported; the orchestrator includes them via the package router.
signals_router = APIRouter(tags=["signals"], dependencies=[Depends(get_current_user)])


def _is_signal_admin(user) -> bool:
    """Return whether *user* may inspect candidates owned by other users.

    User roles are intentionally represented in the existing settings JSON so
    this change does not require a schema migration.  Missing or malformed
    settings always default to a tenant-scoped user.  The demo account is not
    implicitly privileged; deployments that need operator-wide visibility must
    explicitly set ``{"is_admin": true}`` (or ``{"role": "admin"}``).
    """
    settings = getattr(user, "settings", None)
    if not isinstance(settings, dict):
        return False
    return settings.get("is_admin") is True or settings.get("role") == "admin"


# ---------------------------------------------------------------------------
# Payload model
# ---------------------------------------------------------------------------

class TVAlertPayload(BaseModel):
    """A single inbound TradingView alert.

    ``action`` is restricted to entry sides — ``"close"`` is rejected at the
    schema level (TV close/flat alerts are not routed through this ingest).
    ``secret`` is a SecretStr and is always redacted on serialization so it can
    never leak into logs or error responses.
    """

    symbol: str = Field(pattern=r"^[A-Z0-9.\-]+$", min_length=1, max_length=16)
    action: Literal["buy", "sell"]
    price: float | None = None
    timenow: str | None = None
    bar_time: str | None = None
    interval: str = "1D"
    strategy_order_id: str | None = None
    secret: SecretStr | None = Field(default=None, min_length=8)

    @field_validator("price")
    @classmethod
    def _reject_non_finite_price(cls, v: float | None) -> float | None:
        if v is not None and (v != v or v in (float("inf"), float("-inf"))):
            raise ValueError("price must be a finite number")
        return v

    @field_serializer("secret", when_used="always")
    def _redact_secret(self, v: SecretStr | None) -> str | None:
        return "[REDACTED]" if v is not None else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_tv_secret(body_secret: str | None, header: str | None) -> bool:
    """Constant-time compare of the provided secret against the configured one.

    The secret may arrive in the JSON body or the ``X-TV-Secret`` header; the
    header takes precedence. An empty configured secret always fails closed.
    """
    expected = cfg.TV_WEBHOOK_SECRET or ""
    if not expected:
        return False
    provided = header if header else (body_secret or "")
    return hmac.compare_digest(
        hashlib.sha256(provided.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


def _verify_tv_ip(host: str) -> bool:
    """Return True if *host* is an allowed source IP.

    When IP strictness is off, loopback origins are always permitted (dev). In
    all cases an exact match against ``TV_ALLOWED_IPS`` (comma-separated) passes.
    """
    if not cfg.TV_IP_STRICT and host in {"127.0.0.1", "::1", "localhost"}:
        return True
    allowed = [ip.strip() for ip in (cfg.TV_ALLOWED_IPS or "").split(",") if ip.strip()]
    return host in allowed


def _check_freshness(timenow: str | None) -> bool:
    """Return True if *timenow* is within the configured freshness window.

    None/empty/unparseable timestamps are stale (False). ``Z`` suffixes are
    normalized to ``+00:00`` for ISO parsing. Naive timestamps are assumed UTC.
    """
    if not timenow:
        return False
    try:
        ts = datetime.fromisoformat(timenow.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return abs((now - ts).total_seconds()) <= cfg.TV_FRESHNESS_SECONDS


def _event_key(payload: TVAlertPayload) -> str:
    """Stable dedupe key for an inbound alert."""
    parts = "|".join(
        [
            payload.symbol,
            payload.action,
            payload.interval,
            payload.timenow or "",
            payload.strategy_order_id or "",
        ]
    )
    return hashlib.sha256(parts.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/tv")
async def tv_webhook(request: Request):
    """Ingest a TradingView alert.

    Order of checks: source IP (403) -> secret (401) -> freshness (422) ->
    schema validation (422) -> atomic queue + idempotency insert.
    """
    # 1. Source IP allowlist
    client_host = request.client.host if request.client else ""
    if not _verify_tv_ip(client_host):
        log.warning("SECURITY: /api/webhook/tv refused — source IP %s not allowed", client_host)
        return JSONResponse(status_code=403, content={"detail": "Source IP not allowed"})

    # Parse + validate body. A validation error must NEVER echo the secret, so
    # we catch ValidationError here and emit a sanitized 422 (route-scoped).
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse(status_code=422, content={"detail": "Invalid JSON body"})
    if not isinstance(raw, dict):
        return JSONResponse(status_code=422, content={"detail": "Body must be a JSON object"})

    header_secret = request.headers.get("X-TV-Secret")
    body_secret_raw = raw.get("secret")

    # 2. Secret — verified BEFORE schema parsing so an invalid (but secret-bearing)
    #    payload still authenticates first and never leaks the secret in a 422.
    if not _verify_tv_secret(
        body_secret_raw if isinstance(body_secret_raw, str) else None,
        header_secret,
    ):
        log.warning("SECURITY: /api/webhook/tv refused — invalid/missing secret")
        return JSONResponse(status_code=401, content={"detail": "Invalid webhook secret"})

    try:
        payload = TVAlertPayload.model_validate(raw)
    except ValidationError as exc:
        # Sanitize: strip any input echo so the secret never appears in the body.
        errors = [
            {"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        for e in errors:
            e.pop("input", None)
            e.pop("ctx", None)
        return JSONResponse(status_code=422, content={"detail": errors})

    # 3. Freshness — uses timenow (the alert fire time), NOT bar_time.
    if not _check_freshness(payload.timenow):
        log.warning("/api/webhook/tv rejected — stale or missing timenow")
        return JSONResponse(status_code=422, content={"detail": "Stale or missing timenow"})

    event_key = _event_key(payload)
    signal_id = str(uuid4())
    queued_at = datetime.now(timezone.utc).isoformat()

    candidate_payload = {
        "action": payload.action,
        "price": payload.price,
        "timenow": payload.timenow,
        "bar_time": payload.bar_time,
        "interval": payload.interval,
        "strategy_order_id": payload.strategy_order_id,
        "received_ip": client_host,
    }
    payload_json = json.dumps(candidate_payload, default=str)

    try:
        # Parent (direct_candidates) BEFORE child (tv_idempotency) — FK is ON.
        async with transaction() as db:
            await db.execute(
                "INSERT INTO direct_candidates "
                "(id, user_id, symbol, payload, queued_at, ttl_seconds, status, source) "
                "VALUES (?, 'demo', ?, ?, ?, 300, 'pending_review', 'tv_webhook')",
                (signal_id, payload.symbol, payload_json, queued_at),
            )
            await db.execute(
                "INSERT INTO tv_idempotency (event_key, signal_id, created_at) "
                "VALUES (?, ?, ?)",
                (event_key, signal_id, queued_at),
            )
    except sqlite3.IntegrityError:
        # Duplicate event_key — TradingView redelivery. Return the original id.
        async with get_db() as db:
            async with db.execute(
                "SELECT signal_id FROM tv_idempotency WHERE event_key=?",
                (event_key,),
            ) as cur:
                row = await cur.fetchone()
        existing = row[0] if row else None
        log.info("/api/webhook/tv duplicate event_key — returning existing signal %s", existing)
        return {"status": "duplicate", "signal_id": existing}

    log.info("/api/webhook/tv queued signal %s for %s %s", signal_id, payload.action, payload.symbol)
    return {"status": "queued", "signal_id": signal_id}


@signals_router.get("/api/signals")
async def list_signals(
    source: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user=Depends(get_current_user),
):
    """List queued candidates (auth-gated), newest first.

    Optional ``source`` / ``status`` filters; ``payload`` is parsed from JSON.
    """
    clauses: list[str] = []
    args: list = []
    if not _is_signal_admin(user):
        clauses.append("user_id=?")
        args.append(user.id)
    if source is not None:
        clauses.append("source=?")
        args.append(source)
    if status is not None:
        clauses.append("status=?")
        args.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    async with get_db() as db:
        async with db.execute(
            "SELECT id, user_id, symbol, payload, queued_at, ttl_seconds, status, source "
            "FROM direct_candidates" + where +
            " ORDER BY queued_at DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ) as cur:
            rows = await cur.fetchall()

    signals = []
    for row in rows:
        cand_id, user_id, symbol, payload_json, q_at, ttl, st, src = row
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except json.JSONDecodeError:
            payload = {}
        signals.append(
            {
                "id": cand_id,
                "user_id": user_id,
                "symbol": symbol,
                "payload": payload,
                "queued_at": q_at,
                "ttl_seconds": ttl,
                "status": st,
                "source": src,
            }
        )
    return {"signals": signals, "limit": limit, "offset": offset}
