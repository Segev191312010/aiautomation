"""
Unified Screener Pipeline — Phase 2 Real-Time Screener

Single canonical pipeline that:
1. Runs IBKR server-side scans as primary source (<1s, no data subscriptions)
2. Falls back to yfinance batch scan when IBKR is unavailable
3. Normalizes all results into ScreenerCandidate schema
4. Persists the latest snapshot for instant cold-load
5. Enriches top-N candidates with live IBKR quotes
6. Pushes updates via WebSocket

Architecture:
  Universe → Eligibility → Scan → Rank → Quote Enrichment → WS/UI

The screener is NOT a one-shot REST call anymore — it's a background service
that maintains a live snapshot and pushes deltas.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

from models import ScreenerCandidate, ScreenerSnapshot, ScreenerStatusResponse

log = logging.getLogger(__name__)

# ── Global state ────────────────────────────────────────────────────────────

_latest_snapshot: ScreenerSnapshot | None = None
_snapshot_lock = asyncio.Lock()
_ws_broadcast: Callable[[dict], Any] | None = None
_scan_task: asyncio.Task | None = None
_quote_task: asyncio.Task | None = None
_running = False

# Configuration
SCAN_INTERVAL_SECONDS = 60       # How often to refresh the full scan
QUOTE_PUSH_INTERVAL = 0.5        # How often to push quote updates
TOP_N_QUOTE_ENRICHMENT = 20      # How many top candidates get live quotes
SNAPSHOT_STALE_SECONDS = 120     # After this, snapshot is considered stale
UNIVERSE_CLEAN_INTERVAL = 3600   # Clean universe every hour


# ── Public API ───────────────────────────────────────────────────────────────

def set_broadcast(cb: Callable[[dict], Any]) -> None:
    """Wire the WebSocket broadcast function from main.py."""
    global _ws_broadcast
    _ws_broadcast = cb


async def get_latest_snapshot() -> ScreenerSnapshot | None:
    """Return the most recent scan snapshot (non-blocking)."""
    return _latest_snapshot


async def get_status() -> ScreenerStatusResponse:
    """Return live screener pipeline status for the dashboard."""
    snap = _latest_snapshot
    if snap is None:
        return ScreenerStatusResponse(
            connected=False,
            stale=True,
            errors=["No scan has been completed yet"],
        )

    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(snap.created_at)).total_seconds()
    except Exception:
        age = 999

    return ScreenerStatusResponse(
        connected=True,
        last_scan_at=snap.created_at,
        last_scan_source=snap.source,
        last_scan_duration_ms=snap.elapsed_ms,
        candidate_count=len(snap.candidates),
        top_symbols=[c.symbol for c in snap.candidates[:10]],
        data_age_seconds=round(age, 1),
        stale=age > SNAPSHOT_STALE_SECONDS,
        errors=snap.errors,
    )


async def start() -> None:
    """Start the background scan + quote enrichment loops."""
    global _running, _scan_task, _quote_task
    if _running:
        return
    _running = True
    log.info("Screener pipeline starting (scan every %ds)", SCAN_INTERVAL_SECONDS)

    # Initialize universe hygiene (load quarantine list)
    try:
        from universe_hygiene import init_quarantine
        await init_quarantine()
    except Exception as e:
        log.warning("Universe hygiene init failed: %s", e)

    # Run an initial scan immediately
    _scan_task = asyncio.create_task(_scan_loop(), name="screener-scan-loop")
    _quote_task = None


async def stop() -> None:
    """Stop the background loops."""
    global _running
    _running = False
    for task in (_scan_task, _quote_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    log.info("Screener pipeline stopped")


async def run_scan_now() -> ScreenerSnapshot:
    """Force an immediate scan (used by REST endpoint and initial load)."""
    return await _run_single_scan()


# ── Internal: Scan Loop ─────────────────────────────────────────────────────

async def _scan_loop() -> None:
    """Background loop that refreshes the scan on SCAN_INTERVAL_SECONDS."""
    while _running:
        try:
            await _run_single_scan()
        except Exception as e:
            log.error("Screener scan loop error: %s", e, exc_info=True)
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)


async def _run_single_scan() -> ScreenerSnapshot:
    """Execute one full scan cycle: IBKR primary → yfinance fallback."""
    global _latest_snapshot
    start_time = time.monotonic()
    errors: list[str] = []
    candidates: list[ScreenerCandidate] = []
    source: str = "mixed"

    # ── Step 1: IBKR server-side scan (primary) ─────────────────────────
    ibkr_candidates = await _run_ibkr_scan()
    if ibkr_candidates:
        candidates = ibkr_candidates
        source = "ibkr"
        log.info("IBKR scan returned %d candidates", len(candidates))
    else:
        errors.append("IBKR scan returned no results (disconnected or failed)")

    # ── Step 2: yfinance fallback (if IBKR returned nothing) ─────────────
    if not candidates:
        try:
            yf_candidates = await _run_yfinance_fallback()
            if yf_candidates:
                candidates = yf_candidates
                source = "yfinance"
                log.info("yfinance fallback returned %d candidates", len(candidates))
            else:
                errors.append("yfinance fallback also returned no results")
        except Exception as e:
            errors.append(f"yfinance fallback failed: {e}")

    # ── Step 3: Enrich with sector/market cap ────────────────────────────
    if candidates:
        try:
            await _enrich_candidates(candidates)
        except Exception as e:
            errors.append(f"Enrichment failed: {e}")

    # ── Step 4: Build and persist snapshot ───────────────────────────────
    elapsed = int((time.monotonic() - start_time) * 1000)
    stale_at = datetime.now(timezone.utc).timestamp() + SNAPSHOT_STALE_SECONDS

    snapshot = ScreenerSnapshot(
        source=source,
        scan_name="unified_pipeline",
        candidates=candidates,
        total_symbols=len(candidates),
        elapsed_ms=elapsed,
        stale_at=datetime.fromtimestamp(stale_at, tz=timezone.utc).isoformat(),
        errors=errors,
    )

    async with _snapshot_lock:
        _latest_snapshot = snapshot

    log.info("Screener scan complete: %d candidates, %dms, source=%s",
             len(candidates), elapsed, source)
    return snapshot


# ── Internal: IBKR Scan ─────────────────────────────────────────────────────

async def _run_ibkr_scan() -> list[ScreenerCandidate]:
    """Run IBKR multi-scan and normalize to ScreenerCandidate."""
    try:
        from ibkr_scanner import run_multi_scan
        from ibkr_client import ibkr

        if not ibkr.is_connected():
            log.debug("IBKR not connected, skipping IBKR scan")
            return []

        scan_names = ["hot_us_stocks", "top_gainers", "most_active", "gap_up"]
        results_by_scan = await run_multi_scan(scan_names)

        # Deduplicate by symbol, keep highest rank
        seen: dict[str, ScreenerCandidate] = {}
        for scan_name, items in results_by_scan.items():
            for item in items:
                sym = item["symbol"]
                if sym not in seen or item.get("rank", 999) < seen[sym].rank:
                    seen[sym] = ScreenerCandidate(
                        symbol=sym,
                        exchange=item.get("exchange", ""),
                        con_id=item.get("con_id", 0),
                        rank=item.get("rank", 0),
                        source="ibkr",
                    )

        return sorted(seen.values(), key=lambda c: c.rank)

    except ImportError:
        log.debug("ibkr_scanner module not available")
        return []
    except Exception as e:
        log.warning("IBKR scan failed: %s", e)
        return []


# ── Internal: yfinance Fallback ─────────────────────────────────────────────

async def _run_yfinance_fallback() -> list[ScreenerCandidate]:
    """Run a lightweight yfinance scan as fallback when IBKR is unavailable."""
    try:
        from screener import load_universe, run_scan
        from models import ScanRequest, ScanFilter, FilterValue
        from universe_hygiene import filter_universe

        # Load and clean the universe
        raw_symbols = load_universe("sp500")
        clean_symbols = filter_universe(raw_symbols)
        log.debug("yfinance fallback: %d symbols after quarantine filter (was %d)",
                  len(clean_symbols), len(raw_symbols))

        # Use a minimal scan: S&P 500, top movers by volume
        request = ScanRequest(
            universe="sp500",
            interval="1d",
            period="5d",
            limit=50,
            filters=[
                ScanFilter(
                    indicator="VOLUME",
                    params={},
                    operator="GT",
                    value=FilterValue(type="number", number=500000),
                ),
            ],
        )

        response = await run_scan(request)

        candidates = []
        for rank, row in enumerate(response.results, start=1):
            candidates.append(ScreenerCandidate(
                symbol=row.symbol,
                price=row.price,
                change_pct=row.change_pct,
                volume=row.volume,
                indicators=row.indicators,
                screener_score=row.screener_score,
                setup=row.setup,
                relative_volume=row.relative_volume,
                momentum_20d=row.momentum_20d,
                trend_strength=row.trend_strength,
                notes=row.notes,
                source="yfinance",
                rank=rank,
            ))

        return candidates

    except Exception as e:
        log.warning("yfinance fallback scan failed: %s", e)
        return []


# ── Internal: Enrichment ────────────────────────────────────────────────────

async def _enrich_candidates(candidates: list[ScreenerCandidate]) -> None:
    """Add sector and market cap data to candidates."""
    try:
        from screener import enrich_symbols
        from models import EnrichRequest

        symbols = [c.symbol for c in candidates[:100]]  # Enrich top 100
        if not symbols:
            return

        request = EnrichRequest(symbols=symbols)
        enriched = await enrich_symbols(request)

        enrich_map = {e.symbol: e for e in enriched}
        for c in candidates:
            e = enrich_map.get(c.symbol)
            if e:
                c.name = e.name
                c.sector = e.sector
                c.market_cap = e.market_cap

    except Exception as e:
        log.warning("Candidate enrichment failed: %s", e)


# ── Internal: Quote Push Loop ───────────────────────────────────────────────

async def _quote_push_loop() -> None:
    """Push live quote updates for top-N candidates via WebSocket."""
    while _running:
        try:
            await _push_quotes()
        except Exception as e:
            log.error("Quote push error: %s", e, exc_info=True)
        await asyncio.sleep(QUOTE_PUSH_INTERVAL)


async def _push_quotes() -> None:
    """Collect quotes for top candidates and broadcast."""
    # Screener snapshots are hydrated and refreshed through authenticated REST.
    # No user-owned WebSocket audience exists for this global pipeline.
    return

    snap = _latest_snapshot
    if not snap or not snap.candidates:
        return

    top = snap.candidates[:TOP_N_QUOTE_ENRICHMENT]
    quotes = []

    for c in top:
        try:
            price = await _get_quick_price(c.symbol)
            if price is not None:
                quotes.append({
                    "symbol": c.symbol,
                    "price": price,
                    "change_pct": c.change_pct,
                    "volume": c.volume,
                    "source": c.source,
                })
        except Exception:
            pass

    if quotes:
        await _ws_broadcast({
            "type": "screener_quotes",
            "data": {
                "quotes": quotes,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        })


async def _get_quick_price(symbol: str) -> float | None:
    """Get a quick price for a symbol (IBKR tick → yfinance fast_info)."""
    try:
        from market_data import get_latest_price
        return await get_latest_price(symbol)
    except Exception:
        pass

    # Ultra-fast fallback: yfinance fast_info
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        price = ticker.fast_info.get("lastPrice") or ticker.fast_info.get("regularMarketPrice")
        if price and price > 0:
            return float(price)
    except Exception:
        pass

    return None


# ── Internal: WebSocket Broadcast ───────────────────────────────────────────

async def _broadcast_scan_update(snapshot: ScreenerSnapshot) -> None:
    """Push scan results to WebSocket clients."""
    if _ws_broadcast is None:
        return

    try:
        await _ws_broadcast({
            "type": "screener_scan",
            "data": {
                "scan_id": snapshot.scan_id,
                "source": snapshot.source,
                "candidate_count": len(snapshot.candidates),
                "elapsed_ms": snapshot.elapsed_ms,
                "top_candidates": [
                    {
                        "symbol": c.symbol,
                        "price": c.price,
                        "change_pct": c.change_pct,
                        "volume": c.volume,
                        "rank": c.rank,
                        "source": c.source,
                        "sector": c.sector,
                        "setup": c.setup,
                    }
                    for c in snapshot.candidates[:TOP_N_QUOTE_ENRICHMENT]
                ],
                "timestamp": snapshot.created_at,
                "stale_at": snapshot.stale_at,
                "errors": snapshot.errors,
            },
        })
    except Exception as e:
        log.error("Failed to broadcast scan update: %s", e)
