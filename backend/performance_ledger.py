"""Autopilot performance summaries grouped by source and rule."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from database import get_trades


def _window_cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=max(1, days))


def _trade_pnl(trade) -> float | None:
    if trade.metadata and "pnl" in trade.metadata:
        try:
            return float(trade.metadata["pnl"])
        except Exception:
            return None
    return None


async def compute_source_performance(days: int = 30) -> list[dict]:
    cutoff = _window_cutoff(days)
    trades = await get_trades(limit=1000)
    buckets: dict[str, dict] = {}

    for trade in trades:
        try:
            ts = datetime.fromisoformat(trade.timestamp.replace("Z", "+00:00"))
        except Exception:
            continue
        if ts < cutoff:
            continue
        source = trade.source
        bucket = buckets.setdefault(source, {
            "source": source,
            "trades_count": 0,
            "wins": 0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_cost": 0.0,
        })
        bucket["trades_count"] += 1
        pnl = _trade_pnl(trade)
        if pnl is not None:
            bucket["realized_pnl"] += pnl
            if pnl > 0:
                bucket["wins"] += 1

    results: list[dict] = []
    for bucket in buckets.values():
        trades_count = bucket["trades_count"]
        realized = float(bucket["realized_pnl"])
        cost = float(bucket["total_cost"])
        results.append({
            "source": bucket["source"],
            "trades_count": trades_count,
            "hit_rate": (bucket["wins"] / trades_count) if trades_count else None,
            "realized_pnl": realized,
            "unrealized_pnl": bucket["unrealized_pnl"],
            "total_cost": cost,
            "roi": (realized / cost) if cost > 0 else None,
        })
    return sorted(results, key=lambda item: item["source"])


async def compute_autopilot_performance(days: int = 30) -> dict:
    by_source = await compute_source_performance(days)
    combined_trades = sum(item["trades_count"] for item in by_source)
    combined_realized = sum(item["realized_pnl"] for item in by_source)
    combined_cost = sum(item["total_cost"] for item in by_source)
    weighted_hits = sum((item["hit_rate"] or 0) * item["trades_count"] for item in by_source)
    return {
        "window_days": days,
        "total_trades": combined_trades,
        "hit_rate": (weighted_hits / combined_trades) if combined_trades else None,
        "realized_pnl": combined_realized,
        "unrealized_pnl": 0.0,
        "total_cost": combined_cost,
        "roi": (combined_realized / combined_cost) if combined_cost > 0 else None,
        "by_source": by_source,
    }


async def compute_rule_performance(days: int = 30) -> list[dict]:
    cutoff = _window_cutoff(days)
    trades = await get_trades(limit=1000)
    buckets: dict[str, dict] = {}
    for trade in trades:
        try:
            ts = datetime.fromisoformat(trade.timestamp.replace("Z", "+00:00"))
        except Exception:
            continue
        if ts < cutoff:
            continue
        bucket = buckets.setdefault(trade.rule_id, {
            "rule_id": trade.rule_id,
            "rule_name": trade.rule_name,
            "trades_count": 0,
            "wins": 0,
            "net_pnl": 0.0,
            "source": trade.source,
        })
        bucket["trades_count"] += 1
        pnl = _trade_pnl(trade)
        if pnl is not None:
            bucket["net_pnl"] += pnl
            if pnl > 0:
                bucket["wins"] += 1
    return [
        {
            **bucket,
            "hit_rate": (bucket["wins"] / bucket["trades_count"]) if bucket["trades_count"] else None,
        }
        for bucket in sorted(buckets.values(), key=lambda item: item["net_pnl"], reverse=True)
    ]
