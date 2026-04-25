# Swing Screener Dashboard — Backend Implementation Plan

## Status
- **Frontend**: COMPLETE (12 new files, 8 sections, mock data, all quality gates pass)
- **Backend**: NOT STARTED — this plan covers the backend implementation

## Research Summary

5 research agents analyzed the exact trading methodologies:
- Qullamaggie breakout methodology (3-stage framework + ATR matrix)
- Minervini 8-criteria Trend Template from "Trade Like a Stock Market Wizard"
- O'Neil CANSLIM from "How to Make Money in Stocks"
- Weinstein Stage Analysis from "Secrets for Profiting in Bull and Bear Markets"
- 13 legendary traders' quantifiable rules (Shannon, Livermore, Darvas, Schwartz, etc.)

## Architecture: Two New Backend Files

### 1. `backend/swing_screeners.py` — Core computation engine

**Cross-sectional pre-computation (required by Qullamaggie + Minervini + 97 Club):**
```
compute_rs_percentiles(universe_bars, timeframes) -> {symbol: {tf: pctile}}
compute_atr_rs(universe_bars) -> {symbol: atr_rs_pctile}
compute_ibd_rs_ranks(universe_bars) -> {symbol: weighted_rs_rank}
```

**Qullamaggie Screener (4 criteria):**
1. RS >= 97 on at least one of 1W/1M/3M/6M (universe percentile rank)
2. MA Stack: Price >= EMA10 >= SMA20 >= SMA50 >= SMA100 >= SMA200
3. ATR RS >= 50 (above-average ADR% vs universe)
4. Price-to-20-Day Range >= 50%
- Enrichment: ATR extension to SMA50, stage classification, big move %, consolidation score
- Sort by: ATR Extension to SMA50 (largest first)
- 7x+ ATR extension = bold red (over-extended)

**Minervini Screener (10 criteria):**
1. Price > SMA150 AND Price > SMA200
2. SMA150 > SMA200
3. SMA200 rising for 22+ trading days
4. SMA50 > SMA150 AND SMA50 > SMA200
5. Price > SMA50
6. Price >= 30% above 52-week low
7. Within 25% of 52-week high
8. IBD-style RS Rank >= 70 (weighted: 40% 3mo + 20% 6/9/12mo)
9. Green candle (Close >= Open, Close >= prev Close)
10. Market cap >= $1B

**O'Neil Screener (5 criteria, fundamental data from yfinance):**
1. Positive TTM EPS (from income_stmt quarterly)
2. Forecast earnings growth 25%+ (from analyst estimates)
3. Positive ROE (net_income / total_equity)
4. Positive profit margin (net_income / total_revenue)
5. ROE + NOPM >= 25%

**Weinstein Stage Classifier:**
- SMA150 (30-week MA) as anchor
- Stage 2: close > SMA150 AND SMA150 slope > 0.5%/10d
- Stage 4: close < SMA150 AND SMA150 slope < -0.5%/10d
- Stage 3: near flat SMA150, was recently Stage 2
- Stage 1: everything else

**ATR Matrix (13 fixed symbols):**
- 11 Sector SPDRs + RSP + QQQE
- ATR(14) True Range, EMA(21), close
- price_vs_21ema_atr = (close - EMA21) / ATR14
- Sort by extension descending

**97 Club:**
- Universe: all $1B+ stocks
- RS = excess return vs SPY on 3 timeframes (1D, 1W, 1M)
- Ranked as percentile across universe
- Filter: >= 97 on ALL THREE
- TML flag: in 97 Club for 3+ consecutive weeks

**Stockbee Scans:**
- 9M Movers: volume > avg_50d AND volume >= 9M
- 20% Weekly: abs(5-day return) >= 20% (both directions)
- 4% Daily: daily return >= 4% (bullish only)

**Breadth Metrics:**
- 4 universes: NASDAQ100, SP500, Composite (516), $1B+
- Per universe: up/down counts (day/week/month), ratios, % above SMA20/50/200, new 20d highs/lows

**Leading Industries:**
- Group by yfinance industry
- Avg weekly + monthly return per group
- RS vs SPY
- Top 20% (~30 of ~149 groups)

