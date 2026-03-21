"""
Sector Rotation Analysis — RS ratios, momentum, quadrant placement, and sector leaders.

Endpoints:
    GET /api/sectors/rotation        — Sector quadrant data (RS ratio + momentum)
    GET /api/sectors/heatmap         — Multi-timeframe performance grid
    GET /api/sectors/{etf}/leaders   — Top stocks within a sector
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np
import yfinance as yf

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4)

# ── Sector definitions ────────────────────────────────────────────────────────

SECTORS = {
    "XLK":  {"name": "Technology",              "gics": "Information Technology"},
    "XLF":  {"name": "Financials",              "gics": "Financials"},
    "XLV":  {"name": "Healthcare",              "gics": "Health Care"},
    "XLE":  {"name": "Energy",                  "gics": "Energy"},
    "XLI":  {"name": "Industrials",             "gics": "Industrials"},
    "XLY":  {"name": "Consumer Discretionary",  "gics": "Consumer Discretionary"},
    "XLP":  {"name": "Consumer Staples",        "gics": "Consumer Staples"},
    "XLU":  {"name": "Utilities",               "gics": "Utilities"},
    "XLRE": {"name": "Real Estate",             "gics": "Real Estate"},
    "XLC":  {"name": "Communication",           "gics": "Communication Services"},
    "XLB":  {"name": "Materials",               "gics": "Materials"},
}

# ── Stock-to-sector mapping (top SP500 constituents) ──────────────────────────

SP500_SECTORS: dict[str, str] = {
    # Technology (XLK)
    **{s: "XLK" for s in [
        "AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "AMD", "ADBE", "CSCO",
        "INTC", "ACN", "IBM", "TXN", "QCOM", "INTU", "NOW", "AMAT", "ADI",
        "LRCX", "KLAC", "SNPS", "CDNS", "MRVL", "FTNT", "PANW", "CRWD",
        "MSI", "ANSS", "KEYS", "HPQ", "HPE", "DELL", "WDC", "ON", "NXPI",
        "MPWR", "SWKS", "TER", "ZBRA", "EPAM", "IT", "CTSH", "AKAM",
    ]},
    # Financials (XLF)
    **{s: "XLF" for s in [
        "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "SPGI", "BLK", "C",
        "AXP", "PGR", "CME", "ICE", "CB", "MMC", "AON", "MET", "AIG",
        "TFC", "USB", "PNC", "SCHW", "COF", "BK", "STT", "FIS", "FITB",
        "RF", "CFG", "KEY", "HBAN", "MTB", "NTRS", "CINF", "ZION", "GL",
    ]},
    # Healthcare (XLV)
    **{s: "XLV" for s in [
        "UNH", "LLY", "JNJ", "ABBV", "MRK", "TMO", "ABT", "DHR", "PFE",
        "AMGN", "ISRG", "MDT", "GILD", "VRTX", "BSX", "SYK", "ZTS", "BDX",
        "CI", "ELV", "HCA", "MCK", "REGN", "A", "IQV", "BAX", "EW",
        "IDXX", "MTD", "RMD", "HOLX", "TECH", "ALGN", "DXCM", "PODD",
    ]},
    # Energy (XLE)
    **{s: "XLE" for s in [
        "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PXD", "PSX", "VLO",
        "OXY", "HAL", "DVN", "HES", "FANG", "BKR", "CTRA", "MRO", "APA",
        "TRGP", "KMI", "WMB", "OKE",
    ]},
    # Industrials (XLI)
    **{s: "XLI" for s in [
        "GE", "CAT", "HON", "UNP", "RTX", "BA", "DE", "LMT", "MMM",
        "UPS", "FDX", "GD", "NOC", "WM", "EMR", "ITW", "ETN", "PH",
        "CTAS", "TT", "CARR", "CSX", "NSC", "PCAR", "AME", "FAST",
        "RSG", "VRSK", "GWW", "HWM", "ROK", "IR", "DOV", "WAB",
    ]},
    # Consumer Discretionary (XLY)
    **{s: "XLY" for s in [
        "AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG",
        "ABNB", "CMG", "ORLY", "ROST", "DHI", "LEN", "GM", "F", "MAR",
        "HLT", "YUM", "DPZ", "APTV", "GRMN", "POOL", "ULTA", "BBY",
        "EBAY", "ETSY", "EXPE", "LVS", "WYNN", "MGM", "CZR",
    ]},
    # Consumer Staples (XLP)
    **{s: "XLP" for s in [
        "PG", "KO", "PEP", "COST", "WMT", "PM", "MO", "MDLZ", "CL",
        "KMB", "GIS", "SJM", "ADM", "STZ", "KHC", "HSY", "MKC", "CHD",
        "CLX", "K", "CAG", "TSN", "HRL", "SYY", "KR", "TAP",
    ]},
    # Utilities (XLU)
    **{s: "XLU" for s in [
        "NEE", "DUK", "SO", "D", "AEP", "SRE", "EXC", "XEL", "WEC",
        "ED", "ES", "AEE", "DTE", "CMS", "PPL", "AWK", "EVRG", "NI",
        "ATO", "PNW", "LNT",
    ]},
    # Communication (XLC)
    **{s: "XLC" for s in [
        "META", "GOOGL", "GOOG", "NFLX", "DIS", "CMCSA", "T", "VZ",
        "TMUS", "CHTR", "EA", "TTWO", "WBD", "PARA", "FOX", "FOXA",
        "OMC", "IPG", "NWSA", "NWS", "MTCH", "LYV",
    ]},
    # Real Estate (XLRE)
    **{s: "XLRE" for s in [
        "PLD", "AMT", "EQIX", "CCI", "SPG", "O", "PSA", "WELL", "DLR",
        "VICI", "ARE", "AVB", "EQR", "MAA", "UDR", "ESS", "CPT",
        "SBA", "REG", "FRT", "KIM", "BXP", "HST",
    ]},
    # Materials (XLB)
    **{s: "XLB" for s in [
        "LIN", "APD", "SHW", "ECL", "FCX", "NEM", "NUE", "DOW", "DD",
        "PPG", "VMC", "MLM", "CF", "MOS", "ALB", "IFF", "CE", "EMN",
        "IP", "PKG", "WRK", "SEE", "AVY", "FMC", "BALL",
    ]},
}

# ── Cache ─────────────────────────────────────────────────────────────────────

_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL_ROTATION = 300   # 5 min
CACHE_TTL_LEADERS = 900    # 15 min
CACHE_TTL_HEATMAP = 300    # 5 min


def _cache_get(key: str, ttl: float) -> Any | None:
    if key in _cache:
        ts, val = _cache[key]
        if time.time() - ts < ttl:
            return val
    return None


def _cache_set(key: str, val: Any) -> None:
    _cache[key] = (time.time(), val)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _yf_download(symbols: list[str], period: str = "1y") -> dict:
    """Download bars for multiple symbols via yfinance. Returns {symbol: DataFrame}."""
    if not symbols:
        return {}
    try:
        tickers_str = " ".join(symbols)
        data = yf.download(tickers_str, period=period, progress=False, threads=True)
        if data.empty:
            return {}

        result = {}
        # Single symbol: data columns are simple (Open, High, Low, Close, Volume)
        if len(symbols) == 1:
            if not data.empty:
                result[symbols[0]] = data
        else:
            # Multi-symbol: MultiIndex columns (field, symbol)
            for sym in symbols:
                try:
                    sym_data = data.xs(sym, level="Ticker", axis=1) if "Ticker" in (data.columns.names or []) else data[sym] if sym in data.columns.get_level_values(0) else None
                    if sym_data is not None and not sym_data.empty:
                        result[sym] = sym_data
                except (KeyError, TypeError):
                    continue
        return result
    except Exception as exc:
        log.error("yfinance download failed for %s: %s", symbols[:5], exc)
        return {}


def _pct_change(series, periods: int) -> float:
    """Calculate % change over N periods from the end of a series."""
    if series is None or len(series) < periods + 1:
        return 0.0
    try:
        start = float(series.iloc[-(periods + 1)])
        end = float(series.iloc[-1])
        if start == 0:
            return 0.0
        return ((end - start) / start) * 100
    except (IndexError, ValueError, TypeError):
        return 0.0


# ── Sector Rotation ──────────────────────────────────────────────────────────

def _compute_rotation(lookback_days: int = 90) -> list[dict]:
    """Compute RS ratio, momentum, and quadrant for each sector ETF vs SPY."""
    etfs = list(SECTORS.keys()) + ["SPY"]
    period = f"{max(lookback_days + 60, 365)}d"  # extra buffer for SMA calc

    data = _yf_download(etfs, period=period)
    if "SPY" not in data:
        log.error("SPY data not available for rotation calc")
        return []

    spy_close = data["SPY"]["Close"].dropna()
    if spy_close.empty:
        return []

    results = []
    for etf, info in SECTORS.items():
        if etf not in data:
            continue

        close = data[etf]["Close"].dropna()
        if len(close) < 60:
            continue

        # Align to SPY dates
        aligned = close.reindex(spy_close.index).dropna()
        spy_aligned = spy_close.reindex(aligned.index).dropna()
        if len(aligned) < 60:
            continue

        # RS ratio (normalized — starts at 1.0)
        rs_raw = aligned / spy_aligned
        rs_norm = rs_raw / rs_raw.iloc[0]

        # RS SMA (50-period)
        rs_sma = rs_norm.rolling(50).mean()

        # RS Momentum (20-period rate of change)
        if len(rs_norm) >= 20:
            rs_momentum = float((rs_norm.iloc[-1] - rs_norm.iloc[-20]) / rs_norm.iloc[-20])
        else:
            rs_momentum = 0.0

        rs_current = float(rs_norm.iloc[-1])
        rs_sma_current = float(rs_sma.iloc[-1]) if not np.isnan(rs_sma.iloc[-1]) else rs_current

        # Quadrant
        above_sma = rs_current > rs_sma_current
        mom_positive = rs_momentum > 0

        if above_sma and mom_positive:
            quadrant = "LEADING"
        elif above_sma and not mom_positive:
            quadrant = "WEAKENING"
        elif not above_sma and mom_positive:
            quadrant = "IMPROVING"
        else:
            quadrant = "LAGGING"

        # Performance across timeframes
        results.append({
            "symbol": etf,
            "name": info["name"],
            "quadrant": quadrant,
            "rs_ratio": round(rs_current, 4),
            "rs_momentum": round(rs_momentum, 4),
            "rs_sma": round(rs_sma_current, 4),
            "perf_1w": round(_pct_change(close, 5), 2),
            "perf_1m": round(_pct_change(close, 21), 2),
            "perf_3m": round(_pct_change(close, 63), 2),
            "perf_6m": round(_pct_change(close, 126), 2),
            "perf_1y": round(_pct_change(close, 252), 2),
            "price": round(float(close.iloc[-1]), 2),
            "volume": int(data[etf]["Volume"].iloc[-1]) if "Volume" in data[etf] else 0,
        })

    # Sort by RS momentum descending
    results.sort(key=lambda x: x["rs_momentum"], reverse=True)
    return results


async def get_sector_rotation(lookback_days: int = 90) -> list[dict]:
    """Get sector rotation data (cached 5 min)."""
    key = f"rotation_{lookback_days}"
    cached = _cache_get(key, CACHE_TTL_ROTATION)
    if cached is not None:
        return cached

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_executor, _compute_rotation, lookback_days)
    _cache_set(key, result)
    return result


# ── Heatmap ───────────────────────────────────────────────────────────────────

def _compute_heatmap() -> list[dict]:
    """Compute multi-timeframe performance for all sectors."""
    etfs = list(SECTORS.keys())
    data = _yf_download(etfs, period="2y")

    results = []
    for etf, info in SECTORS.items():
        if etf not in data:
            continue
        close = data[etf]["Close"].dropna()
        if close.empty:
            continue

        # YTD: from first trading day of the year
        ytd = 0.0
        try:
            current_year = close.index[-1].year
            year_start = close[close.index.year == current_year]
            if len(year_start) > 1:
                ytd = ((float(year_start.iloc[-1]) - float(year_start.iloc[0])) / float(year_start.iloc[0])) * 100
        except Exception:
            pass

        results.append({
            "symbol": etf,
            "name": info["name"],
            "1w": round(_pct_change(close, 5), 2),
            "1m": round(_pct_change(close, 21), 2),
            "3m": round(_pct_change(close, 63), 2),
            "6m": round(_pct_change(close, 126), 2),
            "1y": round(_pct_change(close, 252), 2),
            "ytd": round(ytd, 2),
        })

    results.sort(key=lambda x: x["1m"], reverse=True)
    return results


async def get_rotation_heatmap() -> list[dict]:
    """Get sector performance heatmap (cached 5 min)."""
    cached = _cache_get("heatmap", CACHE_TTL_HEATMAP)
    if cached is not None:
        return cached

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_executor, _compute_heatmap)
    _cache_set("heatmap", result)
    return result


# ── Sector Leaders ────────────────────────────────────────────────────────────

def _compute_leaders(sector_etf: str, top_n: int = 10, period: str = "3mo") -> dict:
    """Find top-performing stocks within a sector."""
    sector_etf = sector_etf.upper()
    if sector_etf not in SECTORS:
        return {"sector": sector_etf, "sector_name": "Unknown", "leaders": []}

    # Get stocks in this sector
    stocks = [sym for sym, sec in SP500_SECTORS.items() if sec == sector_etf]
    if not stocks:
        return {"sector": sector_etf, "sector_name": SECTORS[sector_etf]["name"], "leaders": []}

    # Download all stocks + sector ETF
    data = _yf_download(stocks + [sector_etf], period=period)

    # Get sector ETF performance for RS comparison
    sector_perf = 0.0
    if sector_etf in data:
        sc = data[sector_etf]["Close"].dropna()
        if len(sc) >= 2:
            sector_perf = ((float(sc.iloc[-1]) - float(sc.iloc[0])) / float(sc.iloc[0])) * 100

    leaders = []
    for sym in stocks:
        if sym not in data:
            continue
        close = data[sym]["Close"].dropna()
        if len(close) < 2:
            continue

        perf = ((float(close.iloc[-1]) - float(close.iloc[0])) / float(close.iloc[0])) * 100
        price = float(close.iloc[-1])
        vol = int(data[sym]["Volume"].iloc[-1]) if "Volume" in data[sym] else 0

        # RS vs sector
        rs_vs = (1 + perf / 100) / (1 + sector_perf / 100) if sector_perf != -100 else 1.0

        leaders.append({
            "symbol": sym,
            "name": sym,  # yfinance doesn't give names in batch download
            "perf": round(perf, 2),
            "price": round(price, 2),
            "volume": vol,
            "rs_vs_sector": round(rs_vs, 3),
        })

    leaders.sort(key=lambda x: x["perf"], reverse=True)

    return {
        "sector": sector_etf,
        "sector_name": SECTORS[sector_etf]["name"],
        "leaders": leaders[:top_n],
    }


async def get_sector_leaders(sector_etf: str, top_n: int = 10, period: str = "3mo") -> dict:
    """Get sector leaders (cached 15 min)."""
    key = f"leaders_{sector_etf}_{top_n}_{period}"
    cached = _cache_get(key, CACHE_TTL_LEADERS)
    if cached is not None:
        return cached

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_executor, _compute_leaders, sector_etf, top_n, period)
    _cache_set(key, result)
    return result
