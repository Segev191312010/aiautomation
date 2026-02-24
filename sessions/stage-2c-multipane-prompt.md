# Stage 2c Session Prompt: Multi-Pane Layout & Crosshair Sync

You are working on a trading platform built with **FastAPI** (backend) and **React 18 + TypeScript + Zustand + TailwindCSS** (dashboard). The project is at `C:\Users\segev\sdvesdaW\trading`.

## Current State
- **Backend** (`backend/`): FastAPI with 40+ endpoints, IBKR integration (ib_insync), 8 technical indicators (RSI, SMA, EMA, MACD, BBANDS, ATR, STOCH, PRICE), rule engine with AND/OR logic + cooldown, order execution, virtual trading simulation, historical replay, mock GBM data, real-time WebSocket, SQLite persistence. Auth scaffold with `users` table, JWT tokens, settings system (Stage 1 complete).
- **Dashboard** (`dashboard/`): React 18 + Vite + Zustand + TailwindCSS. Pages: Dashboard, TradeBotPage, MarketPage, SimulationPage, SettingsPage. Uses lightweight-charts v4.2 for candlesticks. Dark terminal theme. Watchlist grid, comparison overlay. Toast notifications, error boundaries, loading skeletons (Stage 1 complete). Chart toolbar with timeframe buttons, chart type toggle (candle/line/area/baseline), volume histogram, indicator quick-add dropdown, chart annotations/markers API (Stage 2a complete). Drawing tools with HTML5 Canvas overlay — horizontal lines, trendlines, Fibonacci retracement with per-symbol persistence (Stage 2b complete).
- **Database**: SQLite with tables: `rules`, `trades`, `sim_account`, `sim_positions`, `sim_orders`, `users` — all with `user_id` column.
- **3 operating modes**: IBKR Live, IBKR Paper, Simulation (offline with mock data).
- **Existing chart architecture**: `TradingChart.tsx` creates a single `lightweight-charts` instance with candlestick + volume histogram on the same chart (volume uses a separate price scale with `scaleMargins: { top: 0.8, bottom: 0 }`). `IndicatorPanel.tsx` renders RSI and MACD as independent chart instances placed side-by-side below the main chart with a fixed height of `h-36`. The canvas overlay for drawing tools (Stage 2b) is positioned absolutely on top of the main chart div.
- **Key indicator utilities** (`dashboard/src/utils/indicators.ts`): Pure TypeScript indicator calculations — `calcSMA`, `calcEMA`, `calcBB`, `calcVWAP`, `calcRSI`, `calcMACD`. Types: `LinePoint`, `BandsResult`, `MACDResult`. Registry: `INDICATOR_DEFS` array with `IndicatorId` type (`'sma20' | 'sma50' | 'ema12' | 'ema26' | 'bb' | 'vwap' | 'rsi' | 'macd'`), each with `type: 'overlay' | 'oscillator'`.

## What to Build (Stage 2c)

### 1. Multi-Pane Chart Layout (`dashboard/src/components/chart/MultiPaneChart.tsx`)

**Create a wrapper component that manages vertically stacked chart panes:**
- Renders: main price chart pane (top) + volume pane (middle) + 0 to 3 oscillator sub-panes (bottom)
- Each pane is an **independent** `lightweight-charts` `createChart()` instance (NOT sub-charts on the same instance)
- Default layout: Price pane (60% height) + Volume pane (15% height) + one oscillator pane (25% height)
- Panes are stacked vertically in a flex column with `PaneDivider` components between them
- Maintain an array of chart instance refs for crosshair/scroll sync
- Accept props: `symbol: string`, `barSeconds: number`, `className?: string`, `showVolume?: boolean` (default true)
- The main price pane wraps the existing `TradingChart` component (refactored to expose its chart instance ref)
- Volume pane is a dedicated lightweight-charts instance showing only a histogram series
- Each oscillator pane uses the `OscillatorPane` component