**Trend Grades (A+ to F):**
- Multi-timeframe RS composite
- MA stack alignment score
- Volume trend
- Grade assignment based on percentile brackets

### 2. `backend/routers/swing_routes.py` — REST endpoints

```
GET /api/swing/dashboard    — Full composite (all sections)
GET /api/swing/breadth      — Breadth metrics only
GET /api/swing/screener/{name} — Guru screener (qullamaggie|minervini|oneil)
GET /api/swing/atr-matrix   — Sector ATR matrix
GET /api/swing/club97       — 97 Club
GET /api/swing/stockbee/{scan} — Stockbee scan
GET /api/swing/industries   — Leading industries
GET /api/swing/stages       — Stage analysis
GET /api/swing/grades       — Trend grades
```

## Key Implementation Details

### Two-Pass Architecture
Qullamaggie, Minervini, and 97 Club all require **cross-sectional ranking** (percentile within universe). This means:
1. Pass 1: Fetch bars for ALL symbols, compute returns/ADR for each
2. Rank into percentiles
3. Pass 2: Apply per-symbol filters using the pre-computed ranks

### Reusable Existing Code
- `indicators._sma()`, `_ema()`, `_atr()` — core indicator math
- `custom_indicators.adr_pct()`, `sma_distance_atr()`, `relative_volume()` — custom metrics
- `screener._bar_cache` + `refresh_cache()` — bar data fetching
- `screener.load_universe()` — universe loading
- `sector_rotation.SECTORS` — sector ETF list
- `universe_prescreen.prescreen_universe()` — liquidity filter
- `yahoo_data.batch_quotes()` — market cap + fundamental data

### New Indicators Needed
1. `ma_stack_aligned(df)` — Price >= EMA10 >= SMA20 >= SMA50 >= SMA100 >= SMA200
2. `price_in_20d_range(df)` — (close - low20) / (high20 - low20) * 100
3. `big_move_pct(df, lookback=63)` — (close / min(low, 63d) - 1) * 100
4. `consolidation_score(df)` — composite: range contraction + higher lows + MA proximity + vol dryup

### Caching Strategy
| Section | Cache TTL | Rationale |
|---------|-----------|-----------|
| Breadth metrics | 15 min | Market data changes with each bar |
| Guru screeners | 5 min | Quick refresh for active scanning |
| ATR matrix | 5 min | Small dataset (13 ETFs) |
| 97 Club | 15 min | Large universe, expensive |
| Stockbee scans | 5 min | Intraday changes |
| Leading industries | 30 min | Very expensive (industry mapping) |
| Stage analysis | 15 min | Based on 150-day MA, changes slowly |
| Trend grades | 15 min | Multi-timeframe, changes slowly |
| $1B+ universe | 24 hours | Market caps stable day-to-day |

### Data Requirements
- Minimum bars: 252 (1 year) for 52-week high/low + SMA200
- Recommended: 504 (2 years) for stable SMA200 warmup
- Interval: 1d only
- Universe sizes: NASDAQ100 (~100), SP500 (~500), Composite (~516), $1B+ (~2,486)

## Implementation Phases

### Phase 1: Core Infrastructure + ATR Matrix + Stockbee
- Universe helpers (DJIA, composite, $1B+)
- Bar fetching with screener cache reuse
- ATR Matrix (13 symbols, fast)
- Stockbee scans (reuse existing scan functions)

### Phase 2: Cross-Sectional RS + Qullamaggie + Minervini
- RS percentile computation (universe-wide)
- IBD-style weighted RS ranks
- ATR RS percentile
- Qullamaggie 4-criteria filter + enrichment
- Minervini 10-criteria filter

### Phase 3: 97 Club + Stage Analysis + Breadth
- 97 Club (3-timeframe RS, all >= 97)
- Weinstein stage classifier
- Breadth metrics for 4 universes

### Phase 4: O'Neil + Industries + Grades
- O'Neil fundamentals (yfinance quarterly data)
- Industry grouping + ranking
- Trend strength grading (A+ to F)

## Verification
- All 531 backend tests must still pass
- Manual curl tests for each endpoint
- Frontend mock fallback still works when backend endpoints return real data
- Response times: < 5s for ATR matrix, < 30s for guru screeners, < 60s for full dashboard
