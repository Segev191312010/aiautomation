"""Execution brain for ranking rule and direct-AI candidates together.

Direct AI candidates are persisted to SQLite (``direct_candidates`` table) so
they survive a backend restart. Candidates carry a TTL (default 15 min) and
are drained on the next bot cycle; stale rows are marked ``expired``.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from config import cfg
from portfolio_allocator import allocate_candidates

log = logging.getLogger(__name__)


def _priority(candidate: dict) -> tuple[int, float]:
    is_exit = bool(candidate.get("is_exit"))
    score = float(candidate.get("score", 0))
    # Exits always outrank entries regardless of score
    return (1 if is_exit else 0, float("inf") if is_exit else score)


def choose_candidates(rule_candidates: list[dict], direct_candidates: list[dict]) -> list[dict]:
    """
    Merge rule and direct candidates, resolve same-symbol conflicts, then
    allocate them against the risk budget.
    """
    merged: dict[str, dict] = {}
    for candidate in rule_candidates + direct_candidates:
        symbol = str(candidate.get("symbol", "")).upper()
        if not symbol:
            continue
        current = merged.get(symbol)
        if current is None or _priority(candidate) > _priority(current):
            merged[symbol] = dict(candidate)
    return allocate_candidates(list(merged.values()))


def _build_candidate_row(decision: dict) -> dict | None:
    symbol = str(decision.get("symbol", "")).upper()
    if not symbol:
        return None
    now = datetime.now(timezone.utc).isoformat()
    return {
        "symbol": symbol,
        "source": "ai_direct",
        "score": float(decision.get("confidence", 0.5)) * 100.0,
        "risk_pct": float(cfg.RISK_PER_TRADE_PCT),
        "is_exit": str(decision.get("action", "BUY")).upper() == "SELL",
        "decision": dict(decision),
        "queued_at": now,
    }


async def queue_direct_candidates(decisions: list[dict]) -> int:
    """Persist direct AI trade opportunities for execution in the next bot cycle.

    Writes each candidate to the ``direct_candidates`` table with a TTL so it
    survives a backend restart. Returns the number of candidates successfully
    persisted.
    """
    from db.direct_candidates import queue_candidate

    ttl = int(getattr(cfg, "AI_DIRECT_CANDIDATE_TTL_SECONDS", 900))
    queued = 0
    for decision in decisions:
        row = _build_candidate_row(decision)
        if row is None:
            continue
        cand_id = str(decision.get("decision_id") or uuid.uuid4())
        try:
            await queue_candidate(cand_id, row["symbol"], row, ttl_seconds=ttl)
            queued += 1
        except Exception as exc:
            log.error("queue_direct_candidates: persist failed for %s: %s", row["symbol"], exc)
    if queued:
        log.info("Queued %d direct AI candidate(s) to DB", queued)
    return queued


async def drain_direct_candidates(max_age_seconds: int = 900) -> list[dict]:
    """
    Drain queued direct AI opportunities from the DB, dropping stale entries
    and keeping the highest-priority candidate per symbol.
    """
    from db.direct_candidates import drain_candidates

    rows = await drain_candidates(max_age_seconds=max_age_seconds)

    merged: dict[str, dict] = {}
    for candidate in rows:
        symbol = str(candidate.get("symbol", "")).upper()
        if not symbol:
            continue
        current = merged.get(symbol)
        if current is None or _priority(candidate) > _priority(current):
            merged[symbol] = dict(candidate)

    if merged:
        log.info("Drained %d direct candidate(s) for execution: %s", len(merged), list(merged.keys()))
    return list(merged.values())
