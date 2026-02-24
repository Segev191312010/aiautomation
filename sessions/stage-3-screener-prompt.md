# Stage 3 Session Prompt: Stock Screener & Scanner

You are working on a trading platform built with **FastAPI** (backend) and **React 18 + TypeScript + Zustand + TailwindCSS** (dashboard). The project is at `C:\Users\segev\sdvesdaW\trading`.

## Current State
- **Backend** (`backend/`): FastAPI with 40+ endpoints, IBKR integration (ib_insync), 8 technical indicators (RSI, SMA, EMA, MACD, BBANDS, ATR, STOCH, PRICE) in `indicators.py` with a `calculate(df, indicator, params)` function, rule engine with AND/OR logic + cooldown, order execution, virtual trading simulation, historical replay, mock GBM data, real-time WebSocket, SQLite persistence via aiosqlite, Yahoo Finance bars/quotes endpoints (`_yf_bars`, `_yf_quotes`), cross detection (`detect_cross`).
- **Dashboard** (`dashboard/`): React 18 + Vite + Zustand + TailwindCSS. 6 pages: Dashboard, TradeBotPage, MarketPage, SimulationPage, RulesPage (placeholder), SettingsPage (placeholder). Uses lightweight-charts for candlesticks. Dark terminal theme. Watchlist grid, comparison overlay. Sidebar navigation with route-based page switching via `useUIStore.activeRoute`.
- **Database**: SQLite with tables: `rules` (id, data JSON), `trades` (id, rule_id, symbol, action, timestamp, data JSON).
- **3 operating modes**: IBKR Live, IBKR Paper, Simulation (offline with mock data).
- **Stages 1, 2a, 2b, 2c are complete**: Auth scaffold, toast notifications, error boundary, settings system, advanced charting (volume pane, toolbar, drawing tools, multi-pane layout, crosshair sync).

## What to Build (Stage 3)

### 1. Static Universe JSON Files

**Create `backend/data/sp500.json`:**
- JSON array of S&P 500 ticker symbols (~500 strings), e.g. `["AAPL", "MSFT", "AMZN", ...]`
- This is a static file — yfinance is unreliable for fetching constituent lists dynamically
- Include all current S&P 500 members

**Create `backend/data/nasdaq100.json`:**
- JSON array of NASDAQ 100 ticker symbols (~100 strings)

**Create `backend/data/etfs.json`:**
- JSON array of common ETF symbols (~50), e.g. `["SPY", "QQQ", "IWM", "DIA", "XLF", "XLK", "XLE", "XLV", "GLD", "SLV", "TLT", "HYG", "VTI", "VOO", "ARKK", ...]`

### 2. Screener Engine (`backend/screener.py`)

**Create `backend/screener.py`:**

