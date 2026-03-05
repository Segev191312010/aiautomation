"""
External data source adapters for diagnostics.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import math
import urllib.parse
import urllib.request
from typing import Any
import xml.etree.ElementTree as ET

import pandas as pd


def _headers() -> dict[str, str]:
    return {"User-Agent": "Mozilla/5.0 trading-dashboard/diagnostics"}


def _to_unix(ts: Any) -> int:
    if isinstance(ts, (int, float)):
        return int(ts)
    if isinstance(ts, datetime):
        return int(ts.timestamp())
    return int(datetime.now(timezone.utc).timestamp())


async def yahoo_history(symbol: str, period: str = "1y", interval: str = "1d", prepost: bool = False) -> pd.DataFrame:
    import yfinance as yf

    def _fetch() -> pd.DataFrame:
        df = yf.Ticker(symbol).history(period=period, interval=interval, prepost=prepost)
        if df is None or df.empty:
            return pd.DataFrame()
        out = pd.DataFrame(
            {
                "time": df.index,
                "open": df.get("Open"),
                "high": df.get("High"),
                "low": df.get("Low"),
                "close": df.get("Close"),
                "volume": df.get("Volume", 0),
            }
        )
        out = out.dropna(subset=["close"])
        if out.empty:
            return pd.DataFrame()
        out["time"] = pd.to_datetime(out["time"], utc=True)
        out = out.sort_values("time").reset_index(drop=True)
        return out

    return await asyncio.to_thread(_fetch)


async def yahoo_fast_info(symbol: str) -> dict[str, Any]:
    import yfinance as yf

    def _fetch() -> dict[str, Any]:
        fi = yf.Ticker(symbol).fast_info
        return {
            "last_price": getattr(fi, "last_price", None),
            "regular_market_price": getattr(fi, "regular_market_price", None),
            "post_market_price": getattr(fi, "post_market_price", None),
            "pre_market_price": getattr(fi, "pre_market_price", None),
            "market_state": getattr(fi, "market_state", None),
            "timestamp": int(datetime.now(timezone.utc).timestamp()),
        }

    return await asyncio.to_thread(_fetch)


async def yahoo_quote_price(symbol: str) -> tuple[float | None, int | None]:
    info = await yahoo_fast_info(symbol)
    for key in ("last_price", "regular_market_price", "post_market_price", "pre_market_price"):
        raw = info.get(key)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            return (value, int(info.get("timestamp") or datetime.now(timezone.utc).timestamp()))
    return (None, None)


async def yahoo_recommendation_mean(symbols: list[str]) -> list[float]:
    import yfinance as yf

    def _fetch() -> list[float]:
        values: list[float] = []
        for sym in symbols:
            try:
                info = yf.Ticker(sym).info
                raw = info.get("recommendationMean")
                if raw is None:
                    continue
                val = float(raw)
                if math.isfinite(val):
                    values.append(val)
            except Exception:
                continue
        return values

    return await asyncio.to_thread(_fetch)


async def fred_series(series_id: str) -> list[tuple[int, float]]:
    """
    Fetch FRED series using public CSV endpoint.
    Returns [(unix_ts, value)] ordered ascending.
    """

    def _fetch() -> list[tuple[int, float]]:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={urllib.parse.quote(series_id)}"
        req = urllib.request.Request(url, headers=_headers())
        with urllib.request.urlopen(req, timeout=12) as resp:  # nosec B310
            raw = resp.read().decode("utf-8", errors="ignore")
        rows: list[tuple[int, float]] = []
        for line in raw.splitlines()[1:]:
            parts = line.split(",")
            if len(parts) < 2:
                continue
            dt_raw = parts[0].strip()
            val_raw = parts[1].strip()
            if not dt_raw or not val_raw or val_raw == ".":
                continue
            try:
                dt = datetime.fromisoformat(dt_raw).replace(tzinfo=timezone.utc)
                value = float(val_raw)
            except Exception:
                continue
            rows.append((int(dt.timestamp()), value))
        rows.sort(key=lambda x: x[0])
        return rows

    return await asyncio.to_thread(_fetch)


async def yahoo_news_rss() -> list[dict[str, Any]]:
    urls = [
        "https://finance.yahoo.com/news/rssindex",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY,QQQ,DIA&region=US&lang=en-US",
    ]

    def _fetch_one(url: str) -> list[dict[str, Any]]:
        req = urllib.request.Request(url, headers=_headers())
        with urllib.request.urlopen(req, timeout=12) as resp:  # nosec B310
            xml_raw = resp.read().decode("utf-8", errors="ignore")
        root = ET.fromstring(xml_raw)
        out: list[dict[str, Any]] = []
        for item in root.findall("./channel/item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            if not title or not link:
                continue
            published_ts = int(datetime.now(timezone.utc).timestamp())
            if pub_date:
                for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT"):
                    try:
                        published_ts = int(datetime.strptime(pub_date, fmt).timestamp())
                        break
                    except Exception:
                        continue
            out.append(
                {
                    "source": "yahoo_rss",
                    "headline": title,
                    "url": link,
                    "published_at": published_ts,
                }
            )
        return out

    collected: list[dict[str, Any]] = []
    for url in urls:
        try:
            collected.extend(await asyncio.to_thread(_fetch_one, url))
        except Exception:
            continue

    dedup: dict[str, dict[str, Any]] = {}
    for item in collected:
        url = item.get("url")
        if not isinstance(url, str) or not url:
            continue
        prev = dedup.get(url)
        if prev is None or int(item.get("published_at", 0)) > int(prev.get("published_at", 0)):
            dedup[url] = item

    out = list(dedup.values())
    out.sort(key=lambda x: int(x.get("published_at", 0)), reverse=True)
    return out


async def yahoo_market_map_rows(days: int = 5) -> list[dict[str, Any]]:
    sectors = ["XLB", "XLC", "XLE", "XLF", "XLV", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLY"]

    async def _one(symbol: str) -> dict[str, Any] | None:
        df = await yahoo_history(symbol, period="3mo", interval="1d", prepost=False)
        if df.empty:
            return None
        close = df["close"].dropna()
        volume = df["volume"].fillna(0)
        if len(close) < max(21, days + 1):
            return None
        current = float(close.iloc[-1])
        prev = float(close.iloc[-(days + 1)])
        pct = ((current / prev) - 1.0) * 100.0 if prev > 0 else 0.0
        latest_vol = float(volume.iloc[-1])
        avg20 = float(volume.iloc[-20:].mean()) if len(volume) >= 20 else float(volume.mean())
        rel = (latest_vol / avg20) if avg20 > 0 else 0.0
        return {
            "symbol": symbol,
            "pct_change": round(pct, 4),
            "rel_volume": round(rel, 4),
            "price": round(current, 4),
            "as_of_ts": _to_unix(df["time"].iloc[-1]),
        }

    rows: list[dict[str, Any]] = []
    for symbol in sectors:
        item = await _one(symbol)
        if item is not None:
            rows.append(item)
    return rows