**Pane state management:**
- Track active panes as an ordered array: `[{ id: string, type: 'price' | 'volume' | 'oscillator', indicatorId?: IndicatorId, height: number }]`
- `height` values are percentages (summing to 100)
- Price and volume panes are always present (volume can be toggled via `showVolume` prop)
- Oscillator panes are added/removed dynamically
- Maximum 3 oscillator panes at a time

### 2. Crosshair Synchronization (`dashboard/src/hooks/useCrosshairSync.ts`)

**Create a custom hook that synchronizes crosshairs across multiple chart instances:**
- Accept an array of `IChartApi` refs
- On each chart, call `subscribeCrosshairMove(param => ...)`
- When crosshair moves on chart A, call `setCrosshairPosition(price, point, series)` on all OTHER charts at the same `time` coordinate
- **Critical: prevent infinite sync loops.** Use a `syncing` ref flag:
  ```
  const syncingRef = useRef(false)
  // In the crosshair move handler:
  if (syncingRef.current) return  // skip if this event was triggered by sync
  syncingRef.current = true
  // ... update other charts ...
  syncingRef.current = false
  ```
- When crosshair leaves a chart (param.time is undefined), clear crosshair on all other charts
- Return a cleanup function that unsubscribes all listeners

### 3. Time Scale Synchronization (`dashboard/src/hooks/useTimeScaleSync.ts`)

**Create a custom hook that synchronizes scroll and zoom across chart instances:**
- Accept an array of `IChartApi` refs
- On each chart, call `chart.timeScale().subscribeVisibleTimeRangeChange(range => ...)`
- When one chart scrolls/zooms, call `chart.timeScale().setVisibleRange(range)` on all other charts
- Same infinite-loop prevention as crosshair sync (use a `syncing` ref flag)
- Also sync `subscribeVisibleLogicalRangeChange` for pixel-level scroll sync (this handles cases where charts have different time ranges)
- Return a cleanup function

### 4. Resizable Pane Heights (`dashboard/src/components/chart/PaneDivider.tsx`)

**Create a draggable divider component between panes:**
- Renders as a thin horizontal bar (4px height, full width) between two panes
- Visual: `bg-terminal-border` with a small grip indicator (three dots or a line) centered
- On hover: highlight to `bg-terminal-blue/40` with `cursor-row-resize`
- On mousedown: start tracking vertical mouse movement
- Calculate height delta as percentage of the container height
- Adjust the heights of the pane above and the pane below (maintaining their sum)
- Enforce minimum pane height of 80px (convert to percentage based on container height)
- On mouseup: stop tracking, commit new heights to state
- **Double-click**: reset both adjacent panes to their default heights
- Use `onMouseDown` → `window.addEventListener('mousemove'/'mouseup')` pattern for smooth dragging outside the divider element

### 5. Oscillator Pane Component (`dashboard/src/components/chart/OscillatorPane.tsx`)

**Create a generic oscillator pane that renders any oscillator indicator:**
- Accept props: `symbol: string`, `indicatorId: IndicatorId`, `onChartReady: (chart: IChartApi) => void`, `onRemove: () => void`, `className?: string`
- Creates its own `lightweight-charts` instance on mount
- Calls `onChartReady(chartInstance)` after creation so parent can register it for sync
- Based on `indicatorId`, renders the appropriate series:
  - **RSI** (`indicatorId: 'rsi'`):
    - One line series (pink `#f472b6`, lineWidth 2) for RSI values
    - Two reference line series at 70 (red `#ff3d5a44`) and 30 (green `#00e07a44`)
    - Right price scale range: `{ autoScale: false, scaleMargins: { top: 0.05, bottom: 0.05 } }` or set min/max to 0-100
  - **MACD** (`indicatorId: 'macd'`):
    - One histogram series for MACD-Signal difference (green when positive, red when negative)
    - One line series for MACD line (sky blue `#38bdf8`, lineWidth 2)
    - One line series for Signal line (orange `#fb923c`, lineWidth 1)
  - **Stochastic** (extend `IndicatorId` type and `INDICATOR_DEFS` to add `'stoch'`):
    - Two line series: %K (blue `#60a5fa`, lineWidth 2) and %D (orange `#fb923c`, lineWidth 1)
    - Two reference line series at 80 and 20
    - Add `calcStochastic(bars, kPeriod=14, dPeriod=3)` to `indicators.ts`
