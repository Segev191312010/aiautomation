"""
Stock profile service — aggregates yfinance data into per-symbol modules.

Each module is cached independently with a 15-minute TTL.
All yfinance calls are offloaded to a thread executor via asyncio.to_thread().
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# ── Cache ────────────────────────────────────────────────────────────────────

CACHE_TTL = 900        # 15 minutes
MAX_CACHE_ENTRIES = 500


@dataclass
class _CacheEntry:
    data: dict[str, Any]
    fetched_at: float


_cache: dict[tuple[str, str], _CacheEntry] = {}


def _get_cached(symbol: str, module: str) -> dict[str, Any] | None:
    key = (symbol.upper(), module)
    entry = _cache.get(key)
    if entry is None:
        return None
    if time.time() - entry.fetched_at > CACHE_TTL:
        del _cache[key]
        return None
    return entry.data


def _put_cache(symbol: str, module: str, data: dict[str, Any]) -> None:
    # LRU eviction
    if len(_cache) >= MAX_CACHE_ENTRIES:
        oldest_key = min(_cache, key=lambda k: _cache[k].fetched_at)
        del _cache[oldest_key]
    _cache[(symbol.upper(), module)] = _CacheEntry(data=data, fetched_at=time.time())


# ── Helpers ──────────────────────────────────────────────────────────────────

def _safe(info: dict, key: str, cast: type = float) -> Any:
    """Safely extract a value from yfinance .info dict."""
    v = info.get(key)
    if v is None:
        return None
    try:
        result = cast(v)
        if cast is float and (math.isnan(result) or math.isinf(result)):
            return None
        return result
    except (TypeError, ValueError):
        return None


def _now_ts() -> float:
    return time.time()


# ── Fetchers (run in thread) ────────────────────────────────────────────────

def _fetch_info(symbol: str) -> dict[str, Any]:
    import yfinance as yf
    try:
        return yf.Ticker(symbol).info or {}
    except Exception as exc:
        log.warning("yfinance .info failed for %s: %s", symbol, exc)
        return {}


def _fetch_quarterly_financials(symbol: str) -> list[dict]:
    import yfinance as yf
    try:
        df = yf.Ticker(symbol).quarterly_financials
        if df is None or df.empty:
            return []
        rows = []
        for col in df.columns[:8]:  # last 8 quarters max
            period = col.strftime("%Y-Q%q") if hasattr(col, "strftime") else str(col)
            rev = df.loc["Total Revenue", col] if "Total Revenue" in df.index else None
            ni = df.loc["Net Income", col] if "Net Income" in df.index else None
            rows.append({
                "period": str(period)[:10],
                "revenue": float(rev) if rev is not None and not math.isnan(float(rev)) else None,
                "net_income": float(ni) if ni is not None and not math.isnan(float(ni)) else None,
            })
        return rows
    except Exception as exc:
        log.warning("yfinance quarterly_financials failed for %s: %s", symbol, exc)
        return []


def _fetch_institutional_holders(symbol: str) -> list[dict]:
    import yfinance as yf
    try:
        df = yf.Ticker(symbol).institutional_holders
        if df is None or df.empty:
            return []
        holders = []
        for _, row in df.head(10).iterrows():
            holders.append({
                "name": str(row.get("Holder", "")),
                "shares": int(row["Shares"]) if "Shares" in row and row["Shares"] else 0,
                "pct": float(row["% Out"]) if "% Out" in row and row["% Out"] else 0.0,
            })
        return holders
    except Exception as exc:
        log.warning("yfinance institutional_holders failed for %s: %s", symbol, exc)
        return []


def _fetch_financial_statements(symbol: str) -> dict[str, Any]:
    """Fetch income statement, balance sheet, cash flow (annual + quarterly)."""
    import yfinance as yf
    ticker = yf.Ticker(symbol)

    def _df_to_table(df) -> dict:
        if df is None or df.empty:
            return {"periods": [], "items": []}
        periods = [col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col) for col in df.columns[:5]]
        items = []
        for row_label in df.index:
            vals = []
            for col in df.columns[:5]:
                v = df.loc[row_label, col]
                try:
                    fv = float(v)
                    vals.append(None if (math.isnan(fv) or math.isinf(fv)) else fv)
                except (TypeError, ValueError):
                    vals.append(None)
            if any(v is not None for v in vals):
                items.append({"label": str(row_label), "values": vals})
        return {"periods": periods, "items": items}

    try:
        result = {
            "income_statement": {
                "annual": _df_to_table(ticker.financials),
                "quarterly": _df_to_table(ticker.quarterly_financials),
            },
            "balance_sheet": {
                "annual": _df_to_table(ticker.balance_sheet),
                "quarterly": _df_to_table(ticker.quarterly_balance_sheet),
            },
            "cash_flow": {
                "annual": _df_to_table(ticker.cashflow),
                "quarterly": _df_to_table(ticker.quarterly_cashflow),
            },
        }
        return result
    except Exception as exc:
        log.warning("yfinance financial statements failed for %s: %s", symbol, exc)
        return {
            "income_statement": {"annual": {"periods": [], "items": []}, "quarterly": {"periods": [], "items": []}},
            "balance_sheet": {"annual": {"periods": [], "items": []}, "quarterly": {"periods": [], "items": []}},
            "cash_flow": {"annual": {"periods": [], "items": []}, "quarterly": {"periods": [], "items": []}},
        }


def _fetch_upgrades_downgrades(symbol: str) -> list[dict]:
    """Fetch individual analyst firm upgrades/downgrades."""
    import yfinance as yf
    try:
        df = yf.Ticker(symbol).upgrades_downgrades
        if df is None or df.empty:
            return []
        rows = []
        for idx, row in df.head(20).iterrows():
            date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
            rows.append({
                "date": date_str,
                "firm": str(row.get("Firm", "")),
                "to_grade": str(row.get("ToGrade", "")),
                "from_grade": str(row.get("FromGrade", "")),
                "action": str(row.get("Action", "")),
            })
        return rows
    except Exception as exc:
        log.warning("yfinance upgrades_downgrades failed for %s: %s", symbol, exc)
        return []


def _fetch_recommendation_trend(symbol: str) -> list[dict]:
    """Fetch monthly recommendation trend (strongBuy/buy/hold/sell/strongSell)."""
    import yfinance as yf
    try:
        df = yf.Ticker(symbol).recommendations
        if df is None or df.empty:
            return []
        rows = []
        for _, row in df.tail(12).iterrows():
            rows.append({
                "period": str(row.get("period", "")),
                "strong_buy": int(row.get("strongBuy", 0)),
                "buy": int(row.get("buy", 0)),
                "hold": int(row.get("hold", 0)),
                "sell": int(row.get("sell", 0)),
                "strong_sell": int(row.get("strongSell", 0)),
            })
        return rows
    except Exception as exc:
        log.warning("yfinance recommendations failed for %s: %s", symbol, exc)
        return []


def _fetch_earnings_dates(symbol: str) -> str | None:
    import yfinance as yf
    try:
        df = yf.Ticker(symbol).earnings_dates
        if df is None or df.empty:
            return None
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        future = df.index[df.index >= now]
        if len(future) > 0:
            return future[0].strftime("%Y-%m-%d")
        return df.index[0].strftime("%Y-%m-%d") if len(df.index) > 0 else None
    except Exception as exc:
        log.warning("yfinance earnings_dates failed for %s: %s", symbol, exc)
        return None


def _fetch_earnings_with_estimate(symbol: str) -> dict[str, Any]:
    """Fetch next earnings date + revenue/EPS estimates."""
    import yfinance as yf
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.earnings_dates
        result: dict[str, Any] = {"next_date": None, "day_of_week": None,
                                   "eps_estimate": None, "revenue_estimate": None}
        if df is not None and not df.empty:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            future = df.index[df.index >= now]
            if len(future) > 0:
                dt = future[0]
                result["next_date"] = dt.strftime("%Y-%m-%d")
                result["day_of_week"] = dt.strftime("%A")
                # earnings_dates df may have 'EPS Estimate' column
                if "EPS Estimate" in df.columns:
                    v = df.loc[dt, "EPS Estimate"]
                    try:
                        fv = float(v)
                        if not math.isnan(fv):
                            result["eps_estimate"] = round(fv, 2)
                    except (TypeError, ValueError):
                        pass
                if "Revenue Estimate" in df.columns:
                    v = df.loc[dt, "Revenue Estimate"]
                    try:
                        fv = float(v)
                        if not math.isnan(fv):
                            result["revenue_estimate"] = fv
                    except (TypeError, ValueError):
                        pass
        # Also try analyst info for revenue estimate
        if result["revenue_estimate"] is None:
            info = ticker.info or {}
            rev_est = info.get("revenueEstimate")
            if isinstance(rev_est, dict):
                avg = rev_est.get("avg")
                if avg is not None:
                    try:
                        result["revenue_estimate"] = float(avg)
                    except (TypeError, ValueError):
                        pass
        return result
    except Exception as exc:
        log.warning("yfinance earnings_with_estimate failed for %s: %s", symbol, exc)
        return {"next_date": None, "day_of_week": None, "eps_estimate": None, "revenue_estimate": None}


def _fetch_stock_splits(symbol: str) -> list[dict]:
    """Fetch historical stock splits."""
    import yfinance as yf
    try:
        splits = yf.Ticker(symbol).splits
        if splits is None or splits.empty:
            return []
        rows = []
        for date, ratio in splits.items():
            date_str = date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else str(date)[:10]
            try:
                r = float(ratio)
                if r > 1:
                    ratio_str = f"{int(r)}:1"
                    split_type = "Forward"
                elif r > 0:
                    ratio_str = f"1:{int(1/r)}"
                    split_type = "Reverse"
                else:
                    continue
            except (TypeError, ValueError):
                continue
            rows.append({"date": date_str, "type": split_type, "ratio": ratio_str})
        rows.reverse()  # most recent first
        return rows
    except Exception as exc:
        log.warning("yfinance splits failed for %s: %s", symbol, exc)
        return []


# ── Service class ────────────────────────────────────────────────────────────

class StockProfileService:

    async def _info(self, symbol: str) -> dict[str, Any]:
        """Get cached or fresh yfinance .info dict."""
        cached = _get_cached(symbol, "_info")
        if cached is not None:
            return cached
        info = await asyncio.to_thread(_fetch_info, symbol)
        _put_cache(symbol, "_info", info)
        return info

    async def get_overview(self, symbol: str) -> dict:
        cached = _get_cached(symbol, "overview")
        if cached is not None:
            return cached

        info = await self._info(symbol)
        prev_close = _safe(info, "previousClose")
        price = _safe(info, "currentPrice") or _safe(info, "regularMarketPrice")
        change = (price - prev_close) if price and prev_close else None
        change_pct = (change / prev_close * 100) if change and prev_close else None

        data = {
            "symbol": symbol.upper(),
            "name": info.get("shortName") or info.get("longName") or symbol.upper(),
            "exchange": info.get("exchange"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "description": info.get("longBusinessSummary"),
            "employees": _safe(info, "fullTimeEmployees", int),
            "website": info.get("website"),
            "price": price,
            "change": round(change, 2) if change is not None else None,
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "fetched_at": _now_ts(),
        }
        _put_cache(symbol, "overview", data)
        return data

    async def get_key_stats(self, symbol: str) -> dict:
        cached = _get_cached(symbol, "key_stats")
        if cached is not None:
            return cached

        info = await self._info(symbol)
        data = {
            "market_cap": _safe(info, "marketCap"),
            "fifty_two_week_high": _safe(info, "fiftyTwoWeekHigh"),
            "fifty_two_week_low": _safe(info, "fiftyTwoWeekLow"),
            "trailing_pe": _safe(info, "trailingPE"),
            "forward_pe": _safe(info, "forwardPE"),
            "trailing_eps": _safe(info, "trailingEps"),
            "forward_eps": _safe(info, "forwardEps"),
            "volume": _safe(info, "volume", int),
            "avg_volume": _safe(info, "averageVolume", int),
            "dividend_yield": _safe(info, "dividendYield"),
            "beta": _safe(info, "beta"),
            "fifty_day_ma": _safe(info, "fiftyDayAverage"),
            "two_hundred_day_ma": _safe(info, "twoHundredDayAverage"),
            "fetched_at": _now_ts(),
        }
        _put_cache(symbol, "key_stats", data)
        return data

    async def get_financials(self, symbol: str) -> dict:
        cached = _get_cached(symbol, "financials")
        if cached is not None:
            return cached

        info = await self._info(symbol)
        quarterly = await asyncio.to_thread(_fetch_quarterly_financials, symbol)

        data = {
            "total_revenue": _safe(info, "totalRevenue"),
            "revenue_growth": _safe(info, "revenueGrowth"),
            "net_income": _safe(info, "netIncomeToCommon"),
            "operating_margins": _safe(info, "operatingMargins"),
            "gross_margins": _safe(info, "grossMargins"),
            "profit_margins": _safe(info, "profitMargins"),
            "debt_to_equity": _safe(info, "debtToEquity"),
            "current_ratio": _safe(info, "currentRatio"),
            "quarterly_revenue": [
                {"period": q["period"], "value": q["revenue"]}
                for q in quarterly if q.get("revenue") is not None
            ] or None,
            "quarterly_net_income": [
                {"period": q["period"], "value": q["net_income"]}
                for q in quarterly if q.get("net_income") is not None
            ] or None,
            "fetched_at": _now_ts(),
        }
        _put_cache(symbol, "financials", data)
        return data

    async def get_analyst(self, symbol: str) -> dict:
        cached = _get_cached(symbol, "analyst")
        if cached is not None:
            return cached

        info = await self._info(symbol)
        data = {
            "recommendation_mean": _safe(info, "recommendationMean"),
            "recommendation_key": info.get("recommendationKey"),
            "target_mean_price": _safe(info, "targetMeanPrice"),
            "target_high_price": _safe(info, "targetHighPrice"),
            "target_low_price": _safe(info, "targetLowPrice"),
            "target_median_price": _safe(info, "targetMedianPrice"),
            "num_analyst_opinions": _safe(info, "numberOfAnalystOpinions", int),
            "fetched_at": _now_ts(),
        }
        _put_cache(symbol, "analyst", data)
        return data

    async def get_ownership(self, symbol: str) -> dict:
        cached = _get_cached(symbol, "ownership")
        if cached is not None:
            return cached

        info = await self._info(symbol)
        holders = await asyncio.to_thread(_fetch_institutional_holders, symbol)

        data = {
            "held_pct_institutions": _safe(info, "heldPercentInstitutions"),
            "held_pct_insiders": _safe(info, "heldPercentInsiders"),
            "top_holders": holders or None,
            "fetched_at": _now_ts(),
        }
        _put_cache(symbol, "ownership", data)
        return data

    async def get_events(self, symbol: str) -> dict:
        cached = _get_cached(symbol, "events")
        if cached is not None:
            return cached

        info = await self._info(symbol)
        next_earnings = await asyncio.to_thread(_fetch_earnings_dates, symbol)

        ex_div_raw = info.get("exDividendDate")
        ex_div = None
        if ex_div_raw:
            from datetime import datetime, timezone
            try:
                if isinstance(ex_div_raw, (int, float)):
                    ex_div = datetime.fromtimestamp(ex_div_raw, tz=timezone.utc).strftime("%Y-%m-%d")
                else:
                    ex_div = str(ex_div_raw)[:10]
            except Exception:
                pass

        data = {
            "next_earnings_date": next_earnings,
            "ex_dividend_date": ex_div,
            "fetched_at": _now_ts(),
        }
        _put_cache(symbol, "events", data)
        return data

    async def get_narrative(self, symbol: str) -> dict:
        cached = _get_cached(symbol, "narrative")
        if cached is not None:
            return cached

        info = await self._info(symbol)
        strengths: list[str] = []
        risks: list[str] = []

        # Margins
        op_margin = _safe(info, "operatingMargins")
        if op_margin is not None:
            if op_margin > 0.20:
                strengths.append(f"Strong operating margins ({op_margin:.0%})")
            elif op_margin < 0.05:
                risks.append(f"Thin operating margins ({op_margin:.0%})")

        profit_margin = _safe(info, "profitMargins")
        if profit_margin is not None and profit_margin > 0.15:
            strengths.append(f"Healthy profit margins ({profit_margin:.0%})")

        # Growth
        rev_growth = _safe(info, "revenueGrowth")
        if rev_growth is not None:
            if rev_growth > 0.10:
                strengths.append(f"Revenue growing {rev_growth:.0%} YoY")
            elif rev_growth < -0.05:
                risks.append(f"Revenue declining {rev_growth:.0%} YoY")

        # Debt
        dte = _safe(info, "debtToEquity")
        if dte is not None:
            if dte > 150:
                risks.append(f"High debt-to-equity ratio ({dte:.0f})")
            elif dte < 50:
                strengths.append(f"Low leverage (D/E {dte:.0f})")

        # Analyst
        rec = _safe(info, "recommendationMean")
        if rec is not None:
            if rec <= 2.0:
                strengths.append("Strong analyst buy consensus")
            elif rec >= 3.5:
                risks.append("Weak analyst sentiment (hold/sell)")

        # Valuation
        pe = _safe(info, "trailingPE")
        if pe is not None:
            if pe > 50:
                risks.append(f"Premium valuation (P/E {pe:.0f})")
            elif pe < 15 and pe > 0:
                strengths.append(f"Attractive valuation (P/E {pe:.0f})")

        # Dividend
        div_yield = _safe(info, "dividendYield")
        if div_yield is not None and div_yield > 0.02:
            strengths.append(f"Pays {div_yield:.1%} dividend yield")

        # Beta
        beta = _safe(info, "beta")
        if beta is not None and beta > 1.5:
            risks.append(f"High volatility (beta {beta:.2f})")

        # Outlook
        outlook_parts = []
        if rec is not None:
            if rec <= 2.0:
                outlook_parts.append("Analysts are bullish")
            elif rec <= 3.0:
                outlook_parts.append("Analyst consensus is neutral")
            else:
                outlook_parts.append("Analysts lean bearish")
        if rev_growth is not None and rev_growth > 0:
            outlook_parts.append("with positive revenue momentum")
        elif rev_growth is not None:
            outlook_parts.append("amid declining revenues")
        outlook = ". ".join(outlook_parts) + "." if outlook_parts else "Insufficient data for outlook."

        data = {
            "strengths": strengths or ["No notable strengths identified"],
            "risks": risks or ["No notable risks identified"],
            "outlook": outlook,
            "fetched_at": _now_ts(),
        }
        _put_cache(symbol, "narrative", data)
        return data

    async def get_financial_statements(self, symbol: str) -> dict:
        cached = _get_cached(symbol, "financial_statements")
        if cached is not None:
            return cached

        raw = await asyncio.to_thread(_fetch_financial_statements, symbol)
        data = {**raw, "fetched_at": _now_ts()}
        _put_cache(symbol, "financial_statements", data)
        return data

    async def get_analyst_detail(self, symbol: str) -> dict:
        cached = _get_cached(symbol, "analyst_detail")
        if cached is not None:
            return cached

        upgrades, trend = await asyncio.gather(
            asyncio.to_thread(_fetch_upgrades_downgrades, symbol),
            asyncio.to_thread(_fetch_recommendation_trend, symbol),
        )

        data = {
            "upgrades_downgrades": upgrades or None,
            "recommendation_trend": trend or None,
            "fetched_at": _now_ts(),
        }
        _put_cache(symbol, "analyst_detail", data)
        return data

    async def get_rating_scorecard(self, symbol: str) -> dict:
        """Compute a letter-grade scorecard from financial metrics."""
        cached = _get_cached(symbol, "rating_scorecard")
        if cached is not None:
            return cached

        info = await self._info(symbol)

        def _grade(score: float | None) -> str:
            if score is None:
                return "N/A"
            if score >= 80:
                return "A"
            if score >= 65:
                return "B"
            if score >= 50:
                return "C"
            if score >= 35:
                return "D"
            return "F"

        def _score_range(val, thresholds: list[tuple[float, float]], higher_is_better=True) -> float | None:
            """Map a value to a 0-100 score using threshold breakpoints."""
            if val is None:
                return None
            for threshold, score in thresholds:
                if higher_is_better and val >= threshold:
                    return score
                if not higher_is_better and val <= threshold:
                    return score
            return thresholds[-1][1] if thresholds else 20.0

        categories = []

        # --- Profitability ---
        roe_val = _safe(info, "returnOnEquity")
        roa_val = _safe(info, "returnOnAssets")
        pm_val = _safe(info, "profitMargins")
        om_val = _safe(info, "operatingMargins")

        roe_score = _score_range(roe_val, [(0.25, 95), (0.18, 80), (0.12, 65), (0.05, 45), (0, 30)])
        roa_score = _score_range(roa_val, [(0.12, 95), (0.08, 80), (0.05, 65), (0.02, 45), (0, 30)])
        pm_score = _score_range(pm_val, [(0.25, 95), (0.15, 80), (0.08, 65), (0.02, 45), (0, 30)])
        om_score = _score_range(om_val, [(0.25, 95), (0.15, 80), (0.08, 65), (0.02, 45), (0, 30)])

        prof_metrics = [
            {"name": "Return on Equity", "value": roe_val, "score": roe_score, "grade": _grade(roe_score)},
            {"name": "Return on Assets", "value": roa_val, "score": roa_score, "grade": _grade(roa_score)},
            {"name": "Profit Margin", "value": pm_val, "score": pm_score, "grade": _grade(pm_score)},
            {"name": "Operating Margin", "value": om_val, "score": om_score, "grade": _grade(om_score)},
        ]
        prof_scores = [m["score"] for m in prof_metrics if m["score"] is not None]
        prof_avg = sum(prof_scores) / len(prof_scores) if prof_scores else None
        categories.append({"name": "Profitability", "score": prof_avg, "grade": _grade(prof_avg), "metrics": prof_metrics})

        # --- Growth ---
        rev_growth = _safe(info, "revenueGrowth")
        earn_growth = _safe(info, "earningsGrowth")

        rg_score = _score_range(rev_growth, [(0.30, 95), (0.15, 80), (0.05, 65), (0.0, 45), (-0.1, 25)])
        eg_score = _score_range(earn_growth, [(0.30, 95), (0.15, 80), (0.05, 65), (0.0, 45), (-0.1, 25)])

        growth_metrics = [
            {"name": "Revenue Growth", "value": rev_growth, "score": rg_score, "grade": _grade(rg_score)},
            {"name": "Earnings Growth", "value": earn_growth, "score": eg_score, "grade": _grade(eg_score)},
        ]
        growth_scores = [m["score"] for m in growth_metrics if m["score"] is not None]
        growth_avg = sum(growth_scores) / len(growth_scores) if growth_scores else None
        categories.append({"name": "Growth", "score": growth_avg, "grade": _grade(growth_avg), "metrics": growth_metrics})

        # --- Financial Health ---
        cr_val = _safe(info, "currentRatio")
        dte_val = _safe(info, "debtToEquity")
        qr_val = _safe(info, "quickRatio")

        cr_score = _score_range(cr_val, [(2.5, 95), (1.8, 80), (1.2, 65), (0.8, 45), (0, 25)])
        dte_score = _score_range(dte_val, [(30, 95), (60, 80), (100, 65), (150, 45), (250, 25)], higher_is_better=False)
        qr_score = _score_range(qr_val, [(2.0, 95), (1.5, 80), (1.0, 65), (0.5, 45), (0, 25)])

        health_metrics = [
            {"name": "Current Ratio", "value": cr_val, "score": cr_score, "grade": _grade(cr_score)},
            {"name": "Debt to Equity", "value": dte_val, "score": dte_score, "grade": _grade(dte_score)},
            {"name": "Quick Ratio", "value": qr_val, "score": qr_score, "grade": _grade(qr_score)},
        ]
        health_scores = [m["score"] for m in health_metrics if m["score"] is not None]
        health_avg = sum(health_scores) / len(health_scores) if health_scores else None
        categories.append({"name": "Financial Health", "score": health_avg, "grade": _grade(health_avg), "metrics": health_metrics})

        # --- Valuation ---
        pe_val = _safe(info, "trailingPE")
        pb_val = _safe(info, "priceToBook")
        peg_val = _safe(info, "pegRatio")

        pe_score = _score_range(pe_val, [(12, 95), (18, 80), (28, 65), (45, 45), (80, 25)], higher_is_better=False) if pe_val and pe_val > 0 else None
        pb_score = _score_range(pb_val, [(1.5, 95), (3, 80), (5, 65), (10, 45), (20, 25)], higher_is_better=False) if pb_val and pb_val > 0 else None
        peg_score = _score_range(peg_val, [(0.8, 95), (1.2, 80), (1.8, 65), (2.5, 45), (4, 25)], higher_is_better=False) if peg_val and peg_val > 0 else None

        val_metrics = [
            {"name": "P/E Ratio", "value": pe_val, "score": pe_score, "grade": _grade(pe_score)},
            {"name": "Price to Book", "value": pb_val, "score": pb_score, "grade": _grade(pb_score)},
            {"name": "PEG Ratio", "value": peg_val, "score": peg_score, "grade": _grade(peg_score)},
        ]
        val_scores = [m["score"] for m in val_metrics if m["score"] is not None]
        val_avg = sum(val_scores) / len(val_scores) if val_scores else None
        categories.append({"name": "Valuation", "score": val_avg, "grade": _grade(val_avg), "metrics": val_metrics})

        # --- Overall ---
        all_cat_scores = [c["score"] for c in categories if c["score"] is not None]
        overall_score = sum(all_cat_scores) / len(all_cat_scores) if all_cat_scores else None

        data = {
            "overall_score": round(overall_score, 1) if overall_score is not None else None,
            "overall_grade": _grade(overall_score),
            "categories": categories,
            "fetched_at": _now_ts(),
        }
        _put_cache(symbol, "rating_scorecard", data)
        return data

    async def get_company_info(self, symbol: str) -> dict:
        cached = _get_cached(symbol, "company_info")
        if cached is not None:
            return cached

        info = await self._info(symbol)

        # Extract CEO from company officers with compensation
        officers = info.get("companyOfficers", [])
        ceo_name = None
        ceo_title = None
        ceo_compensation = None
        officer_list = []
        for o in (officers or [])[:8]:
            name = o.get("name", "")
            title = o.get("title", "")
            age = o.get("age")
            total_pay = o.get("totalPay")
            salary = o.get("salary")
            bonus = o.get("bonus")
            stock_awards = o.get("stockAwards")
            other_comp = o.get("otherCompensation")
            exercised_value = o.get("exercisedValue")
            officer_entry = {"name": name, "title": title, "age": age}
            if total_pay:
                officer_entry["total_pay"] = total_pay
            officer_list.append(officer_entry)
            if ceo_name is None and ("ceo" in title.lower() or "chief executive" in title.lower()):
                ceo_name = name
                ceo_title = title
                comp: dict[str, Any] = {}
                if salary: comp["salary"] = salary
                if bonus: comp["bonus"] = bonus
                if stock_awards: comp["stock_awards"] = stock_awards
                if other_comp: comp["other_compensation"] = other_comp
                if total_pay: comp["total_compensation"] = total_pay
                if exercised_value: comp["exercised_value"] = exercised_value
                if comp:
                    ceo_compensation = comp

        # HQ location
        city = info.get("city")
        state = info.get("state")
        country = info.get("country")
        hq_parts = [p for p in [city, state, country] if p]
        hq_location = ", ".join(hq_parts) if hq_parts else None

        # IPO date
        ipo_ts = info.get("firstTradeDateEpochUtc")
        ipo_date = None
        if ipo_ts:
            from datetime import datetime, timezone
            try:
                ipo_date = datetime.fromtimestamp(int(ipo_ts), tz=timezone.utc).strftime("%Y-%m-%d")
            except Exception:
                pass

        data = {
            "ceo": ceo_name,
            "ceo_title": ceo_title,
            "ceo_compensation": ceo_compensation,
            "hq_location": hq_location,
            "phone": info.get("phone"),
            "ipo_date": ipo_date,
            "currency": info.get("currency"),
            "market_cap": _safe(info, "marketCap"),
            "enterprise_value": _safe(info, "enterpriseValue"),
            "shares_outstanding": _safe(info, "sharesOutstanding"),
            "float_shares": _safe(info, "floatShares"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "employees": _safe(info, "fullTimeEmployees", int),
            "officers": officer_list or None,
            "fetched_at": _now_ts(),
        }
        _put_cache(symbol, "company_info", data)
        return data

    async def get_stock_splits(self, symbol: str) -> dict:
        cached = _get_cached(symbol, "stock_splits")
        if cached is not None:
            return cached

        splits = await asyncio.to_thread(_fetch_stock_splits, symbol)
        data = {"splits": splits or [], "fetched_at": _now_ts()}
        _put_cache(symbol, "stock_splits", data)
        return data

    async def get_earnings_detail(self, symbol: str) -> dict:
        cached = _get_cached(symbol, "earnings_detail")
        if cached is not None:
            return cached

        result = await asyncio.to_thread(_fetch_earnings_with_estimate, symbol)
        data = {**result, "fetched_at": _now_ts()}
        _put_cache(symbol, "earnings_detail", data)
        return data

    async def get_all(self, symbol: str) -> dict:
        results = await asyncio.gather(
            self.get_overview(symbol),
            self.get_key_stats(symbol),
            self.get_financials(symbol),
            self.get_analyst(symbol),
            self.get_ownership(symbol),
            self.get_events(symbol),
            self.get_narrative(symbol),
            self.get_financial_statements(symbol),
            self.get_analyst_detail(symbol),
            self.get_rating_scorecard(symbol),
            self.get_company_info(symbol),
            self.get_stock_splits(symbol),
            self.get_earnings_detail(symbol),
            return_exceptions=True,
        )
        keys = [
            "overview", "key_stats", "financials", "analyst", "ownership",
            "events", "narrative", "financial_statements", "analyst_detail",
            "rating_scorecard", "company_info", "stock_splits", "earnings_detail",
        ]
        out: dict[str, Any] = {}
        for key, result in zip(keys, results):
            if isinstance(result, Exception):
                log.warning("Stock profile module %s failed for %s: %s", key, symbol, result)
                out[key] = None
            else:
                out[key] = result
        return out
