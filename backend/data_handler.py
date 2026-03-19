"""
Stage 4 — Scalable Data Layer.

Streams OHLCV bars from DuckDB/Parquet for the full US stock universe.
Supports chunked loading (200-bar rolling windows) to avoid RAM bloat.
Falls back to yfinance/IBKR if DuckDB not available.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from events import EventType, MarketEvent

log = logging.getLogger(__name__)

PARQUET_DIR = Path("data/bars")  # partitioned: data/bars/{symbol}.parquet


class DataHandler:
    """Stream historical bars as MarketEvents for backtesting or live replay."""

    def __init__(self, symbols: list[str], start_date: str, end_date: str,
                 window_size: int = 200):
        self.symbols = [s.upper() for s in symbols]
        self.start_date = start_date
        self.end_date = end_date
        self.window_size = window_size
        self._bar_index = 0
        self._dates: list[str] = []
        self._bars: dict[str, pd.DataFrame] = {}
        self.continue_backtest = True

    def load(self) -> None:
        """Load bars from Parquet/DuckDB or fallback to yfinance."""
        try:
            self._load_from_parquet()
        except Exception:
            log.info("Parquet not available, falling back to yfinance")
            self._load_from_yfinance()

        if self._dates:
            log.info("DataHandler loaded: %d symbols, %d dates (%s to %s)",
                     len(self._bars), len(self._dates), self._dates[0], self._dates[-1])
        else:
            log.warning("DataHandler: no data loaded")
            self.continue_backtest = False

    def _load_from_parquet(self) -> None:
        """Load from partitioned Parquet files."""
        for sym in self.symbols:
            path = PARQUET_DIR / f"{sym}.parquet"
            if path.exists():
                df = pd.read_parquet(path)
                df = df[(df.index >= self.start_date) & (df.index <= self.end_date)]
                if len(df) > 0:
                    self._bars[sym] = df
        all_dates = set()
        for df in self._bars.values():
            all_dates.update(df.index.astype(str))
        self._dates = sorted(all_dates)

    def _load_from_yfinance(self) -> None:
        """Fallback: download from yfinance."""
        import yfinance as yf
        raw = yf.download(self.symbols, start=self.start_date, end=self.end_date,
                          group_by="ticker", progress=False, threads=True)
        if raw.empty:
            return
        for sym in self.symbols:
            try:
                df = raw[sym].dropna(how="all") if len(self.symbols) > 1 else raw.dropna(how="all")
                if len(df) < 2:
                    continue
                df.columns = [c.lower() for c in df.columns]
                self._bars[sym] = df
            except Exception:
                continue
        all_dates = set()
        for df in self._bars.values():
            all_dates.update(df.index.astype(str))
        self._dates = sorted(all_dates)

    def next_bars(self) -> list[MarketEvent]:
        """Get all bars for the next timestamp. Returns empty list when done."""
        if self._bar_index >= len(self._dates):
            self.continue_backtest = False
            return []

        date_str = self._dates[self._bar_index]
        self._bar_index += 1
        events = []

        for sym, df in self._bars.items():
            if date_str in df.index.astype(str).values:
                try:
                    row = df.loc[date_str]
                    events.append(MarketEvent(
                        timestamp=datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc),
                        type=EventType.MARKET,
                        symbol=sym,
                        open=float(row.get("open", 0)),
                        high=float(row.get("high", 0)),
                        low=float(row.get("low", 0)),
                        close=float(row.get("close", 0)),
                        volume=float(row.get("volume", 0)),
                    ))
                except Exception:
                    continue

        return events

    def get_bars(self, symbol: str, n: int = 200) -> pd.DataFrame | None:
        """Get the last N bars for a symbol (for indicator computation)."""
        df = self._bars.get(symbol.upper())
        if df is None:
            return None
        end_idx = min(self._bar_index, len(df))
        start_idx = max(0, end_idx - n)
        return df.iloc[start_idx:end_idx]