```python
# Core architecture:

# 1. Universe loading
load_universe(name: str) -> list[str]
# Reads from backend/data/{name}.json
# Supports: "sp500", "nasdaq100", "etfs", "custom" (custom passes symbols directly)

# 2. Server-side bar data cache
_bar_cache: dict[str, dict]  # {symbol: {"df": pd.DataFrame, "fetched_at": float}}
CACHE_TTL = 900  # 15 minutes

get_cached_bars(symbol: str) -> pd.DataFrame | None
# Returns cached DataFrame if fresh (< 15 min old), else None

refresh_cache(symbols: list[str]) -> None
# Batch download using yf.download(tickers=symbols, period="3mo", interval="1d")
# yf.download returns a multi-index DataFrame when given multiple tickers
# Parse into individual DataFrames, normalize column names to lowercase (open, high, low, close, volume)
# Store each in _bar_cache with current timestamp
# Throttle: process in batches of 50 symbols with 2-second delay between batches
# Log warnings on failures, don't raise — skip symbols that fail

# 3. Indicator computation for scan
compute_scan_indicators(df: pd.DataFrame, filters: list[dict]) -> dict
# For each filter, call indicators.calculate(df, indicator, params)
# Return dict of {indicator_key: latest_value}
# Example: {"rsi_14": 28.5, "sma_50": 185.3, "volume": 45000000}

# 4. Filter evaluation
evaluate_filters(computed: dict, filters: list[dict], df: pd.DataFrame) -> bool
# For each filter: extract computed value, apply operator (>, <, >=, <=, crosses_above, crosses_below)
# For crosses_above/crosses_below: use indicators.detect_cross() on the full series
# All filters combined with AND logic
# Return True if symbol passes all filters

# 5. Main scan function
async def run_scan(
    universe: str,           # "sp500" | "nasdaq100" | "etfs" | "custom"
    symbols: list[str] | None,  # only used if universe == "custom"
    filters: list[dict],     # [{indicator, params, operator, value}]
    sort_by: str | None,     # column to sort results by
    sort_dir: str = "desc",  # "asc" | "desc"
    limit: int = 100,        # max results
) -> list[dict]
# 1. Load universe symbols
# 2. Refresh cache for any symbols not cached or stale
# 3. For each symbol: compute indicators, evaluate filters
# 4. Fetch basic quote info (price, change%, volume, market_cap, sector) from yfinance fast_info
# 5. Build result rows with all computed indicator values
# 6. Sort and limit results
# 7. Return list of result dicts
```

**Rate limiting and error handling:**
- Batch yfinance downloads: `yf.download(tickers=[list], period="3mo", interval="1d")` for bar data
- Process in batches of 50 symbols max per `yf.download()` call
- 2-second sleep between batches to respect rate limits
- Wrap all yfinance calls in try/except — log warnings, skip failed symbols
- Never let a single symbol failure crash the entire scan

**Quote info fetching:**
- After filtering, only fetch detailed quote info (name, sector, market_cap) for the MATCHING symbols (not the entire universe)
- Use `yf.Ticker(symbol).fast_info` or batch `yf.Tickers(symbols)` for efficiency
- Cache quote info alongside bar data

### 3. Screener Models (`backend/models.py`)

**Add to `backend/models.py`:**

```python
class ScanFilter(BaseModel):
    indicator: str           # RSI, SMA, EMA, MACD, BBANDS, ATR, STOCH, PRICE, VOLUME, CHANGE_PCT
    params: dict[str, Any] = Field(default_factory=dict)  # e.g. {"length": 14}
    operator: str            # ">", "<", ">=", "<=", "crosses_above", "crosses_below"
    value: float | str       # numeric threshold or indicator reference like "SMA_200"

class ScanRequest(BaseModel):
    universe: Literal["sp500", "nasdaq100", "etfs", "custom"]
    symbols: list[str] | None = None  # only for custom universe
    filters: list[ScanFilter]
    sort_by: str | None = None
    sort_dir: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=100, ge=1, le=500)

class ScanResultRow(BaseModel):
    symbol: str
    name: str | None = None
    price: float | None = None
    change_pct: float | None = None
    volume: int | None = None
    market_cap: float | None = None
    sector: str | None = None
    # Dynamic indicator values — populated based on requested filters
    rsi_14: float | None = None
    sma_20: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    ema_20: float | None = None
    macd: float | None = None
    atr_14: float | None = None
    stoch_k: float | None = None

class ScanPreset(BaseModel):
    id: str = Field(default_factory=lambda: str(_uuid.uuid4()))
    user_id: str = "demo"
    name: str
    universe: str
    filters: list[ScanFilter]
    is_builtin: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
```

### 4. Database Changes (`backend/database.py`)

**Add `screener_presets` table and CRUD:**

```python
# New table schema
_CREATE_SCREENER_PRESETS = """
CREATE TABLE IF NOT EXISTS screener_presets (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'demo',
    name TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# Add to init_db(): await db.execute(_CREATE_SCREENER_PRESETS)
# Also call _seed_screener_presets(db) to insert built-in presets on first run

# CRUD functions:
async def get_screener_presets(user_id: str = "demo") -> list[ScanPreset]
async def get_screener_preset(preset_id: str) -> ScanPreset | None
async def save_screener_preset(preset: ScanPreset) -> None
async def delete_screener_preset(preset_id: str) -> bool
```

