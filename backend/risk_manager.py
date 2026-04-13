"""Core risk management — position sizing, pre-trade checks, drawdown monitoring."""
from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
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


# Dynamic cache with TTL (24h) for yfinance lookups
_SECTOR_CACHE_TTL = 86_400  # 24 hours
_dynamic_sector_cache: dict[str, tuple[str, float]] = {}  # symbol -> (sector, timestamp)


def get_sector(symbol: str) -> str | None:
    """Get sector for symbol. Uses static map first, then TTL-cached yfinance, then 'Unknown'."""
    sym = symbol.upper()
    if sym in _SECTOR_MAP:
        return _SECTOR_MAP[sym]
    cached = _dynamic_sector_cache.get(sym)
    if cached and (time.monotonic() - cached[1]) < _SECTOR_CACHE_TTL:
        return cached[0]
    # Try yfinance lookup (cached after first call)
    try:
        import yfinance as yf
        info = yf.Ticker(sym).info
        sector = info.get("sector")
        if sector:
            _dynamic_sector_cache[sym] = (sector, time.monotonic())
            return sector
    except Exception:
        pass
    # Unknown sector — still track for concentration limits
    _dynamic_sector_cache[sym] = ("Unknown", time.monotonic())
    return "Unknown"


def prefetch_sectors(symbols: list[str]) -> None:
    """Batch pre-fetch sector data for symbols not in static map or cache.

    Runs yfinance lookups in parallel to warm the cache before trade evaluation.
    """
    now = time.monotonic()
    unknown = [
        s.upper() for s in symbols
        if s.upper() not in _SECTOR_MAP
        and (s.upper() not in _dynamic_sector_cache
             or (now - _dynamic_sector_cache[s.upper()][1]) >= _SECTOR_CACHE_TTL)
    ]
    if not unknown:
        return
    log.info("Pre-fetching sector data for %d symbol(s): %s", len(unknown), unknown[:10])

    def _lookup(sym: str) -> tuple[str, str]:
        try:
            import yfinance as yf
            info = yf.Ticker(sym).info
            return (sym, info.get("sector", "Unknown"))
        except Exception:
            return (sym, "Unknown")

    with ThreadPoolExecutor(max_workers=min(len(unknown), 6)) as pool:
        for sym, sector in pool.map(_lookup, unknown):
            _dynamic_sector_cache[sym] = (sector, time.monotonic())
    log.info("Sector pre-fetch complete for %d symbol(s)", len(unknown))


# ── Account state (fetched once per cycle) ───────────────────────────────────

