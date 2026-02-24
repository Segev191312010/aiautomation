# Stage 2b Session Prompt: Drawing Tools

You are working on a trading platform built with **FastAPI** (backend) and **React 18 + TypeScript + Zustand + TailwindCSS** (dashboard). The project is at `C:\Users\segev\sdvesdaW\trading`.

## Current State
- **Backend** (`backend/`): FastAPI with 40+ endpoints, IBKR integration (ib_insync), 8 technical indicators (RSI, SMA, EMA, MACD, BBANDS, ATR, STOCH, PRICE), rule engine with AND/OR logic + cooldown, order execution, virtual trading simulation, historical replay, mock GBM data, real-time WebSocket, SQLite persistence. Auth scaffold with `users` table, JWT tokens, demo user. Settings system (`GET/PUT /api/settings`) storing user preferences as JSON blob.
- **Dashboard** (`dashboard/`): React 18 + Vite + Zustand + TailwindCSS. Pages: Dashboard, TradeBotPage, MarketPage, SimulationPage, SettingsPage. Uses `lightweight-charts@4.2.0` for candlesticks. Dark terminal theme. Watchlist grid, comparison overlay. Toast notification system (`ToastProvider` + `useToast`). Error boundaries. Loading skeletons.
- **Stage 2a Complete**: Chart toolbar with timeframe buttons and chart type toggle (candle/line/area/baseline). Volume histogram pane below main chart. Indicator quick-add dropdown. Chart annotations API for buy/sell markers. `TradingChart.tsx` refactored with toolbar + pane manager architecture.
- **Database**: SQLite with tables: `users` (id, email, password_hash, created_at, settings JSON), `rules`, `trades`, `sim_account`, `sim_positions`, `sim_orders` — all with `user_id` column.
- **Key dependencies**: `lightweight-charts@4.2.0`, `zustand@4.5.5`, `react@18.3.1`, `clsx@2.1.1`, `date-fns@3.6.0`, `tailwindcss@3.4.10`.
- **3 operating modes**: IBKR Live, IBKR Paper, Simulation (offline with mock data).

## What to Build (Stage 2b)

### 1. Drawing Types Definition (`dashboard/src/types/drawing.ts`)

Create the type definitions for the drawing system:
- `DrawingType` — union type: `'horizontal_line' | 'trendline' | 'fibonacci'`
- `DrawingPoint` — `{ time: number; price: number }` (time is Unix seconds, price is the price-axis value)
- `Drawing` — `{ id: string; type: DrawingType; symbol: string; color: string; points: DrawingPoint[]; visible: boolean; extended?: boolean }` where `points` has 1 element for horizontal line, 2 for trendline and fibonacci
- `DrawingToolState` — `{ activeTool: DrawingType | null; selectedDrawingId: string | null; drawingColor: string }`
- Export all types and re-export from `dashboard/src/types/index.ts`

### 2. Drawing Engine Utility (`dashboard/src/utils/drawingEngine.ts`)

Create a pure utility module (no React, no DOM) with all drawing math:

**Fibonacci levels constant:**
- `FIB_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]` with corresponding labels `['0%', '23.6%', '38.2%', '50%', '61.8%', '78.6%', '100%']`

**Coordinate transform helpers** (these take chart/series API references):
- `priceToY(series, price) -> number | null` — wraps `series.priceToCoordinate(price)`
- `yToPrice(series, y) -> number | null` — wraps `series.coordinateToPrice(y)`
- `timeToX(chart, time) -> number | null` — wraps `chart.timeScale().timeToCoordinate(time)`
- `xToTime(chart, x) -> number | null` — wraps `chart.timeScale().coordinateToTime(x)`, returns Unix seconds

**Hit testing functions** (for detecting mouse hover/click on drawings):
- `hitTestHorizontalLine(mouseY: number, lineY: number, threshold?: number) -> boolean` — threshold defaults to 5px
- `hitTestTrendline(mouseX: number, mouseY: number, x1: number, y1: number, x2: number, y2: number, threshold?: number) -> boolean` — point-to-line-segment distance check
- `hitTestFibonacci(mouseY: number, fibLevelYs: number[], threshold?: number) -> boolean` — check proximity to any fib level line

**Fibonacci calculation:**
- `calcFibLevels(highPrice: number, lowPrice: number) -> { level: number; label: string; price: number }[]` — given high and low price, returns the price at each Fibonacci level

**Line math:**
- `pointToLineDistance(px: number, py: number, x1: number, y1: number, x2: number, y2: number) -> number` — perpendicular distance from point to line segment

