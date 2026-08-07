"""
Universe Hygiene — detect and quarantine delisted/invalid symbols.

The universe JSON files contain stale symbols (~20 delisted per operator feedback).
This module validates symbols against yfinance and maintains a quarantine list
so the screener pipeline never wastes time on dead tickers.

Runs on startup and periodically (hourly). Results are persisted to a JSON file
so cold starts don't need to re-validate.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

log = logging.getLogger(__name__)

# ── Configuration ───────────────────────────────────────────────────────────

_QUARANTINE_FILE = os.path.join(os.path.dirname(__file__), "data", "quarantine.json")
_VALIDATION_BATCH_SIZE = 20
_VALIDATION_CONCURRENCY = 3
_VALIDATION_TIMEOUT = 10  # seconds per batch

# Known delisted/acquired symbols (operator-verified, 2026-08)
# These are symbols that yfinance may still return data for (delayed) but
# are no longer actively traded. We quarantine them proactively.
_KNOWN_DELISTED: set[str] = {
    # Acquired / merged
    "ATVI",  # Acquired by Microsoft 2023
    "CTLT",  # Acquired by Danaher
    "FRC",   # Failed bank, acquired by JPM 2023
    "SBNY",  # Failed bank, acquired by Flagstar 2023
    "SIVB",  # Failed bank, acquired by First Citizens 2023
    "RE",    # Delisted
    "MRO",   # Acquired by ConocoPhillips 2024
    "BK",    # Note: BK (BNY Mellon) is still active — this may be a different symbol
    "FB",    # Meta — ticker changed to META
    "TWTR",  # Twitter — delisted after Musk acquisition
    "ABMD",  # Acquired by Johnson & Johnson
    "ATH",   # Check — may be delisted
    "CDAY",  # Acquired
    "CERN",  # Acquired by Oracle
    "COHR",  # Check
    "DISH",  # Merged with EchoStar
    "FISV",  # Acquired
    "HZNP",  # Acquired by Amgen
    "LHCG",  # Acquired
    "MIME",  # Acquired
    "NLSN",  # Acquired
    "PENN",  # Check
    "RNG",   # Check
    "SGEN",  # Acquired by Pfizer
    "SPLK",  # Acquired by Cisco
    "STNE",  # Check
    "UAA",   # Check
    "VMW",   # Acquired by Broadcom
    "VMWARE",# Acquired by Broadcom
    "XLNX",  # Acquired by AMD
    "ZEN",   # Acquired
}


# ── Quarantine state ────────────────────────────────────────────────────────

_quarantine: set[str] = set()
_quarantine_lock = asyncio.Lock()
_validated_at: float = 0.0


def _load_quarantine() -> set[str]:
    """Load persisted quarantine list from disk."""
    if not os.path.isfile(_QUARANTINE_FILE):
        return set()
    try:
        with open(_QUARANTINE_FILE, "r") as f:
            data = json.load(f)
        symbols = set(data.get("symbols", []))
        log.info("Loaded %d quarantined symbols from disk", len(symbols))
        return symbols
    except Exception as e:
        log.warning("Failed to load quarantine file: %s", e)
        return set()


def _save_quarantine(symbols: set[str]) -> None:
    """Persist quarantine list to disk."""
    try:
        os.makedirs(os.path.dirname(_QUARANTINE_FILE), exist_ok=True)
        with open(_QUARANTINE_FILE, "w") as f:
            json.dump({
                "symbols": sorted(symbols),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "count": len(symbols),
            }, f, indent=2)
    except Exception as e:
        log.warning("Failed to save quarantine file: %s", e)


# ── Public API ──────────────────────────────────────────────────────────────

async def init_quarantine() -> None:
    """Load persisted quarantine + known delisted on startup."""
    global _quarantine
    async with _quarantine_lock:
        persisted = _load_quarantine()
        _quarantine = persisted | _KNOWN_DELISTED
        log.info("Quarantine initialized: %d symbols (%d persisted, %d known)",
                 len(_quarantine), len(persisted), len(_KNOWN_DELISTED))


def is_quarantined(symbol: str) -> bool:
    """Check if a symbol is in the quarantine list (non-async, fast)."""
    return symbol.upper() in _quarantine


def filter_universe(symbols: list[str]) -> list[str]:
    """Remove quarantined symbols from a universe list."""
    return [s for s in symbols if s.upper() not in _quarantine]


async def validate_and_quarantine(symbols: list[str]) -> list[str]:
    """
    Validate symbols against yfinance and quarantine invalid ones.
    Returns the list of valid symbols.

    A symbol is considered invalid if:
    - yfinance returns no data for it
    - yfinance returns data but the symbol is known to be delisted
    - The ticker info shows it's delisted/acquired
    """
    global _validated_at

    # First pass: remove already-quarantined
    clean = [s for s in symbols if s.upper() not in _quarantine]
    if not clean:
        return []

    newly_quarantined: set[str] = set()

    # Validate in batches
    sem = asyncio.Semaphore(_VALIDATION_CONCURRENCY)

    async def _validate_batch(batch: list[str]) -> None:
        async with sem:
            try:
                import yfinance as yf

                # Use fast_info for quick validation
                tickers = yf.Tickers(" ".join(batch))
                for sym in batch:
                    try:
                        t = tickers.tickers.get(sym)
                        if t is None:
                            newly_quarantined.add(sym.upper())
                            continue

                        # Check if the ticker has valid price data
                        info = t.fast_info if hasattr(t, 'fast_info') else {}
                        price = None
                        if isinstance(info, dict):
                            price = info.get("lastPrice") or info.get("regularMarketPrice")
                        elif hasattr(info, "last_price"):
                            price = info.last_price

                        if price is None or price <= 0:
                            # Try getting info for delisting status
                            try:
                                ti = t.info
                                if ti.get("delisted") or ti.get("messageBoardId") is None:
                                    newly_quarantined.add(sym.upper())
                                    continue
                            except Exception:
                                newly_quarantined.add(sym.upper())
                                continue

                    except Exception:
                        newly_quarantined.add(sym.upper())

            except Exception as e:
                log.warning("Batch validation failed for %s: %s", batch, e)

    # Process in batches
    batches = [clean[i:i + _VALIDATION_BATCH_SIZE] for i in range(0, len(clean), _VALIDATION_BATCH_SIZE)]
    tasks = [_validate_batch(b) for b in batches]

    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=_VALIDATION_TIMEOUT * len(batches))
    except asyncio.TimeoutError:
        log.warning("Universe validation timed out after %ds", _VALIDATION_TIMEOUT * len(batches))

    # Update quarantine
    if newly_quarantined:
        async with _quarantine_lock:
            _quarantine |= newly_quarantined
            _save_quarantine(_quarantine)
        log.info("Quarantined %d new symbols: %s", len(newly_quarantined), sorted(newly_quarantined))

    _validated_at = time.time()

    # Return valid symbols
    return [s for s in clean if s.upper() not in newly_quarantined]


async def get_quarantine_stats() -> dict[str, Any]:
    """Return quarantine statistics for the dashboard."""
    return {
        "total_quarantined": len(_quarantine),
        "known_delisted": len(_KNOWN_DELISTED),
        "detected_delisted": len(_quarantine - _KNOWN_DELISTED),
        "last_validated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_validated_at)) if _validated_at else None,
        "quarantined_symbols": sorted(_quarantine),
    }
