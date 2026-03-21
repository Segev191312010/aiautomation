"""Core risk management — position sizing, pre-trade checks, drawdown monitoring."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Literal

from risk_config import RiskLimits, DEFAULT_LIMITS
from config import cfg

log = logging.getLogger(__name__)

# ── GICS sector map (top symbols) ────────────────────────────────────────────
_SECTOR_MAP: dict[str, str] = {
    "AAPL": "Tech", "MSFT": "Tech", "GOOGL": "Tech", "GOOG": "Tech", "NVDA": "Tech",
    "META": "Tech", "AVGO": "Tech", "ORCL": "Tech", "CSCO": "Tech", "ADBE": "Tech",
    "CRM": "Tech", "AMD": "Tech", "INTC": "Tech", "QCOM": "Tech", "TXN": "Tech",
    "AMZN": "ConsDisc", "TSLA": "ConsDisc", "HD": "ConsDisc", "NKE": "ConsDisc",
    "MCD": "ConsDisc", "SBUX": "ConsDisc", "TGT": "ConsDisc", "LOW": "ConsDisc",
    "JPM": "Finance", "BAC": "Finance", "GS": "Finance", "MS": "Finance",
    "WFC": "Finance", "C": "Finance", "BLK": "Finance", "SCHW": "Finance",
    "JNJ": "Health", "UNH": "Health", "PFE": "Health", "ABBV": "Health",
    "MRK": "Health", "LLY": "Health", "TMO": "Health", "ABT": "Health",
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy", "SLB": "Energy",
    "PG": "Staples", "KO": "Staples", "PEP": "Staples", "WMT": "Staples", "COST": "Staples",
    "NEE": "Utilities", "DUK": "Utilities", "SO": "Utilities",
    "AMT": "RealEstate", "PLD": "RealEstate", "AVB": "RealEstate",
    "CAT": "Industrials", "UNP": "Industrials", "HON": "Industrials", "BA": "Industrials",
    "LIN": "Materials", "APD": "Materials", "SHW": "Materials",
    "SPY": "ETF", "QQQ": "ETF", "IWM": "ETF", "XLB": "ETF", "XLV": "ETF", "XLU": "ETF",
}


def get_sector(symbol: str) -> str:
    return _SECTOR_MAP.get(symbol.upper(), "Other")


# ── Account state (fetched once per cycle) ───────────────────────────────────

def get_account_state(ib) -> dict:
    """Pull equity, cash, daily P&L, positions from IBKR in one call."""
    equity = 0.0
    cash = 0.0
    daily_pnl = 0.0
    try:
        for av in ib.accountValues():
            if av.currency != "USD":
                continue
            if av.tag == "NetLiquidation":
                equity = float(av.value)
            elif av.tag == "AvailableFunds":
                cash = float(av.value)
            elif av.tag == "RealizedPnL":
                daily_pnl = float(av.value)
    except Exception as e:
        log.warning("Failed to read account values: %s", e)

    positions = []
    try:
        for p in ib.positions():
            if p.position == 0:
                continue
            positions.append({
                "symbol": p.contract.symbol,
                "qty": p.position,
                "avg_cost": p.avgCost,
                "market_price": p.avgCost,
                "sector": get_sector(p.contract.symbol),
            })
    except Exception as e:
        log.warning("Failed to read positions: %s", e)

    return {"equity": equity, "cash": cash, "daily_pnl": daily_pnl, "positions": positions}


def check_drawdown_live(equity: float, peak_equity: float) -> bool:
    """True if drawdown exceeds MAX_TOTAL_DRAWDOWN → bot should pause."""
    if peak_equity <= 0:
        return False
    dd = (peak_equity - equity) / peak_equity
    return dd >= cfg.MAX_TOTAL_DRAWDOWN


def check_daily_loss(daily_pnl: float, equity: float) -> bool:
    """True if today's loss exceeds MAX_DAILY_RISK → skip new entries."""
    if equity <= 0:
        return False
    return daily_pnl < -(equity * cfg.MAX_DAILY_RISK)


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
    est_price: float = 0,
) -> RiskCheckResult:
    """Pre-trade risk check. Returns PASS, WARN, or BLOCK with reasons."""
    if limits is None:
        limits = DEFAULT_LIMITS
    reasons: list[str] = []
    status: Literal["PASS", "WARN", "BLOCK"] = "PASS"

    if account_value <= 0:
        return RiskCheckResult("BLOCK", ["Account value is zero or negative."])

    # Price: use provided, or fallback to position/default
    if est_price <= 0:
        existing = next((p for p in positions if p.get("symbol") == symbol), None)
        est_price = (existing.get("market_price") or existing.get("avg_cost") or 100) if existing else 100
    order_pct = (qty * est_price / account_value) * 100

    # Cash-only: reject SELL if no long position
    if side == "BUY" and not cfg.SHORT_ALLOWED:
        pass  # BUY is always fine
    elif side == "SELL":
        held = next((p for p in positions if p.get("symbol") == symbol and p.get("qty", 0) > 0), None)
        if not held:
            return RiskCheckResult("BLOCK", [f"SELL rejected: no long position in {symbol} (cash account)."])

    # Position count check (use cfg)
    open_count = len([p for p in positions if p.get("qty", 0) > 0])
    if side == "BUY" and open_count >= cfg.MAX_POSITIONS_TOTAL:
        return RiskCheckResult("BLOCK", [f"Max positions ({cfg.MAX_POSITIONS_TOTAL}) reached."])

    # Sector concentration check
    target_sector = get_sector(symbol)
    if side == "BUY" and target_sector != "Other":
        sector_count = sum(1 for p in positions if get_sector(p.get("symbol", "")) == target_sector and p.get("qty", 0) > 0)
        if sector_count >= cfg.MAX_POSITIONS_PER_SECTOR:
            return RiskCheckResult("BLOCK", [f"Sector '{target_sector}' has {sector_count} positions (limit {cfg.MAX_POSITIONS_PER_SECTOR})."])

    # Already holding check
    if side == "BUY":
        already = next((p for p in positions if p.get("symbol") == symbol and p.get("qty", 0) > 0), None)
        if already:
            return RiskCheckResult("BLOCK", [f"Already holding {symbol}."])

    # Position size check — use configurable limit
    max_pct = limits.max_position_pct
    if order_pct > max_pct:
        reasons.append(f"Position size {order_pct:.1f}% exceeds limit {limits.max_position_pct}%.")
        status = "BLOCK"
    elif order_pct > limits.max_position_pct * 0.8:
        reasons.append(f"Position size {order_pct:.1f}% approaching limit {limits.max_position_pct}%.")
        if status == "PASS":
            status = "WARN"

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