**Built-in presets (seeded on startup):**
```python
_BUILTIN_SCREENER_PRESETS = [
    {
        "id": "preset-rsi-oversold",
        "name": "RSI Oversold",
        "universe": "sp500",
        "filters": [{"indicator": "RSI", "params": {"length": 14}, "operator": "<", "value": 30}],
        "is_builtin": True,
    },
    {
        "id": "preset-golden-cross",
        "name": "Golden Cross",
        "universe": "sp500",
        "filters": [{"indicator": "SMA", "params": {"length": 50}, "operator": "crosses_above", "value": "SMA_200"}],
        "is_builtin": True,
    },
    {
        "id": "preset-volume-breakout",
        "name": "Volume Breakout",
        "universe": "sp500",
        "filters": [{"indicator": "VOLUME", "params": {"length": 20}, "operator": ">", "value": 2.0}],
        "is_builtin": True,
    },
    {
        "id": "preset-52wk-high",
        "name": "52-Week High",
        "universe": "sp500",
        "filters": [{"indicator": "PRICE", "params": {}, "operator": ">", "value": "52WK_HIGH_95"}],
        "is_builtin": True,
    },
]
```

Note on special filter values:
- `VOLUME` filter with `value: 2.0` means "current volume > 2x 20-day average volume" — implement this special case in `evaluate_filters`
- `52WK_HIGH_95` means "price > 95% of 52-week high" — implement this as a special resolved value

### 5. Screener Endpoints (`backend/main.py`)

**Add to `backend/main.py`:**

```python
from screener import run_scan, load_universe

# ── Screener endpoints ─────────────────────────────────────────────────────

@app.post("/api/screener/scan")
async def screener_scan(body: ScanRequest):
    """Run a stock screener scan against a universe with indicator filters."""
    try:
        results = await run_scan(
            universe=body.universe,
            symbols=body.symbols,
            filters=[f.model_dump() for f in body.filters],
            sort_by=body.sort_by,
            sort_dir=body.sort_dir,
            limit=body.limit,
        )
        return {"results": results, "count": len(results)}
    except Exception as exc:
        log.warning("Screener scan failed: %s", exc)
        raise HTTPException(500, f"Scan failed: {str(exc)}")

@app.get("/api/screener/universes")
async def screener_universes():
    """Return available universes with symbol counts."""
    return [
        {"id": "sp500", "name": "S&P 500", "count": len(load_universe("sp500"))},
        {"id": "nasdaq100", "name": "NASDAQ 100", "count": len(load_universe("nasdaq100"))},
        {"id": "etfs", "name": "ETFs", "count": len(load_universe("etfs"))},
        {"id": "custom", "name": "Custom", "count": 0},
    ]

@app.get("/api/screener/presets")
async def screener_presets_list():
    """Return all screener presets (built-in + user-saved)."""
    presets = await get_screener_presets()
    return [p.model_dump() for p in presets]

@app.post("/api/screener/presets", status_code=201)
async def screener_presets_create(body: ScanPreset):
    """Save a new screener preset."""
    body.is_builtin = False  # user presets are never built-in
    await save_screener_preset(body)
    return body.model_dump()

@app.delete("/api/screener/presets/{preset_id}")
async def screener_presets_delete(preset_id: str):
    """Delete a screener preset (cannot delete built-in presets)."""
    preset = await get_screener_preset(preset_id)
    if not preset:
        raise HTTPException(404, "Preset not found")
    if preset.is_builtin:
        raise HTTPException(400, "Cannot delete built-in presets")
    ok = await delete_screener_preset(preset_id)
    if not ok:
        raise HTTPException(404, "Preset not found")
    return {"deleted": True}
```

