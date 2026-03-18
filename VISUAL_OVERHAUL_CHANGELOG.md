# Visual Overhaul — Cream Theme (StockTaper-Inspired)
**Date:** 2026-03-05

## Summary
Complete replacement of the dark glassmorphism UI (navy #0b0f1a + blur effects) with a clean, professional, cream-toned design inspired by StockTaper.com.

---

## Phase 1: Design System Foundation

### `dashboard/tailwind.config.ts`
- Removed `darkMode: 'class'`
- Replaced ALL terminal.* colors with warm cream palette:
  - bg: `#0b0f1a` → `#FAF8F5` (warm cream)
  - surface: `#111827` → `#FFFFFF` (pure white)
  - elevated: `#1e293b` → `#F5F3F0` (slightly darker cream)
  - border: `rgba(148,163,184,0.12)` → `#E8E4DF` (warm gray)
  - muted: `#1e293b` → `#F0EDE8`
  - input: `#0f172a` → `#FFFFFF`
  - text: `#f1f5f9` → `#1A1A2E` (near-black)
  - dim: `#94a3b8` → `#6B7280` (medium gray)
  - ghost: `#475569` → `#9CA3AF` (light gray)
  - green: `#10b981` → `#16A34A` (green-600)
  - red: `#ef4444` → `#DC2626` (red-600)
  - blue: `#6366f1` → `#4F46E5` (indigo-600)
  - amber: `#f59e0b` → `#D97706` (amber-600)
  - purple: `#a78bfa` → `#7C3AED` (purple-600)
  - chart.up/down/grid/crosshair updated to cream equivalents
- Replaced shadow-glass/shadow-terminal with shadow-card variants (subtle, no blur)
- Updated keyframe colors (tickUp/tickDown use dark text base `#1A1A2E`)

### `dashboard/src/index.css`
- `html, body, #root` → `background: #FAF8F5; color: #1A1A2E`
- `.glass` / `.card` → white bg, `#E8E4DF` border, 8px radius, no blur
- `.glass-elevated` / `.card-elevated` → cream bg `#F5F3F0`
- `.gradient-text` → solid dark text `#1A1A2E`, bold
- `.gradient-surface` → `#FAFAF8`
- Scrollbar thumb: `rgba(0,0,0,0.12)` (was `rgba(148,163,184,0.15)`)
- `.chart-fullscreen` bg: `#FAF8F5`
- Added `.table-editorial` for dotted border table rows
- Card border-radius reduced from 12px to 8px

---

## Phase 2: Mass Class Replacement (~75 files)

### Patterns replaced across ALL component and page files:

| Old Pattern | New Pattern |
|---|---|
| `glass` (class) | `card` |
| `glass-elevated` | `card-elevated` |
| `shadow-glass-lg` | `shadow-card-lg` |
| `shadow-glass` | `shadow-card` |
| `bg-terminal-bg` | `bg-[#FAF8F5]` |
| `bg-terminal-surface` | `bg-white` |
| `bg-terminal-elevated` | `bg-gray-50` |
| `bg-terminal-input` | `bg-white` |
| `bg-terminal-muted` | `bg-gray-100` |
| `text-terminal-text` | `text-gray-800` |
| `text-terminal-dim` | `text-gray-500` |
| `text-terminal-ghost` | `text-gray-400` |
| `text-terminal-green` | `text-green-600` |
| `text-terminal-red` | `text-red-600` |
| `text-terminal-amber` | `text-amber-600` |
| `text-terminal-blue` | `text-indigo-600` |
| `text-terminal-purple` | `text-purple-600` |
| `border-terminal-border` | `border-gray-200` |
| `border-terminal-red` | `border-red-300` |
| `bg-terminal-green` | `bg-green-600` |
| `bg-terminal-red` | `bg-red-600` |
| `bg-terminal-amber` | `bg-amber-600` |
| `bg-terminal-ghost` | `bg-gray-400` |
| `bg-white/[0.015]` | `bg-gray-50/60` |
| `bg-white/[0.02]` | `bg-gray-50/70` |
| `bg-white/[0.03]` | `bg-gray-50` |
| `bg-white/[0.04]` | `bg-gray-100/60` |
| `bg-white/[0.06]` | `bg-gray-100` |
| `bg-white/[0.08]` | `bg-gray-100` |
| `bg-white/[0.1]` | `bg-gray-100` |
| `border-white/[0.04]` | `border-gray-100` |
| `border-white/[0.06]` | `border-gray-200` |
| `border-white/[0.08]` | `border-gray-200` |
| `gradient-text` (class) | `text-indigo-600 font-bold` |
| `indigo-500/10` | `indigo-50` |
| `indigo-500/20` | `indigo-100` |
| `text-indigo-400` | `text-indigo-600` |
| `text-indigo-300` | `text-indigo-600` |
| `ring-indigo-500/40` | `ring-indigo-300` |
| `from-indigo-500` | `from-indigo-600` |
| `to-purple-500` | `to-purple-600` |
| `border-l-indigo-500` | `border-l-indigo-600` |

### Files modified by category:

**Layout (3 files):**
- `Header.tsx`, `Sidebar.tsx`, `Layout.tsx`

**Pages (9 files):**
- `Dashboard.tsx`, `MarketPage.tsx`, `StockProfilePage.tsx`, `BacktestPage.tsx`, `ScreenerPage.tsx`, `SettingsPage.tsx`, `AlertsPage.tsx`, `SimulationPage.tsx`, `TradeBotPage.tsx`

**Stock Profile (15 files):**
- `HeroModule.tsx`, `KeyStatsStrip.tsx`, `SectionNav.tsx`, `FreshnessTag.tsx`, `FinancialStatementsModule.tsx`, `FinancialHealthModule.tsx`, `CompanyOverviewModule.tsx`, `NarrativeModule.tsx`, `RatingScorecardModule.tsx`, `AnalystSentimentModule.tsx`, `AnalystDetailModule.tsx`, `PriceTargetsModule.tsx`, `OwnershipModule.tsx`, `EventsModule.tsx`, `StockSplitsModule.tsx`

**Alert components (4 files):**
- `AlertBell.tsx`, `AlertForm.tsx`, `AlertHistoryTable.tsx`, `AlertList.tsx`

**Backtest components (5 files):**
- `BacktestParams.tsx`, `BacktestTradeLog.tsx`, `MetricsPanel.tsx`, `StrategyBuilder.tsx`, `EquityCurve.tsx`

**Chart components (7 files):**
- `ChartToolbar.tsx`, `DrawingCanvas.tsx`, `DrawingTools.tsx`, `IndicatorPanel.tsx`, `ResizeHandle.tsx`, `TradingChart.tsx`, `VolumePanel.tsx`

**Screener components (4 files):**
- `FilterBuilder.tsx`, `PresetSelector.tsx`, `ScanResultsTable.tsx`, `UniverseSelector.tsx`

**Ticker components (2 files):**
- `TickerCard.tsx`, `WatchlistGrid.tsx`

**TradeBot components (3 files):**
- `KPICard.tsx`, `BotToggle.tsx`, `PositionsTable.tsx`

**UI components (3 files):**
- `ErrorBoundary.tsx`, `Skeleton.tsx`, `ToastProvider.tsx`

**Insights/Diagnostics (11 files):**
- `OpportunityBoard.tsx`, `OverallSummaryCard.tsx`, `SystemOverviewWidget.tsx`, `DowTheoryWidget.tsx`, `SectorDivergenceWidget.tsx`, `AASWidget.tsx`, `IndicatorCardGrid.tsx`, `BubbleMarketMap.tsx`, `SectorProjectionsPanel.tsx`, `NewsStrip.tsx`, `DiagnosticHeaderRow.tsx`

**Other (2 files):**
- `IndicatorSelector.tsx`, `SimController.tsx`

---

## Phase 3: Design Language Refinement

### `Header.tsx` — Full redesign
- Removed colored pill badges (IBKR red/green, BOT green) → simple dot + monospace text
- Search bar: rectangular with cream bg `#FAF8F5`, not rounded-full
- Page title: monospace font, dark `text-gray-900`, no indigo gradient
- Clock: simpler, less prominent
- Removed all `shadow-[0_0_8px_...]` glow effects
- Removed all `animate-pulse` on badges

### `Sidebar.tsx` — Full redesign
- Logo: plain dark SVG icon + monospace "TRADEBOT" text, no gradient glow square
- Removed status pill cards (IBKR/BOT/SIM colored boxes) entirely
- Nav items: simple gray text + icon, `bg-gray-100` active state
- Removed indigo glow, shadow, scale animations from nav
- Watchlist: tighter spacing, 1px dots, monospace prices
- Reduced width from 220px to 200px

### `TickerCard.tsx` — Full redesign
- Removed colored left border (`border-l-2 border-l-green-600`)
- Removed hover:scale animation
- "View Profile" changed from indigo pill to simple bordered text button
- Range bars: thin 1px lines instead of thick colored bars with dots
- Added dotted border separator before metrics (editorial style)
- Simpler, more monospace-driven typography
- Rounded from `rounded-2xl` to `rounded-lg`

### `WatchlistGrid.tsx` — Partial redesign
- Active tab: solid dark fill (`bg-gray-900 text-white`) instead of indigo glow
- Sort buttons: neutral gray borders, no indigo highlights
- Cleaner header with less chrome
- Removed `shadow-[0_0_12px_...]` glow from active states

### `Dashboard.tsx` — Chart container redesign
- Removed glassmorphism gradient border (the `backgroundImage` with linear gradients)
- Plain white card with `border-[#E8E4DF]` border
- Chart type shown in bordered monospace tag, not colored pill
- Compare button: neutral bordered, not colored fill
- Added `clsx` import for conditional classes

### `EquityCurve.tsx` — Chart theme update
- Background: `#0b0f1a` → `#FFFFFF`
- Text color: `#94a3b8` → `#6B7280`
- Grid lines: `#1e293b` → `#F0EDE8`
- Border: `rgba(148,163,184,0.12)` → `#E8E4DF`
- Strategy line: `#6366f1` → `#4F46E5`
- Buy/hold line: `#475569` → `#9CA3AF`
- Buy markers: `#10b981` → `#16A34A`
- Sell markers: `#ef4444` → `#DC2626`

---

## Quality Gates
- `npm run typecheck` — PASS (0 errors)
- `npm run build` — PASS (builds in ~2s)
- CSS bundle: 63.5 KB (down from 72.8 KB)
- JS bundle: 762.7 KB (down from 773.4 KB)

---

## What Was NOT Changed
- Backend code (no Python changes)
- Store logic / state management
- API services
- Router / App.tsx structure
- Types / interfaces
- Test files
- Font choices (Inter + JetBrains Mono kept)
