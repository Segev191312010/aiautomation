"""
IBKR Scanner — server-side market scanning via TWS Scanner API.

Uses reqScannerSubscription to scan the ENTIRE US market on IBKR's servers.
Results return in <1 second regardless of universe size. No data subscriptions needed.

This enables 2-second bot cycles scanning 6,000+ stocks.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from ib_insync import ScannerSubscription, TagValue

from ibkr_client import ibkr

log = logging.getLogger(__name__)

# Cache scanner results to avoid hammering IBKR
_scanner_cache: dict[str, dict] = {}
_CACHE_TTL = 5  # seconds — short TTL for fast cycles


# ── Pre-built Scan Templates ────────────────────────────────────────────────

SCAN_TEMPLATES = {
    "hot_us_stocks": {
        "instrument": "STK",
        "locationCode": "STK.US.MAJOR",
        "scanCode": "HOT_BY_VOLUME",
        "numberOfRows": 50,
        "abovePrice": 5.0,
        "belowPrice": 500.0,
        "aboveVolume": 500000,
    },
    "top_gainers": {
        "instrument": "STK",
        "locationCode": "STK.US.MAJOR",
        "scanCode": "TOP_PERC_GAIN",
        "numberOfRows": 50,
        "abovePrice": 5.0,
        "belowPrice": 500.0,
        "aboveVolume": 100000,
    },
    "top_losers": {
        "instrument": "STK",
        "locationCode": "STK.US.MAJOR",
        "scanCode": "TOP_PERC_LOSE",
        "numberOfRows": 50,
        "abovePrice": 5.0,
        "belowPrice": 500.0,
        "aboveVolume": 100000,
    },
    "most_active": {
        "instrument": "STK",
        "locationCode": "STK.US.MAJOR",
        "scanCode": "MOST_ACTIVE",
        "numberOfRows": 50,
        "abovePrice": 5.0,
        "belowPrice": 500.0,
    },
    "high_opt_volume": {
        "instrument": "STK",
        "locationCode": "STK.US.MAJOR",
        "scanCode": "HIGH_OPT_IMP_VOLAT",
        "numberOfRows": 30,
        "abovePrice": 10.0,
        "aboveVolume": 200000,
    },
    "gap_up": {
        "instrument": "STK",
        "locationCode": "STK.US.MAJOR",
        "scanCode": "HIGH_OPEN_GAP",
        "numberOfRows": 30,
        "abovePrice": 5.0,
        "aboveVolume": 100000,
    },
    "gap_down": {
        "instrument": "STK",
        "locationCode": "STK.US.MAJOR",
        "scanCode": "LOW_OPEN_GAP",
        "numberOfRows": 30,
        "abovePrice": 5.0,
        "aboveVolume": 100000,
    },
    "new_highs": {
        "instrument": "STK",
        "locationCode": "STK.US.MAJOR",
        "scanCode": "HIGH_VS_52W_HL",
        "numberOfRows": 30,
        "abovePrice": 5.0,
        "aboveVolume": 100000,
    },
}


# ── Scanner Execution ────────────────────────────────────────────────────────

async def run_scan(scan_name: str = "hot_us_stocks", max_results: int = 50) -> list[dict]:
    """
    Run an IBKR server-side scan. Returns list of {symbol, price, volume, change_pct, ...}.
    Results cached for _CACHE_TTL seconds.
    """
    import time

    # Check cache
    cached = _scanner_cache.get(scan_name)
    if cached and (time.time() - cached["ts"]) < _CACHE_TTL:
        return cached["results"]

    if not ibkr.is_connected():
        log.warning("IBKR not connected — scanner unavailable")
        return []

    template = SCAN_TEMPLATES.get(scan_name)
    if not template:
        log.warning("Unknown scan template: %s", scan_name)
        return []

    try:
        sub = ScannerSubscription(
            instrument=template["instrument"],
            locationCode=template["locationCode"],
            scanCode=template["scanCode"],
            numberOfRows=min(max_results, template.get("numberOfRows", 50)),
            abovePrice=template.get("abovePrice"),
            belowPrice=template.get("belowPrice"),
            aboveVolume=template.get("aboveVolume"),
        )

        scan_data = await ibkr.ib.reqScannerDataAsync(sub, [])

        results = []
        for item in scan_data:
            contract = item.contractDetails.contract
            results.append({
                "symbol": contract.symbol,
                "exchange": contract.primaryExchange or contract.exchange,
                "con_id": contract.conId,
                "rank": item.rank,
                "scan": scan_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        # Cache
        _scanner_cache[scan_name] = {"results": results, "ts": time.time()}

        log.info("IBKR scan '%s' returned %d results", scan_name, len(results))
        return results

    except Exception as e:
        log.error("IBKR scanner failed for '%s': %s", scan_name, e)
        return []


async def run_multi_scan(scan_names: list[str] | None = None) -> dict[str, list[dict]]:
    """Run multiple scans concurrently and return results by scan name."""
    if scan_names is None:
        scan_names = ["hot_us_stocks", "top_gainers", "gap_up"]

    tasks = {name: run_scan(name) for name in scan_names}
    results = {}
    for name, task in tasks.items():
        try:
            results[name] = await task
        except Exception as e:
            log.warning("Scan '%s' failed: %s", name, e)
            results[name] = []

    return results


async def get_scan_symbols(scan_names: list[str] | None = None) -> list[str]:
    """Run scans and return deduplicated list of symbols."""
    all_results = await run_multi_scan(scan_names)
    symbols = set()
    for results in all_results.values():
        for item in results:
            symbols.add(item["symbol"])
    return sorted(symbols)


# ── API Endpoint Data ────────────────────────────────────────────────────────

def get_available_scans() -> list[dict]:
    """Return available scan templates for the frontend."""
    return [
        {"id": name, "name": name.replace("_", " ").title(), "max_results": t.get("numberOfRows", 50)}
        for name, t in SCAN_TEMPLATES.items()
    ]