### 6. Screener Types (`dashboard/src/types/index.ts`)

**Add to `dashboard/src/types/index.ts`:**

```typescript
// ── Screener ─────────────────────────────────────────────────────────────────

export type ScreenerIndicator = 'RSI' | 'SMA' | 'EMA' | 'MACD' | 'BBANDS' | 'ATR' | 'STOCH' | 'PRICE' | 'VOLUME' | 'CHANGE_PCT'
export type ScreenerOperator = '>' | '<' | '>=' | '<=' | 'crosses_above' | 'crosses_below'

export interface ScanFilter {
  indicator: ScreenerIndicator
  params: Record<string, number | string>
  operator: ScreenerOperator
  value: number | string
}

export interface ScanResultRow {
  symbol: string
  name?: string
  price?: number
  change_pct?: number
  volume?: number
  market_cap?: number
  sector?: string
  rsi_14?: number
  sma_20?: number
  sma_50?: number
  sma_200?: number
  ema_20?: number
  macd?: number
  atr_14?: number
  stoch_k?: number
}

export interface ScanPreset {
  id: string
  user_id: string
  name: string
  universe: string
  filters: ScanFilter[]
  is_builtin: boolean
  created_at: string
}

export interface UniverseInfo {
  id: string
  name: string
  count: number
}

export type ScreenerSortField = 'symbol' | 'price' | 'change_pct' | 'volume' | 'market_cap' | 'rsi_14' | 'sma_50' | 'sector'
```

**Update the `AppRoute` type:**
```typescript
export type AppRoute = 'dashboard' | 'tradebot' | 'market' | 'simulation' | 'rules' | 'settings' | 'screener'
```

### 7. Screener API Functions (`dashboard/src/services/api.ts`)

**Add to `dashboard/src/services/api.ts`:**

```typescript
// ── Screener ─────────────────────────────────────────────────────────────────

export const runScreenerScan = (body: {
  universe: string
  symbols?: string[]
  filters: ScanFilter[]
  sort_by?: string
  sort_dir?: 'asc' | 'desc'
  limit?: number
}) => post<{ results: ScanResultRow[]; count: number }>('/api/screener/scan', body)

export const fetchScreenerUniverses = () => get<UniverseInfo[]>('/api/screener/universes')

export const fetchScreenerPresets = () => get<ScanPreset[]>('/api/screener/presets')

export const saveScreenerPreset = (body: {
  name: string
  universe: string
  filters: ScanFilter[]
}) => post<ScanPreset>('/api/screener/presets', body)

export const deleteScreenerPreset = (id: string) => del<{ deleted: boolean }>(`/api/screener/presets/${id}`)
```

Add the necessary type imports at the top of `api.ts` from `@/types`:
```typescript
import type { ScanFilter, ScanResultRow, ScanPreset, UniverseInfo } from '@/types'
```

### 8. Screener Store (`dashboard/src/store/index.ts`)

**Add `useScreenerStore` to `dashboard/src/store/index.ts`:**

