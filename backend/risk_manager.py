"""Core risk management — position sizing, pre-trade checks, drawdown monitoring."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Literal

from risk_config import RiskLimits, DEFAULT_LIMITS

log = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    status: Literal["PASS", "WARN", "BLOCK"]
    reasons: list[str] = field(default_factory=list)


@dataclass
class DrawdownStatus:
    current_drawdown_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    peak_value: float = 0.0
    trough_value: float = 0.0
    drawdown_duration_days: int = 0
    is_breached: bool = False


def check_trade_risk(
    symbol: str,
    qty: int,
    side: str,
    positions: list[dict],
    account_value: float,
    limits: RiskLimits | None = None,
) -> RiskCheckResult:
    """Pre-trade risk check. Returns PASS, WARN, or BLOCK with reasons."""
    if limits is None:
        limits = DEFAULT_LIMITS
    reasons: list[str] = []
    status: Literal["PASS", "WARN", "BLOCK"] = "PASS"

    if account_value <= 0:
        return RiskCheckResult("BLOCK", ["Account value is zero or negative."])

    # Estimate price from existing position or default
    existing = next((p for p in positions if p.get("symbol") == symbol), None)
    est_price = (existing.get("market_price") or existing.get("avg_cost") or 100) if existing else 100
    order_pct = (qty * est_price / account_value) * 100

    # Position size check
    if order_pct > limits.max_position_pct:
        reasons.append(f"Position size {order_pct:.1f}% exceeds limit {limits.max_position_pct}%.")
        status = "BLOCK"
    elif order_pct > limits.max_position_pct * 0.8:
        reasons.append(f"Position size {order_pct:.1f}% approaching limit {limits.max_position_pct}%.")
        if status == "PASS":
            status = "WARN"

    # Open positions check
    open_count = len(positions)
    if side == "BUY" and not existing:
        if open_count >= limits.max_open_positions:
            reasons.append(f"Open positions ({open_count}) at limit ({limits.max_open_positions}).")
            status = "BLOCK"
        elif open_count >= limits.max_open_positions - 2:
            reasons.append(f"Open positions ({open_count}) near limit ({limits.max_open_positions}).")
            if status == "PASS":
                status = "WARN"

    # Concentration check
    if existing and side == "BUY":
        total_qty = existing.get("qty", 0) + qty
        total_pct = (total_qty * est_price / account_value) * 100
        if total_pct > limits.max_position_pct * 1.5:
            reasons.append(f"Combined position {total_pct:.1f}% exceeds concentration limit.")
            status = "BLOCK"

    if not reasons:
        reasons.append("All risk checks passed.")
    return RiskCheckResult(status=status, reasons=reasons)


def calculate_position_size(
    entry_price: float,
    stop_price: float | None,
    account_value: float,
    risk_pct: float = 1.0,
    method: str = "fixed_fractional",
    max_positions: int = 20,
    win_rate: float = 0.5,
    avg_win: float = 1.5,
    avg_loss: float = 1.0,
) -> dict:
    """Calculate recommended position size using the selected method."""
    if entry_price <= 0 or account_value <= 0:
        return {"shares": 0, "value": 0, "pct_of_portfolio": 0, "method": method}

    shares = 0

    if method == "fixed_fractional":
        risk_amount = account_value * (risk_pct / 100)
        if stop_price and stop_price > 0:
            risk_per_share = abs(entry_price - stop_price)
            shares = math.floor(risk_amount / max(risk_per_share, 0.01))
        else:
            shares = math.floor(risk_amount / entry_price)

    elif method == "kelly":
        b = avg_win / max(avg_loss, 0.01)
        kelly_f = max(0, min((win_rate * b - (1 - win_rate)) / b, 0.25))
        shares = math.floor(account_value * kelly_f / entry_price)

    elif method == "equal_weight":
        shares = math.floor(account_value / max_positions / entry_price)

    elif method == "atr":
        risk_amount = account_value * (risk_pct / 100)
        dist = abs(entry_price - stop_price) if stop_price else entry_price * 0.02
        shares = math.floor(risk_amount / max(dist, 0.01))

    shares = max(shares, 0)
    value = shares * entry_price
    pct = (value / account_value * 100) if account_value > 0 else 0

    return {
        "shares": shares,
        "value": round(value, 2),
        "pct_of_portfolio": round(pct, 2),
        "method": method,
        "entry_price": entry_price,
        "stop_price": stop_price,
    }


def check_drawdown(equity_history: list[dict]) -> DrawdownStatus:
    """Compute drawdown status from equity history."""
    if not equity_history:
        return DrawdownStatus()

    values = [e.get("value", e.get("equity", 0)) for e in equity_history]
    if not values:
        return DrawdownStatus()

    peak = values[0]
    max_dd = 0.0
    peak_val = values[0]
    trough_val = values[0]
    dd_start = 0

    for i, v in enumerate(values):
        if v > peak:
            peak = v
            dd_start = i
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
            trough_val = v
            peak_val = peak

    current_dd = (peak - values[-1]) / peak if peak > 0 else 0

    return DrawdownStatus(
        current_drawdown_pct=round(current_dd * 100, 2),
        max_drawdown_pct=round(max_dd * 100, 2),
        peak_value=round(peak_val, 2),
        trough_value=round(trough_val, 2),
        drawdown_duration_days=len(values) - dd_start,
        is_breached=False,
    )
