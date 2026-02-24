# Stage 2a Session Prompt: Chart Core & Volume

You are working on a trading platform built with **FastAPI** (backend) and **React 18 + TypeScript + Zustand + TailwindCSS** (dashboard). The project is at `C:\Users\segev\sdvesdaW\trading`.

## Current State
- **Backend** (`backend/`): FastAPI with 40+ endpoints, IBKR integration (ib_insync), 8 technical indicators (RSI, SMA, EMA, MACD, BBANDS, ATR, STOCH, PRICE) in `indicators.py`, rule engine with AND/OR logic + cooldown, order execution, virtual trading simulation, historical replay, mock GBM data, real-time WebSocket, SQLite persistence. Yahoo Finance bars endpoint (`GET /api/yahoo/{symbol}/bars`) supports `period` and `interval` params. The `_yf_bars()` helper uses `yfinance` to fetch OHLCV data.
- **Dashboard** (`dashboard/`): React 18 + Vite + Zustand + TailwindCSS. Pages: Dashboard, TradeBotPage, MarketPage, SimulationPage, SettingsPage. Uses **lightweight-charts v4.2** for candlesticks. Dark terminal theme (Bloomberg-style palette). Watchlist grid, comparison overlay, indicator selector (SMA 20/50, EMA 12/26, BB, VWAP overlays + RSI/MACD oscillator sub-panels).
- **Database**: SQLite with tables: `users` (id, email, password_hash, created_at, settings JSON), `rules`, `trades`, `sim_account`, `sim_positions`, `sim_orders` — all with `user_id TEXT DEFAULT 'demo'`.
- **3 operating modes**: IBKR Live, IBKR Paper, Simulation (offline with mock data).

### Stage 1 Completed
Stage 1 has been implemented. The following infrastructure is in place and MUST be used:
- **Toast system**: `ToastProvider` + `useToast()` hook (`success`, `error`, `warning`, `info`). Toast on all API errors and user actions. Use this for any error feedback in Stage 2a.
- **Error boundaries**: `ErrorBoundary` component wraps each page in `App.tsx`. New pages/components automatically benefit.
- **Settings page**: `SettingsPage.tsx` at `/settings`. User preferences stored as JSON blob in `users.settings` column.
- **Auth scaffold**: `users` table with demo user, `user_id` on all tables, JWT token infrastructure (demo user auto-authenticates). `get_current_user()` FastAPI dependency available.
- **Loading skeletons**: `Skeleton` component used on Dashboard, TradeBotPage, MarketPage.
- **API auth header**: `api.ts` sends `Authorization: Bearer <token>` on all requests.

### Existing Chart Architecture
The current charting code lives in three files:
- **`TradingChart.tsx`** — single monolithic component that creates a `lightweight-charts` instance, renders candlesticks + volume histogram (overlay on same chart, `priceScaleId: 'volume'` with `scaleMargins: { top: 0.8, bottom: 0 }`), handles live candle updates via WebSocket, replay bar injection, overlay indicators (SMA, EMA, BB, VWAP), and comparison overlay normalization.
- **`IndicatorPanel.tsx`** — separate `RSIPanel` and `MACDPanel` components, each creating their own `lightweight-charts` instance. Combined `IndicatorPanel` renders whichever oscillators are selected.
- **`MarketPage.tsx`** — integrates chart + indicator panels + symbol search + timeframe selector (1D/1W/1M/3M/6M/1Y/2Y/ALL) + comparison toggle + `IndicatorSelector`.

Key details about the current chart:
- Chart theme colors: bg `#080d18`, text `#5f7a9d`, grid `#111f35`, crosshair `#2b4a7a`, candle up `#00e07a`, candle down `#ff3d5a`.
- Volume is currently rendered as a histogram series inside the main chart (not a separate pane).
- Indicators are computed client-side in `utils/indicators.ts` (calcSMA, calcEMA, calcBB, calcVWAP, calcRSI, calcMACD).
- The store tracks `selectedIndicators: IndicatorId[]` where `IndicatorId = 'sma20' | 'sma50' | 'ema12' | 'ema26' | 'bb' | 'vwap' | 'rsi' | 'macd'`.
- `TIMEFRAMES` in MarketPage maps labels to Yahoo Finance `period`/`interval` pairs.
- `intervalToSeconds()` converts interval strings like `'5m'`, `'1d'`, `'1wk'` to seconds.