```typescript
// ── Screener store ───────────────────────────────────────────────────────────

interface ScreenerState {
  results:        ScanResultRow[]
  presets:        ScanPreset[]
  universes:      UniverseInfo[]
  filters:        ScanFilter[]
  selectedUniverse: string
  customSymbols:  string
  sortField:      ScreenerSortField
  sortDir:        SortDir
  scanning:       boolean
  presetsLoaded:  boolean

  setResults:          (r: ScanResultRow[]) => void
  setPresets:          (p: ScanPreset[]) => void
  setUniverses:        (u: UniverseInfo[]) => void
  setFilters:          (f: ScanFilter[]) => void
  addFilter:           () => void
  updateFilter:        (index: number, filter: ScanFilter) => void
  removeFilter:        (index: number) => void
  setSelectedUniverse: (u: string) => void
  setCustomSymbols:    (s: string) => void
  setSort:             (field: ScreenerSortField, dir: SortDir) => void
  setScanning:         (v: boolean) => void
  loadPreset:          (preset: ScanPreset) => void
  reset:               () => void
}

export const useScreenerStore = create<ScreenerState>((set) => ({
  results:           [],
  presets:           [],
  universes:         [],
  filters:           [{ indicator: 'RSI', params: { length: 14 }, operator: '<', value: 30 }],
  selectedUniverse:  'sp500',
  customSymbols:     '',
  sortField:         'change_pct',
  sortDir:           'desc',
  scanning:          false,
  presetsLoaded:     false,

  setResults:          (r) => set({ results: r }),
  setPresets:          (p) => set({ presets: p, presetsLoaded: true }),
  setUniverses:        (u) => set({ universes: u }),
  setFilters:          (f) => set({ filters: f }),
  addFilter:           () => set((s) => ({
    filters: [...s.filters, { indicator: 'RSI', params: { length: 14 }, operator: '<', value: 30 }],
  })),
  updateFilter:        (index, filter) => set((s) => ({
    filters: s.filters.map((f, i) => (i === index ? filter : f)),
  })),
  removeFilter:        (index) => set((s) => ({
    filters: s.filters.filter((_, i) => i !== index),
  })),
  setSelectedUniverse: (u) => set({ selectedUniverse: u }),
  setCustomSymbols:    (s) => set({ customSymbols: s }),
  setSort:             (field, dir) => set({ sortField: field, sortDir: dir }),
  setScanning:         (v) => set({ scanning: v }),
  loadPreset:          (preset) => set({
    selectedUniverse: preset.universe,
    filters: preset.filters,
  }),
  reset:               () => set({
    results: [],
    filters: [{ indicator: 'RSI', params: { length: 14 }, operator: '<', value: 30 }],
    selectedUniverse: 'sp500',
    customSymbols: '',
    scanning: false,
  }),
}))
```

Add the necessary type imports at the top of `store/index.ts`:
```typescript
import type { ScanFilter, ScanResultRow, ScanPreset, UniverseInfo, ScreenerSortField } from '@/types'
```

### 9. Universe Selector (`dashboard/src/components/screener/UniverseSelector.tsx`)

**Create `dashboard/src/components/screener/UniverseSelector.tsx`:**
- Horizontal button group or dropdown showing available universes: S&P 500, NASDAQ 100, ETFs, Custom
- Each button shows the universe name and symbol count (e.g. "S&P 500 (503)")
- Active universe is highlighted with `bg-terminal-blue/15 text-terminal-blue`
- When "Custom" is selected, show a text input below for comma-separated symbols
- Uses `useScreenerStore` for `selectedUniverse`, `setSelectedUniverse`, `customSymbols`, `setCustomSymbols`
- Fetches universes from `fetchScreenerUniverses()` on mount and stores in `useScreenerStore.universes`

### 10. Filter Builder (`dashboard/src/components/screener/FilterBuilder.tsx`)

**Create `dashboard/src/components/screener/FilterBuilder.tsx`:**

This component is the core of the screener UI and will be **reused in Stage 6 (Rule Builder)** — keep it generic.

- Renders a list of filter rows, each row is: `[Indicator dropdown] [Params inputs] [Operator dropdown] [Value input] [Remove button]`
- "Add Filter" button at the bottom adds a new row with default values

**Indicator dropdown options:**
| Indicator | Params shown | Default params |
|-----------|-------------|----------------|
| RSI | Length | `{length: 14}` |
| SMA | Length | `{length: 20}` |
| EMA | Length | `{length: 20}` |
| MACD | Fast, Slow, Signal | `{fast: 12, slow: 26, signal: 9}` |
| BBANDS | Length, Std, Band | `{length: 20, std: 2, band: "mid"}` |
| ATR | Length | `{length: 14}` |
| STOCH | K, D, Smooth K | `{k: 14, d: 3, smooth_k: 3}` |
| Price | (none) | `{}` |
| Volume | Avg Length | `{length: 20}` |
| Change % | (none) | `{}` |

