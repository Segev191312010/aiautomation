"""
Universe expansion and liquidity pre-screening.

Extracted from bot_runner.py. Expands universe identifiers (sp500, nasdaq100, etc.)
to symbol lists and filters large universes down to liquid, in-range stocks.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any

from bot_health import record_degraded_event
from screener import load_universe

log = logging.getLogger(__name__)

# ── Liquidity pre-screen config ──────────────────────────────────────────────
_PRESCREEN_MIN_PRICE = 5.0
_PRESCREEN_MIN_VOLUME = 500_000
_PRESCREEN_TTL = 86_400  # refresh every 24 h
_prescreen_cache: dict[str, tuple[list[str], float]] = {}


def _prescreen_cache_key(candidates: list[str]) -> str:
    normalized = ",".join(sorted(sym.upper() for sym in candidates))
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def expand_universe(universe_id: str) -> list[str]:
    """Expand a universe identifier to a list of symbols."""
    if universe_id == "all":
        symbols: set[str] = set()
        for uid in ("sp500", "nasdaq100", "etfs"):
            symbols.update(load_universe(uid))
        return sorted(symbols)
    # us_all and any other named universe load directly from their JSON file
    return load_universe(universe_id)


async def prescreen_universe(candidates: list[str]) -> list[str]:
    """
    Filter a large symbol list down to liquid, in-range stocks.

    Downloads 5 days of daily bars for all candidates in one batch call,
    then keeps only symbols where:
      - last close  > _PRESCREEN_MIN_PRICE  (default $5  -- filters penny stocks)
      - avg volume  > _PRESCREEN_MIN_VOLUME (default 500k -- filters illiquid names)

    Results are cached for _PRESCREEN_TTL seconds (24 h by default),
    keyed by the hash of the sorted candidate set.
    """
    now = time.time()
    cache_key = _prescreen_cache_key(candidates)
    cached = _prescreen_cache.get(cache_key)
    if cached and (now - cached[1]) < _PRESCREEN_TTL:
        log.info("Pre-screen cache hit for %d candidates -> %d liquid symbols", len(candidates), len(cached[0]))
        return cached[0]

    log.info(
        "Pre-screening %d symbols (price>$%.0f, vol>%s) ...",
        len(candidates),
        _PRESCREEN_MIN_PRICE,
        f"{_PRESCREEN_MIN_VOLUME:,}",
    )

    try:
        import pandas as pd
        import yfinance as yf

        liquid: list[str] = []
        batch_size = 1000
        loop = asyncio.get_running_loop()

        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]
            try:
                raw = await loop.run_in_executor(
                    None,
                    lambda b=batch: yf.download(
                        b,
                        period="5d",
                        interval="1d",
                        auto_adjust=True,
                        progress=False,
                        group_by="ticker",
                        threads=True,
                    ),
                )
                if raw.empty:
                    continue

                if isinstance(raw.columns, pd.MultiIndex):
                    for sym in batch:
                        try:
                            sym_df = raw[sym].dropna(how="all")
                            if sym_df.empty:
                                continue
                            last_close = float(sym_df["Close"].iloc[-1])
                            avg_volume = float(sym_df["Volume"].mean())
                            if last_close >= _PRESCREEN_MIN_PRICE and avg_volume >= _PRESCREEN_MIN_VOLUME:
                                liquid.append(sym.upper())
                        except Exception as exc:
                            log.debug("Pre-screen: malformed bars for %s: %s", sym, exc)
                            record_degraded_event()
                else:
                    sym = batch[0]
                    try:
                        last_close = float(raw["Close"].iloc[-1])
                        avg_volume = float(raw["Volume"].mean())
                        if last_close >= _PRESCREEN_MIN_PRICE and avg_volume >= _PRESCREEN_MIN_VOLUME:
                            liquid.append(sym.upper())
                    except Exception as exc:
                        log.debug("Pre-screen %s skipped (malformed bars): %s", sym, exc)

                log.info(
                    "Pre-screen batch %d/%d done -> %d liquid so far",
                    i // batch_size + 1,
                    -(-len(candidates) // batch_size),
                    len(liquid),
                )
            except Exception as exc:
                log.warning("Pre-screen batch %d failed: %s", i // batch_size, exc)

        screened_symbols = sorted(set(liquid))
        _prescreen_cache[cache_key] = (screened_symbols, now)
        log.info(
            "Pre-screen complete: %d / %d symbols pass liquidity filter",
            len(screened_symbols),
            len(candidates),
        )
        return screened_symbols
    except Exception as exc:
        log.error("Pre-screen failed, falling back to full list: %s", exc)
        record_degraded_event()
        return candidates
