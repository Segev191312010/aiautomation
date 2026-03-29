"""Portfolio analytics engine — P&L tracking, sector exposure, correlation, performance."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# Sector cache (symbol -> sector) — refreshed daily
_sector_cache: dict[str, tuple[str, float]] = {}  # symbol -> (sector, timestamp)
_SECTOR_TTL = 86400  # 24 hours


def _get_sector(symbol: str) -> str:
    """Get GICS sector for a symbol via yfinance (cached 24h)."""
    import time
    cached = _sector_cache.get(symbol)
    if cached and (time.time() - cached[1]) < _SECTOR_TTL:
        return cached[0]
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info
        sector = info.get("sector", "Unknown")
        _sector_cache[symbol] = (sector, time.time())
        return sector
    except Exception:
        return "Unknown"


def compute_realized_pnl(trades: list[dict]) -> dict:
    """Compute realized P&L. S9: canonical outcomes scored directly, FIFO only for legacy."""
    matched: list[dict] = []
    total_pnl = 0.0

    # S9: split into canonical outcomes (use directly) and legacy rows (FIFO match)
    canonical_ids: set[str] = set()
    for trade in trades:
        if (trade.get("realized_pnl") is not None
                and trade.get("entry_price") is not None
                and trade.get("exit_price") is not None):
            matched.append({
                "symbol": trade.get("symbol", ""),
                "entry_date": trade.get("opened_at") or trade.get("timestamp", ""),
                "exit_date": trade.get("closed_at") or trade.get("timestamp", ""),
                "entry_price": trade["entry_price"],
                "exit_price": trade["exit_price"],
                "qty": abs(trade.get("quantity", 0)),
                "pnl": round(float(trade["realized_pnl"]), 2),
                "pnl_pct": round(float(trade.get("pnl_pct", 0)), 2),
                "hold_time": _hold_time(
                    trade.get("opened_at") or trade.get("timestamp", ""),
                    trade.get("closed_at") or trade.get("timestamp", ""),
                ),
            })
            total_pnl += float(trade["realized_pnl"])
            canonical_ids.add(trade.get("id", ""))
            # Also exclude the linked entry trade from FIFO
            if trade.get("position_id"):
                canonical_ids.add(trade["position_id"])

    # FIFO match only legacy rows (not already scored via canonical path)
    legacy_trades = [t for t in trades if t.get("id", "") not in canonical_ids]
    buys: dict[str, list[dict]] = defaultdict(list)
    sorted_legacy = sorted(legacy_trades, key=lambda t: t.get("timestamp", ""))

    for trade in sorted_legacy:
        symbol = trade.get("symbol", "")
        action = trade.get("action", "")
        qty = abs(trade.get("quantity", 0))
        price = trade.get("fill_price") or trade.get("price", 0)
        ts = trade.get("timestamp", "")

        if not price or not qty:
            continue

        if action == "BUY":
            buys[symbol].append({"qty": qty, "price": price, "ts": ts})
        elif action == "SELL":
            remaining = qty
            while remaining > 0 and buys[symbol]:
                lot = buys[symbol][0]
                fill_qty = min(remaining, lot["qty"])
                pnl = (price - lot["price"]) * fill_qty
                total_pnl += pnl
                matched.append({
                    "symbol": symbol,
                    "entry_date": lot["ts"],
                    "exit_date": ts,
                    "entry_price": lot["price"],
                    "exit_price": price,
                    "qty": fill_qty,
                    "pnl": round(pnl, 2),
                    "pnl_pct": round((price / lot["price"] - 1) * 100, 2) if lot["price"] else 0,
                    "hold_time": _hold_time(lot["ts"], ts),
                })
                lot["qty"] -= fill_qty
                if lot["qty"] <= 0:
                    buys[symbol].pop(0)
                remaining -= fill_qty

    winners = [m for m in matched if m["pnl"] > 0]
    losers = [m for m in matched if m["pnl"] <= 0]

    return {
        "total_pnl": round(total_pnl, 2),
        "trade_count": len(matched),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": round(len(winners) / len(matched) * 100, 1) if matched else 0,
        "avg_win": round(np.mean([m["pnl"] for m in winners]), 2) if winners else 0,
        "avg_loss": round(np.mean([m["pnl"] for m in losers]), 2) if losers else 0,
        "best_trade": max((m["pnl"] for m in matched), default=0),
        "worst_trade": min((m["pnl"] for m in matched), default=0),
        "profit_factor": round(
            abs(sum(m["pnl"] for m in winners)) / abs(sum(m["pnl"] for m in losers)), 2
        ) if losers and sum(m["pnl"] for m in losers) != 0 else 999.99,
        "matched_trades": matched,
    }


def _hold_time(entry_ts: str, exit_ts: str) -> str:
    """Compute human-readable hold time between two ISO timestamps."""
    try:
        entry = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
        exit_ = datetime.fromisoformat(exit_ts.replace("Z", "+00:00"))
        delta = exit_ - entry
        if delta.days > 0:
            return f"{delta.days}d"
        hours = delta.seconds // 3600
        return f"{hours}h" if hours > 0 else f"{delta.seconds // 60}m"
    except Exception:
        return "—"


def compute_unrealized_pnl(positions: list[dict]) -> dict:
    """Compute unrealized P&L from current positions."""
    total_unrealized = 0.0
    details = []
    for pos in positions:
        qty = pos.get("qty", 0)
        avg_cost = pos.get("avg_cost", 0)
        market_price = pos.get("market_price", pos.get("current_price", avg_cost))
        pnl = (market_price - avg_cost) * qty
        total_unrealized += pnl
        details.append({
            "symbol": pos.get("symbol", ""),
            "qty": qty,
            "avg_cost": avg_cost,
            "market_price": market_price,
            "unrealized_pnl": round(pnl, 2),
            "pnl_pct": round((market_price / avg_cost - 1) * 100, 2) if avg_cost else 0,
        })
    return {"total_unrealized_pnl": round(total_unrealized, 2), "positions": details}


def compute_daily_pnl(matched_trades: list[dict], days: int = 90) -> list[dict]:
    """Aggregate matched trades into daily P&L buckets."""
    daily: dict[str, float] = defaultdict(float)
    for t in matched_trades:
        date = t.get("exit_date", "")[:10]
        if date:
            daily[date] += t.get("pnl", 0)

    # Fill gaps
    if not daily:
        return []

    dates = sorted(daily.keys())
    start = datetime.strptime(dates[0], "%Y-%m-%d")
    end = datetime.strptime(dates[-1], "%Y-%m-%d")
    result = []
    cumulative = 0.0
    current = start
    while current <= end:
        d = current.strftime("%Y-%m-%d")
        pnl = round(daily.get(d, 0), 2)
        cumulative = round(cumulative + pnl, 2)
        result.append({"date": d, "pnl": pnl, "cumulative": cumulative})
        current += timedelta(days=1)

    return result[-days:]


def compute_sector_exposure(positions: list[dict]) -> list[dict]:
    """Map positions to GICS sectors and compute weights."""
    if not positions:
        return []

    total_value = sum(
        abs(p.get("market_value", p.get("qty", 0) * p.get("market_price", 0)))
        for p in positions
    )
    if total_value == 0:
        return []

    sector_values: dict[str, dict] = defaultdict(lambda: {"value": 0.0, "positions": 0})
    for pos in positions:
        symbol = pos.get("symbol", "")
        sector = _get_sector(symbol)
        value = abs(pos.get("market_value", pos.get("qty", 0) * pos.get("market_price", 0)))
        sector_values[sector]["value"] += value
        sector_values[sector]["positions"] += 1

    return sorted(
        [
            {
                "sector": s,
                "weight": round(d["value"] / total_value * 100, 1),
                "value": round(d["value"], 2),
                "positions": d["positions"],
            }
            for s, d in sector_values.items()
        ],
        key=lambda x: x["weight"],
        reverse=True,
    )


def compute_correlation_matrix(symbols: list[str], period_days: int = 90) -> dict | None:
    """Compute pairwise correlation matrix using daily returns from yfinance."""
    if len(symbols) < 2:
        return None

    try:
        import yfinance as yf
        end = datetime.now()
        start = end - timedelta(days=period_days + 10)
        data = yf.download(symbols, start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"), progress=False)

        if data.empty:
            return None

        close = data["Close"] if "Close" in data.columns else data
        returns = close.pct_change().dropna()

        if returns.empty or len(returns) < 5:
            return None

        corr = returns.corr()
        return {
            "symbols": list(corr.columns),
            "matrix": [[round(corr.iloc[i, j], 3) for j in range(len(corr.columns))] for i in range(len(corr.index))],
        }
    except Exception:
        log.exception("Failed to compute correlation matrix")
        return None


def compute_performance_metrics(matched_trades: list[dict], account_value: float = 100000) -> dict:
    """Compute portfolio performance metrics."""
    if not matched_trades:
        return {
            "total_return_pct": 0, "sharpe_ratio": 0, "sortino_ratio": 0,
            "win_rate": 0, "profit_factor": 0, "avg_hold_time": "—",
            "total_trades": 0,
        }

    pnls = [t["pnl"] for t in matched_trades]
    returns = [t["pnl_pct"] / 100 for t in matched_trades]

    total_return = sum(pnls)
    total_return_pct = (total_return / account_value) * 100 if account_value else 0
    avg_return = np.mean(returns) if returns else 0
    std_return = np.std(returns, ddof=1) if len(returns) > 1 else 0

    sharpe = (avg_return / std_return * np.sqrt(252)) if std_return > 0 else 0

    neg_returns = [r for r in returns if r < 0]
    downside_std = np.std(neg_returns, ddof=1) if len(neg_returns) > 1 else 0
    sortino = (avg_return / downside_std * np.sqrt(252)) if downside_std > 0 else 0

    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]
    win_rate = len(winners) / len(pnls) * 100 if pnls else 0
    profit_factor = abs(sum(winners)) / abs(sum(losers)) if losers and sum(losers) != 0 else 999.99

    return {
        "total_return": round(total_return, 2),
        "total_return_pct": round(total_return_pct, 2),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "avg_hold_time": matched_trades[0].get("hold_time", "—") if matched_trades else "—",
        "total_trades": len(matched_trades),
        "best_trade": round(max(pnls), 2),
        "worst_trade": round(min(pnls), 2),
    }
