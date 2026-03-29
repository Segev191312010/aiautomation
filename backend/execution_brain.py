"""Execution brain for ranking rule and direct-AI candidates together."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from config import cfg
from portfolio_allocator import allocate_candidates

_pending_direct_candidates: list[dict] = []


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


def queue_direct_candidates(decisions: list[dict]) -> int:
    """Queue direct AI trade opportunities for execution in the next bot cycle."""
    now = datetime.now(timezone.utc).isoformat()
    queued = 0
    for decision in decisions:
        symbol = str(decision.get("symbol", "")).upper()
        if not symbol:
            continue
        _pending_direct_candidates.append({
            "symbol": symbol,
            "source": "ai_direct",
            "score": float(decision.get("confidence", 0.5)) * 100.0,
            "risk_pct": float(cfg.RISK_PER_TRADE_PCT),
            "is_exit": str(decision.get("action", "BUY")).upper() == "SELL",
            "decision": dict(decision),
            "queued_at": now,
        })
        queued += 1
    return queued


def drain_direct_candidates(max_age_seconds: int = 900) -> list[dict]:
    """
    Drain queued direct AI opportunities, dropping stale entries and keeping the
    highest-priority candidate per symbol.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    merged: dict[str, dict] = {}
    for candidate in list(_pending_direct_candidates):
        queued_at_raw = candidate.get("queued_at")
        try:
            queued_at = datetime.fromisoformat(str(queued_at_raw).replace("Z", "+00:00"))
        except Exception:
            queued_at = datetime.min.replace(tzinfo=timezone.utc)  # malformed = treat as expired
        if queued_at < cutoff:
            continue
        symbol = str(candidate.get("symbol", "")).upper()
        if not symbol:
            continue
        current = merged.get(symbol)
        if current is None or _priority(candidate) > _priority(current):
            merged[symbol] = dict(candidate)
    _pending_direct_candidates.clear()
    return list(merged.values())
