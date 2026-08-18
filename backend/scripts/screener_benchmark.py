"""Offline screener benchmark using deterministic OHLCV fixtures.

This measures local filter/scoring work only.  It deliberately does not call
IBKR, yfinance, the network, or the production cache refresh path, so its
latency is not a claim about market-data acquisition or end-to-end scans.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from models import FilterValue, ScanFilter  # noqa: E402
from screener import compute_screener_snapshot, evaluate_symbol  # noqa: E402


def _fixture(symbol_index: int, bars: int, seed: int) -> pd.DataFrame:
    """Create stable OHLCV data without touching external services."""
    rng = np.random.default_rng(seed + symbol_index)
    close = 80.0 + symbol_index + np.cumsum(rng.normal(0.08, 1.0, bars))
    close = np.maximum(close, 1.0)
    high = close + rng.uniform(0.25, 1.5, bars)
    low = np.maximum(close - rng.uniform(0.25, 1.5, bars), 0.1)
    open_ = close + rng.normal(0.0, 0.35, bars)
    volume = rng.integers(500_000, 5_000_000, bars).astype(float)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


def _filters() -> list[ScanFilter]:
    return [
        ScanFilter(
            indicator="VOLUME",
            params={},
            operator="GT",
            value=FilterValue(type="number", number=100_000),
        ),
    ]


async def run_offline_benchmark(
    *,
    symbol_count: int = 20,
    bars: int = 260,
    concurrency: int = 3,
    repeats: int = 3,
    seed: int = 20260818,
) -> dict[str, Any]:
    """Run repeated local scans and return reproducible benchmark metadata."""
    if symbol_count < 1 or bars < 220 or concurrency < 1 or repeats < 1:
        raise ValueError("symbol_count, concurrency, repeats must be positive; bars must be >= 220")

    fixtures = {f"FIX{index:04d}": _fixture(index, bars, seed) for index in range(symbol_count)}
    filters = _filters()

    async def scan_one(symbol: str) -> tuple[str, Any, dict[str, Any]]:
        result = await asyncio.to_thread(evaluate_symbol, fixtures[symbol], filters)
        snapshot = await asyncio.to_thread(compute_screener_snapshot, fixtures[symbol])
        return symbol, result, snapshot

    durations_ms: list[float] = []
    counts: list[int] = []
    scores: list[float] = []
    sorted_results = True
    for _ in range(repeats):
        started = time.perf_counter()
        semaphore = asyncio.Semaphore(concurrency)

        async def limited(symbol: str) -> tuple[str, Any, dict[str, Any]]:
            async with semaphore:
                return await scan_one(symbol)

        rows = await asyncio.gather(*(limited(symbol) for symbol in fixtures))
        elapsed_ms = (time.perf_counter() - started) * 1000
        matched = [(symbol, snapshot) for symbol, result, snapshot in rows if result is not None]
        matched.sort(key=lambda item: (-float(item[1]["screener_score"]), item[0]))
        counts.append(len(matched))
        scores.extend(float(snapshot["screener_score"]) for _, snapshot in matched)
        durations_ms.append(round(elapsed_ms, 3))

        symbols = [symbol for symbol, _ in matched]
        if len(symbols) != len(set(symbols)):
            raise AssertionError("benchmark produced duplicate symbols")
        ordered_scores = [float(snapshot["screener_score"]) for _, snapshot in matched]
        if ordered_scores != sorted(ordered_scores, reverse=True):
            sorted_results = False
            raise AssertionError("benchmark results are not sorted by score")
        if any(not math.isfinite(score) for score in scores):
            raise AssertionError("benchmark produced a non-finite score")

    return {
        "benchmark": "offline_screener_fixture",
        "network_calls": 0,
        "symbol_count": symbol_count,
        "bars_per_symbol": bars,
        "concurrency": concurrency,
        "repeats": repeats,
        "seed": seed,
        "matched_count": counts[0],
        "matched_count_consistent": len(set(counts)) == 1,
        "durations_ms": durations_ms,
        "min_ms": min(durations_ms),
        "median_ms": float(np.median(durations_ms)),
        "max_ms": max(durations_ms),
        "result_integrity": {
            "unique_symbols": True,
            "finite_scores": True,
            "sorted_by_score": sorted_results,
            "counts_consistent": len(set(counts)) == 1,
        },
        "scope_note": "Local fixture evaluation only; excludes network and market-data acquisition latency.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=int, default=20)
    parser.add_argument("--bars", type=int, default=260)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260818)
    args = parser.parse_args()
    result = asyncio.run(run_offline_benchmark(
        symbol_count=args.symbols,
        bars=args.bars,
        concurrency=args.concurrency,
        repeats=args.repeats,
        seed=args.seed,
    ))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