### 3. Drawing Canvas Overlay (`dashboard/src/components/chart/DrawingCanvas.tsx`)

**CRITICAL ARCHITECTURE**: `lightweight-charts@4.2.0` has NO native drawing/plugin system. You MUST use an HTML5 Canvas element positioned absolutely on top of the chart container.

**Component Props:**
```typescript
interface DrawingCanvasProps {
  chartApi: IChartApi | null
  seriesApi: ISeriesApi<'Candlestick'> | null
  symbol: string
  containerRef: React.RefObject<HTMLDivElement>  // the chart container div for sizing
}
```

**Canvas positioning:**
- The `<canvas>` element is positioned `absolute`, `inset-0`, matching the chart container dimensions
- `pointer-events: none` by default — allows chart pan/zoom to work normally underneath
- When a drawing tool is active (`activeTool !== null` in store), switch to `pointer-events: auto` to capture mouse events for drawing
- Use a `ResizeObserver` on the container to keep canvas dimensions in sync

**Mouse event handling (when a tool is active):**
- **Horizontal Line tool**: `mousedown` on canvas → read Y coordinate → convert to price via `series.coordinateToPrice(y)` → create Drawing with one point `{time: 0, price}` (time=0 signals it spans full width) → add to store → deselect tool
- **Trendline tool**: `mousedown` → record start point `{time, price}` (convert X→time via `chart.timeScale().coordinateToTime(x)`, Y→price) → `mousemove` → draw preview rubber-band line → `mouseup` → record end point → create Drawing with two points → add to store → deselect tool
- **Fibonacci tool**: `mousedown` → record first point (high or low) → `mousemove` → draw preview fib levels → `mouseup` → record second point → create Drawing with two points → add to store → deselect tool

**Rendering (on every frame / viewport change):**
- Clear the entire canvas
- For each visible drawing belonging to the current symbol:
  - Convert drawing `points` (time, price) back to pixel coordinates using `timeToCoordinate` / `priceToCoordinate`
  - **Horizontal Line**: draw a dashed line (`setLineDash([6, 4])`) across the full canvas width at the price's Y coordinate. Draw a price label box on the right edge (background rectangle + white text showing the price value).
  - **Trendline**: draw a solid line between the two pixel points. If `extended` is true, extend the line beyond the endpoints to canvas edges.
  - **Fibonacci**: compute fib level prices from the two points. Draw a horizontal line at each level with the level label (e.g., "38.2%") and price on the right side. Fill translucent color bands between adjacent levels.
- Highlight the hovered drawing (thicker line, brighter color)
- Draw selection handles (small squares at endpoints) for the selected drawing

**Viewport sync — redraw on chart changes:**
- Subscribe to `chart.timeScale().subscribeVisibleTimeRangeChange()` — redraw when user scrolls/zooms
- Subscribe to `chart.subscribeCrosshairMove()` — use for hover hit-testing (check if mouse is near a drawing)
- On container resize (ResizeObserver) — resize canvas and redraw

**Interaction (when NO tool is active, pointer-events is `none` EXCEPT we need hover detection):**
- Use `chart.subscribeCrosshairMove(param)` to get mouse position even when pointer-events is `none` on the canvas. The crosshair move event provides `param.point.x` and `param.point.y` in pixel coordinates — use these for hit-testing against drawings.
- When a drawing is detected under the crosshair: set `hoveredDrawingId` in local state, switch canvas pointer-events to `auto` briefly if needed for click-to-select
- When the user clicks on a hovered drawing: set `selectedDrawingId` in store

