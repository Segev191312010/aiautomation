"""Context builder for the Claude review worker.

Assembles a compact, JSON-serialisable snapshot for a single TradingView
candidate so Claude can decide whether to approve/decline a paper order.

The worker runs in PAPER mode only — it proposes orders against the virtual
SimEngine account, never the live broker. ``build_context`` reuses the
existing simulation + position services where available and degrades to
``None``/empty values (never raises) if a service is unavailable, so a
transient read failure never blocks the review loop.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_payload(candidate: dict) -> dict:
    """Coerce a candidate's ``payload`` (dict or JSON string) into a dict."""
    payload = candidate.get("payload")
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            log.warning("claude_context: corrupt payload for candidate %s", candidate.get("id"))
            return {}
    return {}


async def _load_account() -> dict | None:
    """Virtual paper account summary from the SimEngine singleton."""
    try:
        from simulation import sim_engine

        account = await sim_engine.get_account()
        return account.model_dump()
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("claude_context: account unavailable: %s", exc)
        return None


async def _load_positions() -> list[dict]:
    """Virtual paper positions from the SimEngine singleton."""
    try:
        from simulation import sim_engine

        positions = await sim_engine.get_positions()
        return [p.model_dump() for p in positions]
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("claude_context: positions unavailable: %s", exc)
        return []


async def _load_recent_signals(limit: int = 10) -> list[dict]:
    """Recent TradingView candidates (newest first) for short-term context."""
    try:
        from db.core import get_db

        async with get_db() as db:
            async with db.execute(
                "SELECT id, symbol, status, queued_at "
                "FROM direct_candidates WHERE source='tv_webhook' "
                "ORDER BY queued_at DESC LIMIT ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        return [
            {"id": r[0], "symbol": r[1], "status": r[2], "queued_at": r[3]}
            for r in rows
        ]
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("claude_context: recent signals unavailable: %s", exc)
        return []


async def build_context(candidate: dict) -> dict:
    """Build the review context dict for a single candidate.

    Returns ``{mode, signal, positions, account, recent_signals, now_utc}``.
    Always PAPER mode — the worker never proposes live orders.
    """
    payload = _parse_payload(candidate)
    signal = {
        "candidate_id": candidate.get("id"),
        "symbol": candidate.get("symbol") or payload.get("symbol"),
        "payload": payload,
    }

    return {
        "mode": "paper",
        "signal": signal,
        "positions": await _load_positions(),
        "account": await _load_account(),
        "recent_signals": await _load_recent_signals(),
        "now_utc": _now_utc(),
    }