### Terminal Theme (Tailwind)
All new UI must use the existing terminal dark theme palette:
- Backgrounds: `terminal-bg` (#080d18), `terminal-surface` (#0e1726), `terminal-elevated` (#131f33)
- Borders: `terminal-border` (#1c2e4a), `terminal-muted` (#243650)
- Text: `terminal-text` (#dce8f5), `terminal-dim` (#5f7a9d), `terminal-ghost` (#384d6b)
- Colors: `terminal-green` (#00e07a), `terminal-red` (#ff3d5a), `terminal-blue` (#4f91ff), `terminal-amber` (#f59e0b)
- Font: JetBrains Mono (`font-mono`), sizes 10-11px for chart labels, 12-13px for UI text

## What to Build (Stage 2a)

### 1. Chart Toolbar (`dashboard/src/components/chart/ChartToolbar.tsx`)

A compact toolbar that sits above the chart. Contains:

**Timeframe buttons:**
- Options: `1m`, `5m`, `15m`, `30m`, `1H`, `4H`, `1D`, `1W`, `1M`
- Each maps to a Yahoo Finance `{ period, interval }` pair:
  - `1m` → `{ period: '1d', interval: '1m' }`
  - `5m` → `{ period: '5d', interval: '5m' }`
  - `15m` → `{ period: '5d', interval: '15m' }`
  - `30m` → `{ period: '1mo', interval: '30m' }`
  - `1H` → `{ period: '3mo', interval: '1h' }`
  - `4H` → `{ period: '6mo', interval: '4h' }` (note: yfinance does not support `4h` natively — compute by aggregating `1h` bars or use `60m` with `period: '6mo'`)
  - `1D` → `{ period: '1y', interval: '1d' }`
  - `1W` → `{ period: '2y', interval: '1wk' }`
  - `1M` → `{ period: '5y', interval: '1mo' }`
- Active timeframe visually highlighted (same style as current MarketPage timeframe buttons: `border-terminal-blue/50 text-terminal-blue bg-terminal-blue/10`)

**Chart type toggle:**
- Options: Candlestick, OHLC Bar, Line, Area, Baseline
- Small icon buttons or text toggle group
- Default: Candlestick
- Selected type stored in `useMarketStore` as `chartType`

**Indicator quick-add dropdown:**
- Button labeled "Indicators" that opens a dropdown
- Lists all indicators from `INDICATOR_DEFS` (SMA 20, SMA 50, EMA 12, EMA 26, BB, VWAP, RSI, MACD)
- Checkboxes showing which are active
- Clicking toggles the indicator (calls existing `toggleIndicator` action)
- This replaces or supplements the existing `IndicatorSelector` component on MarketPage

**Fullscreen toggle:**
- Button with expand/collapse icon
- Toggles the chart container to fill the entire viewport (CSS `position: fixed; inset: 0; z-index: 50`)
- Press Escape or click again to exit

**Screenshot button:**
- Button with camera icon
- Calls `chart.takeScreenshot()` from the lightweight-charts API
- Opens the screenshot in a new tab or triggers a download as `{symbol}_{timeframe}_{timestamp}.png`

### 2. Volume Panel (`dashboard/src/components/chart/VolumePanel.tsx`)

A dedicated volume histogram rendered as a **separate lightweight-charts pane below the main chart**, instead of the current overlay approach.

- Creates its own `lightweight-charts` instance (like RSIPanel/MACDPanel do)
- Volume bars color-coded: green (`#00874a66`) when `close >= open`, red (`#99243866`) when `close < open` (matching current colors)
- Time axis synchronized with main chart — when main chart scrolls/zooms, volume pane follows
- Time axis labels hidden on volume pane (`timeScale: { visible: false }`) since the main chart shows them
- Compact height: ~60-80px
- Header: small label "Volume" in `text-[10px] font-mono text-terminal-ghost`

**Time axis sync implementation:**
- Subscribe to main chart's `timeScale().subscribeVisibleLogicalRangeChange()`
- When range changes, call `volumeChart.timeScale().setVisibleLogicalRange(range)` on the volume pane
- Also sync from volume → main if user scrolls in the volume pane

### 3. Refactor TradingChart.tsx

Major refactor to split responsibilities:

**Extract `useChart` hook (`dashboard/src/hooks/useChart.ts`):**
- Encapsulates chart creation, ResizeObserver setup, and cleanup
- Accepts: `containerRef`, `chartOptions` (merged with theme defaults)
- Returns: `{ chart, mainSeries }` refs
- Handles the lifecycle (create on mount, remove on unmount, resize)
- Used by TradingChart, VolumePanel, RSIPanel, MACDPanel (they all currently duplicate chart creation code)

**Support multiple chart types in TradingChart:**
- Accept `chartType` prop (or read from store): `'candlestick' | 'ohlc' | 'line' | 'area' | 'baseline'`
- When `chartType` changes, remove the old main series and create a new one of the correct type:
  - `'candlestick'` → `chart.addCandlestickSeries({...})`
  - `'ohlc'` → `chart.addBarSeries({...})` with OHLC bar styling
  - `'line'` → `chart.addLineSeries({...})` using close prices
  - `'area'` → `chart.addAreaSeries({...})` with gradient fill
  - `'baseline'` → `chart.addBaselineSeries({...})` with top/bottom color zones
- Each type gets appropriate terminal theme colors
- Volume overlay is removed from TradingChart (moved to VolumePanel)
- All existing features must still work: live candle updates, replay bar injection, overlay indicators, comparison overlay

**Heikin-Ashi computation utility (`dashboard/src/utils/heikinAshi.ts`):**
- Pure function: `toHeikinAshi(bars: OHLCVBar[]): OHLCVBar[]`
- HA Close = (Open + High + Low + Close) / 4
- HA Open = (prev HA Open + prev HA Close) / 2 (first bar: (Open + Close) / 2)
- HA High = max(High, HA Open, HA Close)
- HA Low = min(Low, HA Open, HA Close)
- Volume passes through unchanged
- Applied as a data transform before setting candlestick data when Heikin-Ashi mode is active

### 4. Chart Annotations API (`dashboard/src/utils/chartAnnotations.ts`)

Utility functions for adding markers to charts. This API is used later by backtest (Stage 4) and alerts (Stage 5), so design it to be reusable.

**Export these functions:**

```typescript
interface TradeMarker {
  time: number        // Unix seconds
  action: 'BUY' | 'SELL'
  price: number
  label?: string       // e.g., "RSI < 30"
}

// Add buy/sell markers to a candlestick/line series
function addTradeMarkers(series: ISeriesApi<any>, trades: TradeMarker[]): void
// Uses lightweight-charts `series.setMarkers()` API
// BUY markers: position 'belowBar', shape 'arrowUp', color '#00e07a'
// SELL markers: position 'aboveBar', shape 'arrowDown', color '#ff3d5a'

// Remove all markers from a series
function clearTradeMarkers(series: ISeriesApi<any>): void
// Calls series.setMarkers([])
```

Markers must be sorted by time (lightweight-charts requirement). Use the native `setMarkers()` API — no canvas overlay needed for markers.

### 5. Backend: Indicator Endpoint

**Add to `backend/main.py`:**

`GET /api/market/{symbol}/indicators`

Query parameters:
- `indicator` (required): one of `RSI`, `SMA`, `EMA`, `MACD`, `BBANDS`, `ATR`, `STOCH`
- `length` (optional, default varies by indicator): primary parameter
- `period` (optional, default `1y`): Yahoo Finance period for bar data
- `interval` (optional, default `1d`): Yahoo Finance interval for bar data
- Additional params as needed (e.g., `fast`, `slow`, `signal` for MACD; `band` for BBANDS)

Response: `[{"time": 1700000000, "value": 42.5}, ...]`

Implementation:
1. Fetch bars using existing `_yf_bars()` helper
2. Convert to pandas DataFrame
3. Call `indicators.calculate(df, indicator, params)`
4. Serialize result using a new helper function

**Add to `backend/indicators.py`:**

```python
def series_to_json(series: pd.Series, df: pd.DataFrame) -> list[dict]:
    """Convert a pandas Series to [{time, value}, ...] for JSON response.

    Uses the 'time' column from df for timestamps.
    Drops NaN values (indicator warmup period).
    """
```

This helper converts the pandas Series output from `calculate()` into the `[{time, value}]` format the frontend expects.

### 6. Backend: Extended Yahoo Bars

**Modify the existing `GET /api/yahoo/{symbol}/bars` endpoint (or ensure the `_yf_bars` helper works correctly with):**

Support these additional intervals:
- `1m` — requires `period` <= `7d` (Yahoo Finance limitation)
- `2m` — requires `period` <= `60d`
- `5m` — already supported
- `15m` — requires `period` <= `60d`
- `30m` — already supported

Add validation: if the requested interval/period combination is invalid for Yahoo Finance, return a helpful error message (use the Stage 1 `{error, detail}` format). Common constraints:
- `1m` data only available for last 7 days
- `2m`, `5m`, `15m`, `30m` data only available for last 60 days
- `60m`/`1h` data available for last 730 days

## Files to Create
- `dashboard/src/components/chart/ChartToolbar.tsx`
- `dashboard/src/components/chart/VolumePanel.tsx`
- `dashboard/src/hooks/useChart.ts` (reusable chart creation hook)
- `dashboard/src/utils/heikinAshi.ts` (Heikin-Ashi bar conversion)
- `dashboard/src/utils/chartAnnotations.ts` (trade marker helpers)

## Files to Modify
- `dashboard/src/components/chart/TradingChart.tsx` — major refactor: remove volume overlay, support multiple chart types, use `useChart` hook, accept `chartType` prop
- `dashboard/src/components/chart/IndicatorPanel.tsx` — refactor RSIPanel and MACDPanel to use `useChart` hook, generalize for any oscillator
- `dashboard/src/pages/MarketPage.tsx` — integrate ChartToolbar, replace inline timeframe buttons with toolbar, add VolumePanel below chart, wire chart type from store
- `dashboard/src/pages/Dashboard.tsx` — use refactored TradingChart (ensure backward compatibility, no breakage)
- `dashboard/src/store/index.ts` — extend `MarketState` with `chartType: ChartType` field and `setChartType` action
- `dashboard/src/types/index.ts` — add `ChartType` type (`'candlestick' | 'ohlc' | 'line' | 'area' | 'baseline'`), add chart-related types (e.g., `TradeMarker`)
- `dashboard/src/services/api.ts` — add `fetchIndicatorData(symbol, indicator, params)` function
- `backend/main.py` — add `GET /api/market/{symbol}/indicators` endpoint, add interval validation to yahoo bars endpoint
- `backend/indicators.py` — add `series_to_json()` serialization helper

## Dependencies to Install

**Backend** (no new packages — uses existing `yfinance`, `pandas`, `numpy`).

**Frontend** (no new packages — uses existing `lightweight-charts`, `clsx`, `zustand`, `tailwindcss`).

## Definition of Done
1. ChartToolbar renders above the chart with timeframe buttons (1m, 5m, 15m, 30m, 1H, 4H, 1D, 1W, 1M)
2. Clicking a timeframe button loads the correct bars from Yahoo Finance
3. Chart type toggle switches between Candlestick, OHLC Bar, Line, Area, and Baseline views
4. Chart type selection persists in Zustand store
5. Indicator dropdown in toolbar shows all indicators with toggle checkboxes
6. Fullscreen button expands chart to fill viewport; Escape or toggle exits
7. Screenshot button captures chart and triggers download
8. VolumePanel renders below the main chart as a separate pane with color-coded bars
9. Volume pane time axis is synchronized with main chart (scroll/zoom in sync)
10. `useChart` hook is extracted and used by TradingChart, VolumePanel, RSIPanel, and MACDPanel
11. Heikin-Ashi utility correctly transforms OHLCV bars
12. `addTradeMarkers()` and `clearTradeMarkers()` work with lightweight-charts series
13. `GET /api/market/{symbol}/indicators?indicator=RSI&length=14` returns `[{time, value}, ...]`
14. `series_to_json()` helper in `indicators.py` correctly serializes pandas Series with timestamps
15. Yahoo bars endpoint accepts `1m`, `2m`, `5m`, `15m` intervals with proper validation
16. Invalid interval/period combinations return `{error, detail}` JSON
17. All existing chart features still work: live candle updates, replay bars, overlay indicators, comparison mode
18. Dashboard page chart is not broken by the refactor
19. MarketPage layout integrates toolbar + chart + volume pane + oscillator panels cleanly
20. All new UI uses the terminal dark theme (colors, font, border styles)
21. Toast notifications appear on API errors (using Stage 1 toast system)
22. No TypeScript errors (`npm run typecheck` passes)

## Important Notes
- Do NOT break existing functionality. The refactor must be backward-compatible — Dashboard, TradeBotPage, SimulationPage must all still work with the refactored TradingChart.
- Use the Stage 1 toast system (`useToast()`) for any error feedback (e.g., failed bar loads, screenshot errors).
- Error boundaries from Stage 1 already wrap each page — no need to add new ones.
- The charting library is **lightweight-charts v4.2** (package: `lightweight-charts@^4.2.0`). Use its API directly — no wrappers like `react-lightweight-charts`.
- The terminal dark theme must be consistent across all new components. Reference `tailwind.config.ts` for exact colors.
- The `useChart` hook should be generic enough to reuse in Stages 2b (drawing tools) and 2c (multi-pane sync).
- The chart annotations API (`addTradeMarkers`) is intentionally minimal now — it will be consumed by Stage 4 (backtest trade markers) and Stage 5 (alert markers on chart).
- For the 4H timeframe: yfinance does not natively support `4h` interval. Either aggregate `1h` bars client-side (group every 4 bars), or use `interval='60m'` with appropriate period and note the limitation. Do not let this block the rest of the work.
- Test everything with `SIM_MODE=true` and `MOCK_MODE=true` (no IBKR needed).