**Operator dropdown options:** `>`, `<`, `>=`, `<=`, `crosses_above`, `crosses_below`

**Value input:** Numeric input field. For cross-type operators, also allow text like "SMA_200" for indicator references.

**Params inputs:** Shown dynamically based on indicator selection. E.g., RSI shows a "Length" number input (default 14), MACD shows "Fast", "Slow", "Signal" inputs.

**Styling:**
- Each row has a subtle border-bottom or gap
- Remove button (X icon or minus icon) on the right of each row
- Add button with plus icon, styled as `text-terminal-blue`
- All inputs use terminal theme: `bg-terminal-muted border-terminal-border text-terminal-text font-mono text-sm`

### 11. Preset Selector (`dashboard/src/components/screener/PresetSelector.tsx`)

**Create `dashboard/src/components/screener/PresetSelector.tsx`:**
- Dropdown/select showing all presets (built-in marked with a tag/badge, user presets plain)
- Selecting a preset calls `useScreenerStore.loadPreset(preset)` which populates universe + filters
- "Save Current" button opens a small inline form (just a name input + confirm)
- "Delete" button next to user presets (not shown for built-in presets)
- Fetches presets from `fetchScreenerPresets()` on mount
- Save calls `saveScreenerPreset({name, universe, filters})`
- Delete calls `deleteScreenerPreset(id)` then refreshes preset list
- Show toast on save success, delete success, or errors

### 12. Scan Results Table (`dashboard/src/components/screener/ScanResultsTable.tsx`)

**Create `dashboard/src/components/screener/ScanResultsTable.tsx`:**

- Sortable table with columns: Symbol, Name, Price, Change%, Volume, Market Cap, RSI(14), Sector
- Click column header to sort (toggle asc/desc). Show sort arrow indicator.
- Click a result row to navigate to Market page with that symbol:
  ```typescript
  const { setSelectedSymbol } = useMarketStore()
  const { setRoute } = useUIStore()
  // On row click:
  setSelectedSymbol(row.symbol)
  setRoute('market')
  ```
- Row count badge at top: "X results found" (green badge)
- Empty state when no results: centered message "No symbols match your filters" with a muted icon
- Loading state: show skeleton rows or spinner overlay while `scanning` is true

**Column formatting:**
- Symbol: bold, monospace, white text
- Name: truncated if long, muted text
- Price: right-aligned, 2 decimal places
- Change%: right-aligned, colored green (positive) / red (negative), with +/- prefix
- Volume: right-aligned, formatted with K/M/B suffix (e.g. "45.2M")
- Market Cap: right-aligned, formatted with B/T suffix (e.g. "2.8T")
- RSI(14): right-aligned, colored red if < 30, green if > 70, white otherwise
- Sector: left-aligned, muted text

**Table styling:**
- Terminal dark theme consistent with existing tables
- Alternating row backgrounds: `bg-terminal-surface` / `bg-terminal-muted/30`
- Hover: `bg-terminal-muted` with cursor pointer
- Fixed header row that doesn't scroll
- Scrollable body if results overflow

### 13. Screener Page (`dashboard/src/pages/ScreenerPage.tsx`)

**Create `dashboard/src/pages/ScreenerPage.tsx`:**

Layout (top to bottom):
1. **Page header**: "Stock Screener" title + brief description
2. **Universe Selector** (horizontal bar)
3. **Preset Selector** (dropdown + save/delete buttons, same row as or below universe selector)
4. **Filter Builder** (expandable section with filter rows)
5. **Scan Button**: Large primary button "Scan [Universe Name]" with loading spinner when scanning
   - Disabled when no filters are configured
   - On click: set `scanning = true`, call `runScreenerScan(...)`, set results, set `scanning = false`
   - Wrap in try/catch, show toast on error
   - Scan can take 10-30 seconds for large universes — button shows spinner + "Scanning..." text
6. **Results Table** (takes remaining space)