- Chart options: use the existing `PANEL_OPTS` theme from `IndicatorPanel.tsx` but with `timeScale.visible: false` (the time axis is only shown on the bottom-most pane)
- The bottom-most pane in the stack should have `timeScale.visible: true`
- Recalculate and update data when `bars` change (subscribe to `useMarketStore` for the symbol's bars)
- On unmount, call `chart.remove()` and clean up ResizeObserver

### 6. Pane Header Component (`dashboard/src/components/chart/PaneHeader.tsx`)

**Create a small header bar rendered at the top of each oscillator pane:**
- Shows indicator name and parameters (e.g., "RSI (14)", "MACD (12,26,9)", "Stoch (14,3)")
- Legend with color swatches for each series line
- Settings gear icon button — opens a small popover/dropdown to change indicator parameters:
  - RSI: `length` (default 14, range 2-100)
  - MACD: `fast` (12), `slow` (26), `signal` (9)
  - Stochastic: `kPeriod` (14), `dPeriod` (3)
- Close button (X) — calls `onRemove` to remove the pane
- Styled: `h-7`, `px-3`, `border-b border-terminal-border`, `bg-terminal-surface`, `text-[10px] font-mono text-terminal-ghost`

### 7. Pane Management UI (in chart toolbar area)

**Add an "Add Indicator Pane" button to the existing indicator selector / chart toolbar:**
- Button labeled "+ Indicator" or "Add Pane" in the oscillator section of `IndicatorSelector.tsx`
- Clicking opens a dropdown menu listing available oscillator indicators
- Only show oscillators NOT already visible as panes (filter out active ones)
- Selecting an indicator adds a new pane at the bottom of the stack
- The existing oscillator pill toggle buttons in `IndicatorSelector.tsx` should now add/remove panes (instead of the old side-by-side `IndicatorPanel` layout)
- Maximum 3 oscillator panes — disable the "Add" button when limit reached

### 8. Refactor TradingChart to Expose Chart Instance

**Modify `dashboard/src/components/chart/TradingChart.tsx`:**
- Add a `React.forwardRef` or a callback prop `onChartReady: (chart: IChartApi) => void` so the parent `MultiPaneChart` can access the internal `IChartApi` instance
- This is needed for crosshair sync and time scale sync
- Also add an `onCandleSeriesReady: (series: ISeriesApi<'Candlestick'>) => void` callback for crosshair position targeting
- Keep all existing functionality intact (live candle updates, overlay indicators, comparison mode, replay bars, resize observer)
- The drawing tools canvas overlay (Stage 2b) should continue working — it only applies to this main price pane

### 9. Volume as a Separate Pane

**Refactor volume out of `TradingChart.tsx` into its own pane:**
- Currently, volume is rendered as a histogram series inside the main candlestick chart with `scaleMargins: { top: 0.8, bottom: 0 }`
- In the new multi-pane layout, volume becomes its own `lightweight-charts` instance in a dedicated pane below the price chart
- The volume pane creates a histogram series, subscribes to the same bars, and colors bars green/red based on close vs open
- Volume pane has no header (or a minimal one saying "Vol")
- Volume pane height default: 15% of total
- Remove the volume histogram from `TradingChart.tsx` (it now lives in its own pane)

### 10. Store Extensions

**Modify `dashboard/src/store/index.ts` — extend `MarketState`:**
- Add `activePanes: PaneConfig[]` — ordered array of active pane configurations
- Add `paneSizes: Record<string, number>` — pane ID to height percentage mapping
- Add `setPaneSizes: (sizes: Record<string, number>) => void`
- Add `addOscillatorPane: (indicatorId: IndicatorId) => void` — adds a new pane config
- Add `removeOscillatorPane: (paneId: string) => void` — removes a pane config
- Add `oscillatorParams: Record<string, Record<string, number>>` — per-indicator custom parameters (e.g., `{ rsi: { length: 14 }, macd: { fast: 12, slow: 26, signal: 9 } }`)
- Add `setOscillatorParams: (indicatorId: string, params: Record<string, number>) => void`
- Default `activePanes`: `[{ id: 'price', type: 'price' }, { id: 'volume', type: 'volume' }]` (no oscillators by default)
- Default `paneSizes`: `{ price: 70, volume: 30 }` (adjusts automatically when oscillators are added)

### 11. Type Definitions

**Modify `dashboard/src/types/index.ts` — add new types:**
```typescript
export interface PaneConfig {
  id: string
  type: 'price' | 'volume' | 'oscillator'
  indicatorId?: IndicatorId  // only for oscillator panes
}

export interface PaneLayout {
  panes: PaneConfig[]
  sizes: Record<string, number>  // pane id -> height percentage
}
```

### 12. Add Stochastic Indicator

**Modify `dashboard/src/utils/indicators.ts`:**
- Add `'stoch'` to the `IndicatorId` union type
- Add `{ id: 'stoch', label: 'Stoch (14,3)', type: 'oscillator', color: '#60a5fa' }` to `INDICATOR_DEFS`
- Add `StochResult` type: `{ k: LinePoint[]; d: LinePoint[] }`
- Add `calcStochastic(bars: OHLCVBar[], kPeriod = 14, dPeriod = 3): StochResult` function:
  - %K = 100 * (close - lowest low over kPeriod) / (highest high over kPeriod - lowest low over kPeriod)
  - %D = SMA of %K over dPeriod

### 13. Update MarketPage

**Modify `dashboard/src/pages/MarketPage.tsx`:**
- Replace the current layout of `<TradingChart>` + `<IndicatorPanel>` with `<MultiPaneChart>`
- Pass `symbol={selectedSymbol}`, `barSeconds={barSeconds}`
- Remove the direct `<IndicatorPanel>` usage (its functionality is now inside `MultiPaneChart` via `OscillatorPane`)
- Keep all existing toolbar rows (symbol search, timeframe selector, compare toggle, indicator selector)
- The indicator selector's oscillator buttons now add/remove oscillator panes in the `MultiPaneChart`

### 14. Update Dashboard Page

**Modify `dashboard/src/pages/Dashboard.tsx`:**
- Replace the bare `<TradingChart>` with `<MultiPaneChart>` using a simpler config
- Pass `showVolume={true}` — show price + volume, but no oscillator panes by default on Dashboard
- Keep the existing chart header bar and KPI rail layout

## Dependencies to Install

**Frontend** (add to `dashboard/package.json`):
```
No new packages required — all functionality is built with existing dependencies (lightweight-charts, React, Zustand, TailwindCSS).
```

If the drag-resize implementation becomes complex, optionally consider:
```
react-resizable-panels (optional — only if manual mouse-event-based resize proves too buggy)
```

## Files to Create
- `dashboard/src/components/chart/MultiPaneChart.tsx` — layout wrapper managing stacked panes, crosshair sync, scroll sync
- `dashboard/src/components/chart/PaneDivider.tsx` — draggable resize handle between panes
- `dashboard/src/components/chart/OscillatorPane.tsx` — generic oscillator pane (RSI, MACD, Stochastic)
- `dashboard/src/components/chart/PaneHeader.tsx` — small header bar for each oscillator pane (name, settings gear, close button)
- `dashboard/src/components/chart/VolumePane.tsx` — dedicated volume histogram pane
- `dashboard/src/hooks/useCrosshairSync.ts` — hook for syncing crosshairs across chart instances
- `dashboard/src/hooks/useTimeScaleSync.ts` — hook for syncing scroll/zoom across chart instances

## Files to Modify
- `dashboard/src/components/chart/TradingChart.tsx` — expose chart instance ref via callback prop, remove volume histogram (moved to VolumePane), keep all other functionality
- `dashboard/src/components/chart/IndicatorPanel.tsx` — refactor: oscillator pane add/remove now goes through store + MultiPaneChart; this file may become a thin wrapper or be deprecated in favor of OscillatorPane
- `dashboard/src/components/indicators/IndicatorSelector.tsx` — oscillator toggles now add/remove panes via store actions instead of just toggling selectedIndicators
- `dashboard/src/pages/MarketPage.tsx` — replace TradingChart + IndicatorPanel with MultiPaneChart
- `dashboard/src/pages/Dashboard.tsx` — replace bare TradingChart with MultiPaneChart (simpler config)
- `dashboard/src/store/index.ts` — extend MarketState with activePanes, paneSizes, oscillatorParams, and related actions
- `dashboard/src/types/index.ts` — add PaneConfig, PaneLayout types
- `dashboard/src/utils/indicators.ts` — add 'stoch' to IndicatorId, add calcStochastic function, add StochResult type

## Definition of Done
1. Market page shows price chart + volume pane + at least one oscillator in vertically stacked panes
2. Moving crosshair on any pane shows crosshair at the same time coordinate on all other panes
3. Scrolling or zooming one pane scrolls/zooms all panes in sync
4. Dragging a divider between panes resizes them smoothly
5. User can add a new oscillator pane from the indicator selector / toolbar dropdown
6. User can close an oscillator pane via its header close button
7. RSI pane shows a line series with 30/70 reference lines
8. MACD pane shows histogram + MACD line + Signal line
9. Stochastic pane shows %K and %D lines with 20/80 reference lines
10. Minimum 80px pane height enforced during resize
11. Pane layout (which panes are active and their sizes) persists across page navigation (stored in Zustand)
12. Drawing tools from Stage 2b still work on the main price pane
13. All existing chart functionality intact (live candle updates, overlay indicators, comparison mode, replay bars, chart type toggle, timeframe switching)
14. Double-clicking a divider resets adjacent panes to default sizes
15. Maximum of 3 oscillator panes enforced
16. Volume pane is a separate chart instance (not overlaid on the price chart)
17. Only the bottom-most pane shows the time axis
18. Indicator parameter changes via pane header settings update the indicator data in real time

## Important Notes
- Do NOT break existing functionality. This is additive. All Stage 1, 2a, and 2b features must continue working.
- Each pane is a SEPARATE `lightweight-charts` `createChart()` instance. Do not try to use sub-charts or multiple price scales on a single chart for different panes.
- **Crosshair sync must not create infinite loops.** When chart A's crosshair event triggers an update on chart B, chart B's resulting crosshair event must NOT re-trigger chart A. Use a `syncing` boolean ref guard.
- **Time scale sync must not create infinite loops.** Same guard pattern as crosshair sync.
- The drawing canvas overlay (Stage 2b) only applies to the main price pane. It does not interact with volume or oscillator panes.
- The time axis (`timeScale`) should only be visible on the bottom-most pane in the stack. All other panes hide it (`timeScale: { visible: false }`). When the bottom pane is removed, the new bottom pane should show the time axis.
- Keep the terminal dark theme consistent for all new UI components. Use existing terminal color tokens (`terminal-surface`, `terminal-border`, `terminal-ghost`, `terminal-text`, `terminal-blue`, `terminal-green`, `terminal-red`, etc.).
- When adding or removing oscillator panes, redistribute heights proportionally. For example, if adding an RSI pane to a layout of Price(70%)+Volume(30%), adjust to Price(55%)+Volume(12%)+RSI(33%) or similar reasonable defaults.
- Test everything with `SIM_MODE=true` and `MOCK_MODE=true` (no IBKR needed).
- The `lightweight-charts` API reference for crosshair sync: `chart.subscribeCrosshairMove(handler)` returns a subscription. `series.setCrosshairPosition(price, time)` or use the chart's crosshair methods. For time scale: `chart.timeScale().subscribeVisibleLogicalRangeChange(handler)` and `chart.timeScale().setVisibleLogicalRange(range)`.