def get_account_state(ib) -> dict:
    """Pull equity, cash, daily P&L (realized + unrealized), positions from IBKR."""
    equity = 0.0
    cash = 0.0
    realized_pnl = 0.0
    unrealized_pnl = 0.0
    try:
        for av in ib.accountValues():
            if av.currency != "USD":
                continue
            if av.tag == "NetLiquidation":
                equity = float(av.value)
            elif av.tag == "AvailableFunds":
                cash = float(av.value)
            elif av.tag == "RealizedPnL":
                realized_pnl = float(av.value)
            elif av.tag == "UnrealizedPnL":
                unrealized_pnl = float(av.value)
    except Exception as e:
        log.warning("Failed to read account values: %s", e)
    daily_pnl = realized_pnl + unrealized_pnl

    positions = []
    try:
        # I-2 FIX: Use ib.portfolio() which provides marketPrice (not just avgCost)
        for p in ib.portfolio():
            if p.position == 0:
                continue
            import math
            # Guard against nan, UNSET_DOUBLE (~1.8e308), zero, and None
            _mp = p.marketPrice
            _valid_mp = (_mp is not None and not math.isnan(_mp) and 0 < _mp < 1e7)
            _ac = p.averageCost
            _valid_ac = (_ac is not None and not math.isnan(_ac) and 0 < _ac < 1e7)
            mkt_price = _mp if _valid_mp else (_ac if _valid_ac else 100.0)
            positions.append({
                "symbol": p.contract.symbol,
                "qty": p.position,
                "avg_cost": p.averageCost,
                "market_price": mkt_price,
                "market_value": p.marketValue,
                "sector": get_sector(p.contract.symbol) or "Unknown",
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

    # Price: use provided, or fallback to position data — never guess
    if est_price <= 0:
        existing = next((p for p in positions if p.get("symbol") == symbol), None)
        if existing:
            est_price = existing.get("market_price") or existing.get("avg_cost") or 0
        if est_price <= 0:
            return RiskCheckResult("BLOCK", [f"Cannot estimate price for {symbol} — blocking for safety."])
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
    if side == "BUY" and target_sector is not None:
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


# ── Portfolio Concentration Enforcement ────────────────────────────────────


@dataclass
class PortfolioImpactResult:
    """Structured result from check_portfolio_impact(). Serialization-safe."""
    allowed: bool
    reason: str  # pass | sell_exit | sector_limit | correlation_limit | error_skip | degraded_data_skip
    symbol: str = ""
    side: str = ""
    sector: str | None = None
    sector_weight_before: float | None = None
    sector_weight_after: float | None = None
    correlated_count: int | None = None
    corr_threshold: float | None = None
    max_correlated_positions: int | None = None
    details: str | None = None


def compute_current_sector_exposure(
    positions: list[dict],
    net_liq: float,
) -> dict[str, float]:
    """Compute current sector exposure as % of net liquidation.

    positions: list of dicts with 'symbol' and 'market_value' (or 'qty'+'market_price').
    Returns: {sector: pct_of_net_liq}
    """
    if net_liq <= 0:
        return {}
    sector_notional: dict[str, float] = {}
    for p in positions:
        sym = p.get("symbol", "")
        val = p.get("market_value") or (p.get("qty", 0) * p.get("market_price", 0))
        if not val or val <= 0:
            continue
        sector = get_sector(sym) or "Other"
        sector_notional[sector] = sector_notional.get(sector, 0) + val
    return {s: round(v / net_liq * 100, 2) for s, v in sector_notional.items()}


def _count_correlated_positions(
    candidate_symbol: str,
    held_symbols: list[str],
    corr_matrix: dict,
    threshold: float,
) -> int:
    """Count held_symbols whose abs(corr) with candidate exceeds threshold.

    corr_matrix format: {"symbols": ["A","B",...], "matrix": [[1.0, 0.9,...], ...]}
    Ignores self-correlation, NaN, and missing entries. Returns 0 on any issue.
    """
    symbols = corr_matrix.get("symbols", [])
    matrix = corr_matrix.get("matrix", [])
    cand_upper = candidate_symbol.upper()
    if cand_upper not in [s.upper() for s in symbols]:
        return 0
    cand_idx = next(i for i, s in enumerate(symbols) if s.upper() == cand_upper)

    count = 0
    for held in held_symbols:
        held_upper = held.upper()
        if held_upper == cand_upper:
            continue  # skip self
        try:
            held_idx = next(i for i, s in enumerate(symbols) if s.upper() == held_upper)
        except StopIteration:
            continue  # not in matrix
        try:
            corr_val = matrix[cand_idx][held_idx]
            if corr_val is None or (isinstance(corr_val, float) and math.isnan(corr_val)):
                continue
            if abs(corr_val) > threshold:
                count += 1
        except (IndexError, TypeError):
            continue
    return count


def check_portfolio_impact(
    symbol: str,
    side: str,
    positions: list[dict],
    net_liq: float,
    candidate_notional: float = 0,
    pending_orders: list[dict] | None = None,
    approved_candidates: list[dict] | None = None,
    corr_matrix: dict | None = None,
    limits: RiskLimits | None = None,
) -> PortfolioImpactResult:
    """Check portfolio concentration before placing an order.

    1. Exits always PASS (reason=sell_exit)
    2. Sector check: project exposure, block if > max_sector_pct
    3. Correlation check: count correlated positions, block if >= max_correlated_positions
    4. Wrapped in try/except: on error → allowed=True, reason=error_skip
    """
    if limits is None:
        limits = DEFAULT_LIMITS
    sym = symbol.upper()

    try:
        # ── Exits always pass ─────────────────────────────────────────────
        if side.upper() in ("SELL", "SELL_EXIT", "EXIT"):
            return PortfolioImpactResult(
                allowed=True, reason="sell_exit", symbol=sym, side=side,
            )

        # ── Gather all relevant exposure ──────────────────────────────────
        all_positions = list(positions or [])
        for order in (pending_orders or []):
            if order.get("side", "").upper() == "BUY":
                all_positions.append(order)
        for cand in (approved_candidates or []):
            if cand.get("side", "").upper() == "BUY":
                all_positions.append(cand)

        # ── Sector check ──────────────────────────────────────────────────
        candidate_sector = get_sector(sym)
        sector_degraded = candidate_sector is None

        sector_before: float | None = None
        sector_after: float | None = None

        if not sector_degraded and net_liq > 0:
            # Current sector exposure (including pending + approved)
            exposure = compute_current_sector_exposure(all_positions, net_liq)
            sector_before = exposure.get(candidate_sector, 0.0)
            # Project after adding candidate
            sector_after = sector_before + (candidate_notional / net_liq * 100 if net_liq > 0 else 0)

            if sector_after > limits.max_sector_pct:
                return PortfolioImpactResult(
                    allowed=False,
                    reason="sector_limit",
                    symbol=sym,
                    side=side,
                    sector=candidate_sector,
                    sector_weight_before=round(sector_before, 2),
                    sector_weight_after=round(sector_after, 2),
                    details=f"Sector '{candidate_sector}' would reach {sector_after:.1f}% (limit {limits.max_sector_pct}%)",
                )

        # ── Correlation check ─────────────────────────────────────────────
        if corr_matrix is None:
            if sector_degraded:
                # C2 safety fix: fail CLOSED when both sector and correlation
                # data are unavailable. Conservative 5% position / 20% sector
                # defaults would still be speculative — better to block.
                return PortfolioImpactResult(
                    allowed=False, reason="degraded_data_block", symbol=sym, side=side,
                    sector=candidate_sector,
                    details="Sector unknown AND correlation matrix unavailable — blocking for safety",
                )
            # Sector data available but no correlation matrix — allow with
            # a warning (sector check already ran above and would have blocked
            # if the sector was over-concentrated).
            return PortfolioImpactResult(
                allowed=True, reason="degraded_corr_skip", symbol=sym, side=side,
                sector=candidate_sector,
                sector_weight_before=round(sector_before, 2) if sector_before is not None else None,
                sector_weight_after=round(sector_after, 2) if sector_after is not None else None,
                details="Correlation matrix unavailable — sector check passed, correlation skipped",
            )

        # Build list of all relevant symbols for correlation counting
        relevant_symbols = []
        for p in all_positions:
            s = p.get("symbol", "")
            if s and s.upper() != sym:
                relevant_symbols.append(s.upper())
        relevant_symbols = list(set(relevant_symbols))

        corr_count = _count_correlated_positions(
            sym, relevant_symbols, corr_matrix, limits.corr_threshold,
        )

        if corr_count >= limits.max_correlated_positions:
            return PortfolioImpactResult(
                allowed=False,
                reason="correlation_limit",
                symbol=sym,
                side=side,
                sector=candidate_sector,
                sector_weight_before=round(sector_before, 2) if sector_before is not None else None,
                sector_weight_after=round(sector_after, 2) if sector_after is not None else None,
                correlated_count=corr_count,
                corr_threshold=limits.corr_threshold,
                max_correlated_positions=limits.max_correlated_positions,
                details=f"{corr_count} positions correlated >{limits.corr_threshold} with {sym} (limit {limits.max_correlated_positions})",
            )

        # ── All clear ─────────────────────────────────────────────────────
        return PortfolioImpactResult(
            allowed=True,
            reason="pass",
            symbol=sym,
            side=side,
            sector=candidate_sector,
            sector_weight_before=round(sector_before, 2) if sector_before is not None else None,
            sector_weight_after=round(sector_after, 2) if sector_after is not None else None,
            correlated_count=corr_count,
            corr_threshold=limits.corr_threshold,
            max_correlated_positions=limits.max_correlated_positions,
        )

    except Exception as exc:
        log.error("Portfolio impact check FAILED (error_blocked) for %s: %s", symbol, exc)
        return PortfolioImpactResult(
            allowed=False, reason="error_blocked", symbol=sym, side=side,
            details=f"Internal error — blocking for safety: {exc}",
        )


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