**On mount:**
- Fetch universes via `fetchScreenerUniverses()`
- Fetch presets via `fetchScreenerPresets()`

**Scan handler:**
```typescript
const handleScan = async () => {
  setScanning(true)
  try {
    const resp = await runScreenerScan({
      universe: selectedUniverse,
      symbols: selectedUniverse === 'custom' ? customSymbols.split(',').map(s => s.trim()).filter(Boolean) : undefined,
      filters,
      sort_by: sortField,
      sort_dir: sortDir,
      limit: 100,
    })
    setResults(resp.results)
    toast.success(`Found ${resp.count} matching symbols`)
  } catch (err) {
    toast.error(`Scan failed: ${err instanceof Error ? err.message : 'Unknown error'}`)
  } finally {
    setScanning(false)
  }
}
```

### 14. Sidebar Navigation (`dashboard/src/components/layout/Sidebar.tsx`)

**Modify `dashboard/src/components/layout/Sidebar.tsx`:**
- Add a "Screener" nav item to `NAV_ITEMS` array, positioned after "Market" and before "Simulation"
- Route: `'screener'`
- Label: `'Screener'`
- Icon: A search/filter icon (magnifying glass or funnel SVG)

### 15. App Routing (`dashboard/src/App.tsx`)

**Modify `dashboard/src/App.tsx`:**
- Import `ScreenerPage` from `@/pages/ScreenerPage`
- Add `case 'screener': return <ScreenerPage />` to the `PageSwitch` component

### 16. Backend Tests (`backend/tests/test_screener.py`)

**Create `backend/tests/test_screener.py`:**

```python
# Test filter evaluation logic
def test_filter_greater_than():
    """RSI > 50 should match when RSI is 65"""
    ...

def test_filter_less_than():
    """RSI < 30 should match when RSI is 25"""
    ...

def test_filter_greater_equal():
    """Price >= 100 should match when price is 100"""
    ...

def test_filter_combined_and():
    """Multiple filters combined with AND — all must pass"""
    ...

def test_filter_no_match():
    """Filter that matches nothing returns empty"""
    ...

def test_volume_breakout_filter():
    """Volume > 2x 20-day average should detect breakouts"""
    ...

# Test indicator computation on bulk data
def test_compute_rsi_for_scan():
    """RSI computation returns valid latest value"""
    ...

def test_compute_sma_for_scan():
    """SMA computation returns valid latest value"""
    ...

# Test universe loading
def test_load_sp500_universe():
    """sp500.json loads correctly with ~500 symbols"""
    ...

def test_load_nasdaq100_universe():
    """nasdaq100.json loads correctly with ~100 symbols"""
    ...

def test_load_invalid_universe():
    """Unknown universe raises ValueError"""
    ...

# Test cache behavior
def test_cache_stores_and_retrieves():
    """Cached data is returned within TTL"""
    ...

def test_cache_expires_after_ttl():
    """Stale cache is not returned"""
    ...

# Test API endpoints (FastAPI TestClient)
def test_scan_endpoint_returns_results():
    """POST /api/screener/scan returns results array"""
    ...

def test_universes_endpoint():
    """GET /api/screener/universes returns list with counts"""
    ...

def test_preset_crud():
    """Create, list, delete preset lifecycle"""
    ...

def test_delete_builtin_preset_fails():
    """Cannot delete built-in presets"""
    ...
```

Use `pytest` + `httpx` (FastAPI TestClient). For tests that would call yfinance, mock the yfinance calls with synthetic DataFrames to keep tests fast and deterministic.

## Dependencies to Install