**Hover/Select interaction detail:**
- When hovering over a drawing, show a small delete button (X icon) near the drawing
- When a drawing is selected, show draggable handles at its control points
- Drag handles to adjust drawing position (update the point's time/price in store)
- Right-click on a drawing: show a context menu with options: Delete, Change Color (submenu), Toggle Extend (trendline only)

**Escape key:**
- If a tool is active → deselect the tool (set `activeTool = null`)
- If a drawing is selected → deselect it (set `selectedDrawingId = null`)

### 4. Drawing Tools Toolbar (`dashboard/src/components/chart/DrawingTools.tsx`)

A toolbar component placed in the MarketPage toolbar area:

**Tool buttons** (icon + label, arranged horizontally):
- Horizontal Line (icon: horizontal rule / minus icon)
- Trendline (icon: diagonal line / trending-up icon)
- Fibonacci Retracement (icon: stacked horizontal lines or fib-spiral icon)
- Each button highlights (active state with `bg-terminal-blue/20 border-terminal-blue/50 text-terminal-blue`) when its tool is selected
- Clicking an already-selected tool deselects it

**Color picker:**
- A small color swatch button that opens a dropdown with preset colors: `#3b82f6` (blue), `#22c55e` (green), `#ef4444` (red), `#f59e0b` (amber), `#a855f7` (purple), `#ec4899` (pink), `#06b6d4` (cyan), `#ffffff` (white)
- Selected color shows a check mark or ring indicator
- Default color: `#3b82f6` (terminal blue)

**Action buttons:**
- Delete Selected — enabled only when a drawing is selected. Removes the selected drawing from store.
- Clear All — clears all drawings for the current symbol (with confirmation: use a simple "Are you sure?" or just do it with a toast "Cleared N drawings")

**Layout:** Styled consistently with the existing toolbar rows using terminal theme. Small, compact buttons matching the timeframe selector style (`text-[11px] font-mono`).

### 5. Drawing State Management (Zustand)

**Extend `dashboard/src/store/index.ts`** — add a new `useDrawingStore`:

```typescript
interface DrawingState {
  // Per-symbol drawings
  drawings: Record<string, Drawing[]>  // keyed by symbol

  // Tool state
  activeTool: DrawingType | null
  selectedDrawingId: string | null
  drawingColor: string

  // Actions
  setActiveTool: (tool: DrawingType | null) => void
  setSelectedDrawingId: (id: string | null) => void
  setDrawingColor: (color: string) => void
  addDrawing: (drawing: Drawing) => void
  updateDrawing: (id: string, updates: Partial<Drawing>) => void
  removeDrawing: (symbol: string, id: string) => void
  clearDrawings: (symbol: string) => void
  setDrawings: (symbol: string, drawings: Drawing[]) => void
  loadDrawingsFromSettings: (settings: Record<string, Drawing[]>) => void
}
```

- `drawings` is keyed by symbol so switching symbols shows only that symbol's drawings
- `addDrawing` generates a UUID via `crypto.randomUUID()` and appends to the correct symbol array
- After any mutation (add/update/remove/clear), trigger a debounced save to backend via `PUT /api/settings` with `{ drawings: drawingsState }` — debounce 2 seconds to avoid spamming on rapid edits
- On app startup / symbol change, load drawings from `GET /api/settings` response's `drawings` field

### 6. Integration into TradingChart (`dashboard/src/components/chart/TradingChart.tsx`)

**Expose chart and series references to DrawingCanvas:**
- The `TradingChart` component already has `chartRef` and `candleRef` (ISeriesApi). These need to be accessible by `DrawingCanvas`.
- Option A (recommended): Have `TradingChart` accept a callback prop like `onChartReady?: (chart: IChartApi, series: ISeriesApi<'Candlestick'>) => void` that fires after chart initialization
- Option B: Use `useImperativeHandle` + `forwardRef` to expose chart/series
- The `DrawingCanvas` is rendered as a sibling/child positioned absolutely within the same container as the chart

**Container structure after integration:**
```tsx
<div className="relative w-full h-full">
  <div ref={containerRef} className="w-full h-full" />  {/* lightweight-charts */}
  <DrawingCanvas
    chartApi={chartApi}
    seriesApi={seriesApi}
    symbol={symbol}
    containerRef={containerRef}
  />
</div>
```

### 7. Integration into MarketPage (`dashboard/src/pages/MarketPage.tsx`)

**Add DrawingTools to the toolbar area:**
- Add a new toolbar row (row 3) below the indicator selector row, or integrate into row 2 alongside the indicator selector
- Import and render `<DrawingTools />` component
- The DrawingTools component reads/writes to `useDrawingStore` internally

**Load drawings on mount / symbol change:**
- On initial mount, fetch settings via `GET /api/settings` and load drawings into `useDrawingStore`
- When `selectedSymbol` changes, the DrawingCanvas automatically filters to show only that symbol's drawings (already handled by the store being keyed by symbol)

### 8. API Integration (`dashboard/src/services/api.ts`)

**Add settings-based drawing persistence:**
- `saveDrawings(drawings: Record<string, Drawing[]>) -> Promise<void>` — calls `PUT /api/settings` with `{ drawings }` as part of the settings JSON blob
- `loadDrawings() -> Promise<Record<string, Drawing[]>>` — calls `GET /api/settings` and extracts the `drawings` field (returns `{}` if not present)
- These use the existing settings endpoint — drawings are stored as a nested object inside the user's settings JSON

## Files to Create
- `dashboard/src/types/drawing.ts` — Drawing, DrawingType, DrawingPoint, DrawingToolState types
- `dashboard/src/utils/drawingEngine.ts` — coordinate transforms, hit testing, Fibonacci calculations, line math
- `dashboard/src/components/chart/DrawingCanvas.tsx` — HTML5 Canvas overlay for rendering and interacting with drawings
- `dashboard/src/components/chart/DrawingTools.tsx` — toolbar UI with tool buttons, color picker, delete/clear actions

## Files to Modify
- `dashboard/src/types/index.ts` — re-export all types from `drawing.ts`
- `dashboard/src/store/index.ts` — add `useDrawingStore` with per-symbol drawings, active tool, selected drawing, color state, and debounced persistence
- `dashboard/src/components/chart/TradingChart.tsx` — expose chart/series references via callback prop, render DrawingCanvas overlay inside the chart container
- `dashboard/src/pages/MarketPage.tsx` — add DrawingTools toolbar, load drawings from settings on mount
- `dashboard/src/services/api.ts` — add `saveDrawings()` and `loadDrawings()` functions using the existing settings API

## Definition of Done
1. User can select the Horizontal Line tool, click on the chart, and a dashed horizontal line appears at that price level with a price label on the right edge
2. User can select the Trendline tool, click-drag on the chart, and a line appears between the two clicked points
3. User can select the Fibonacci Retracement tool, click-drag on the chart, and horizontal lines appear at 0%, 23.6%, 38.2%, 50%, 61.8%, 78.6%, 100% levels with labels and price values
4. Drawings persist when switching symbols and coming back (stored per-symbol in Zustand)
5. Drawings persist across page reloads (saved to `PUT /api/settings` and loaded from `GET /api/settings`)
6. User can hover over a drawing to highlight it (thicker/brighter rendering)
7. User can delete a drawing via the hover delete button or right-click context menu
8. User can change drawing color via the color picker before drawing, or via right-click context menu on existing drawings
9. Drawings redraw correctly when the chart is scrolled or zoomed (subscribed to `subscribeVisibleTimeRangeChange`)
10. Chart pan/zoom still works normally when no drawing tool is active (`pointer-events: none` on canvas)
11. Escape key deselects the active drawing tool and deselects any selected drawing
12. All existing chart functionality still works: candlestick/line/area/baseline chart types, volume pane, overlay indicators (SMA, EMA, BB, VWAP), comparison overlay, live WebSocket candle updates, replay bar injection

## Important Notes
- `lightweight-charts@4.2.0` has NO native plugin or drawing system — you MUST use the HTML5 Canvas overlay approach described above. Do NOT try to use any `createPlugin` or `attachPrimitive` API — it does not exist in this version.
- The canvas overlay MUST use `pointer-events: none` when no tool is active so that chart pan/zoom works normally. Only switch to `pointer-events: auto` when the user has selected a drawing tool.
- For crosshair-based hover detection (when pointer-events is `none`), use `chart.subscribeCrosshairMove()` which provides mouse coordinates regardless of canvas pointer-events state.
- Use the existing terminal dark theme colors for all UI: `terminal-blue` (#3b82f6), `terminal-green` (#00e07a), `terminal-border` (#1c2e4a), `terminal-surface` (#0a1220), `terminal-ghost` (#3d5a80), `terminal-dim` (#5f7a9d), `terminal-text` (#c9d6e3).
- Use the Stage 1 toast system (`useToast`) for user feedback (e.g., "Drawing deleted", "Cleared 5 drawings", errors).
- Keep the drawing engine math (`drawingEngine.ts`) as a pure utility with no React or DOM dependencies — this makes it testable and reusable.
- The debounced save to settings API should wait 2 seconds after the last drawing mutation before saving, to avoid excessive API calls during rapid drawing/editing.
- Performance: only redraw the canvas on actual viewport changes (time range change, resize, crosshair move for hover). Do NOT use `requestAnimationFrame` loops — use event-driven redraws only.
- Generate drawing IDs with `crypto.randomUUID()`.
- Do NOT install any new npm packages. The canvas overlay uses the native HTML5 Canvas API and all existing dependencies.
- Do NOT break existing functionality. This is additive.
- Test everything with `SIM_MODE=true` and `MOCK_MODE=true` (no IBKR needed).