**Backend** (add to `requirements.txt` if not already present):
```
yfinance>=0.2.36
```
(yfinance is likely already installed since it's used in existing yahoo endpoints — verify)

**Frontend** (no new packages needed — all UI is built with existing TailwindCSS)

## Files to Create
- `backend/screener.py`
- `backend/data/sp500.json`
- `backend/data/nasdaq100.json`
- `backend/data/etfs.json`
- `backend/tests/test_screener.py`
- `dashboard/src/pages/ScreenerPage.tsx`
- `dashboard/src/components/screener/FilterBuilder.tsx`
- `dashboard/src/components/screener/ScanResultsTable.tsx`
- `dashboard/src/components/screener/PresetSelector.tsx`
- `dashboard/src/components/screener/UniverseSelector.tsx`

## Files to Modify
- `backend/main.py` — add screener endpoints (POST /api/screener/scan, GET /api/screener/universes, GET/POST/DELETE /api/screener/presets)
- `backend/database.py` — add screener_presets table creation in init_db(), add CRUD functions, seed built-in presets
- `backend/models.py` — add ScanFilter, ScanRequest, ScanResultRow, ScanPreset models
- `dashboard/src/App.tsx` — import ScreenerPage, add 'screener' case to PageSwitch
- `dashboard/src/components/layout/Sidebar.tsx` — add Screener nav item to NAV_ITEMS array
- `dashboard/src/store/index.ts` — add useScreenerStore with filters, results, presets, scanning state
- `dashboard/src/services/api.ts` — add runScreenerScan, fetchScreenerUniverses, fetchScreenerPresets, saveScreenerPreset, deleteScreenerPreset
- `dashboard/src/types/index.ts` — add ScreenerIndicator, ScreenerOperator, ScanFilter, ScanResultRow, ScanPreset, UniverseInfo, ScreenerSortField types; update AppRoute

## Definition of Done
1. User can select S&P 500 universe and scan for "RSI(14) < 30" — returns matching stocks in the results table
2. Results table shows Symbol, Name, Price, Change%, Volume, Market Cap, RSI(14), Sector with sortable columns
3. Clicking a result row navigates to Market page with that symbol selected in the chart
4. At least 4 built-in presets work and are selectable from the preset dropdown (RSI Oversold, Golden Cross, Volume Breakout, 52-Week High)
5. User can save a custom filter configuration as a named preset
6. User can delete saved (non-built-in) presets
7. Loading spinner shows on the Scan button during scan (can take 10-30 seconds for large universes)
8. Toast shows on scan completion (success with count) and on scan errors (yfinance timeout, etc.)
9. Empty state shows "No symbols match your filters" when scan returns zero results
10. Backend caches bar data in memory — subsequent scans within 15 minutes reuse cached data
11. `pytest backend/tests/test_screener.py` passes all tests

## Important Notes
- **yfinance rate limits** — batch downloads are essential. Use `yf.download(tickers=[list], period="3mo", interval="1d")` instead of individual `yf.Ticker(sym).history()` calls. Process in batches of 50 symbols with 2-second delays.
- **Static universe files** — S&P 500 and NASDAQ 100 symbol lists must be static JSON files, not fetched dynamically. yfinance does not reliably provide constituent lists.
- **Server-side scans** — the client sends filter criteria, the server fetches data + computes indicators + applies filters + returns results. Never send bulk bar data to the client.
- **Use existing `indicators.py`** — all indicator computations must go through the `calculate(df, indicator, params)` function. Do NOT reimplement RSI, SMA, etc. in screener.py.
- **FilterBuilder reuse** — the filter builder component pattern will be reused in Stage 6 (Rule Builder). Design it as a generic, reusable component that accepts filter configuration and callbacks. Keep indicator definitions and operator lists as constants that can be imported.
- **Toast system** — use the existing toast notification system (from Stage 1) for all success/error feedback.
- **Navigation to Market page** — when a user clicks a scan result row, set the selected symbol in `useMarketStore` and switch the route to `'market'`. The Market page should automatically load the chart for that symbol.
- **Do NOT break existing functionality.** This is additive. All existing endpoints, pages, and features must continue to work.
- Keep the terminal dark theme consistent for all new UI components.
- Test everything with `MOCK_MODE=true` (no IBKR needed). The screener relies on yfinance, not IBKR.
