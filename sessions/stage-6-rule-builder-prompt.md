# Stage 6 Session Prompt: Strategy / Rule Builder UI

You are working on a trading platform built with **FastAPI** (backend) and **React 18 + TypeScript + Zustand + TailwindCSS** (dashboard). The project is at `C:\Users\segev\sdvesdaW\trading`.

## Current State
- **Backend** (`backend/`): FastAPI with 40+ endpoints, IBKR integration (ib_insync), 8 technical indicators (RSI, SMA, EMA, MACD, BBANDS, ATR, STOCH, PRICE) in `indicators.py`, rule engine with AND/OR logic + cooldown in `rule_engine.py`, order execution, virtual trading simulation, historical replay, mock GBM data, real-time WebSocket, SQLite persistence. Full rules CRUD API already exists: `GET/POST/PUT/DELETE /api/rules`, `POST /api/rules/{id}/toggle`. Yahoo Finance bars endpoint (`GET /api/yahoo/{symbol}/bars`) supports `period` and `interval` params. Screener with scan engine and filter builder. Backtesting engine with bar-by-bar evaluation, equity curves, metrics, and trade logs. Alert engine with price/technical alerts, WebSocket broadcast, and browser push notifications.
- **Dashboard** (`dashboard/`): React 18 + Vite + Zustand + TailwindCSS. Pages: Dashboard, TradeBotPage, MarketPage, SimulationPage, SettingsPage, ScreenerPage, BacktestPage, AlertsPage. Uses **lightweight-charts v4.2** for candlesticks with multi-pane layout, drawing tools, and crosshair sync. Dark terminal theme (Bloomberg-style palette). Watchlist grid, comparison overlay, indicator selector. The **Rules page currently shows a "coming soon" placeholder** in `App.tsx`.
- **Database**: SQLite with tables: `users`, `rules` (id, data JSON), `trades`, `sim_account`, `sim_positions`, `sim_orders`, `screener_presets`, `backtests`, `alerts`, `alert_history` -- all with `user_id TEXT DEFAULT 'demo'`.
- **3 operating modes**: IBKR Live, IBKR Paper, Simulation (offline with mock data).
- **3 seeded starter rules** in the database: "RSI Oversold Bounce" (AAPL, RSI crosses_below 30, BUY 100 MKT), "Golden Cross" (AAPL, SMA_50 crosses_above SMA_200, BUY 50 MKT), "RSI Overbought Exit" (AAPL, RSI crosses_above 70, SELL 100 MKT).

### Stages 1-5 Completed
All previous stages are implemented. The following infrastructure is in place and MUST be used:

**Stage 1 — Foundation:**
- **Toast system**: `ToastProvider` + `useToast()` hook (`success`, `error`, `warning`, `info`). Toast on all API errors and user actions.
- **Error boundaries**: `ErrorBoundary` component wraps each page in `App.tsx`.
- **Settings page**: `SettingsPage.tsx` at `/settings`. User preferences stored as JSON blob.
- **Auth scaffold**: `users` table with demo user, `user_id` on all tables, JWT token infrastructure.
- **Loading skeletons**: `Skeleton` component available for loading states.
- **API auth header**: `api.ts` sends `Authorization: Bearer <token>` on all requests.

**Stage 2 — Advanced Charting:**
- Multi-pane chart layout with volume, RSI, MACD panels and crosshair sync.
- Chart toolbar with timeframe buttons, chart type toggle, indicator dropdown, fullscreen, screenshot.
- Drawing tools (horizontal lines, trendlines, Fibonacci retracement).
- `useChart` hook for reusable chart creation.
- `addTradeMarkers()` / `clearTradeMarkers()` annotation API.

**Stage 3 — Screener:**
- `ScreenerPage.tsx` with `FilterBuilder.tsx` for visual filter rows: `[Indicator] [Operator] [Value]` with +/- buttons.
- `ScanResultsTable.tsx`, `PresetSelector.tsx`, `UniverseSelector.tsx`.
- Backend scan engine with server-side caching.

**Stage 4 — Backtesting:**
- `BacktestPage.tsx` with `StrategyBuilder.tsx` for entry/exit condition builder (reuses Condition model).
- `BacktestParams.tsx`, `EquityCurve.tsx`, `MetricsPanel.tsx`, `BacktestTradeLog.tsx`.
- Backend bar-by-bar engine in `backtester.py` with indicator warmup, no look-ahead bias.
- Trade entry/exit markers on chart via annotation API.
- Endpoints: `POST /api/backtest/run`, `GET /api/backtest/history`, `GET /api/backtest/{id}`, `POST /api/backtest/save`.

**Stage 5 — Alerts:**
- `AlertsPage.tsx` with `AlertForm.tsx`, `AlertBell.tsx` header bell with unread badge.
- Backend alert engine in `alert_engine.py` with async evaluation loop.
- WebSocket `alert_fired` event wired to toasts + bell badge.
- Browser push notification support.

### Existing Rules Architecture (IMPORTANT — do NOT recreate)

The rules CRUD API already fully exists. Here is what is in place:

**Backend Models (`backend/models.py`):**
```python
class Condition(BaseModel):
    indicator: Literal["RSI", "SMA", "EMA", "MACD", "BBANDS", "ATR", "STOCH", "PRICE"]
    params: dict[str, Any] = Field(default_factory=dict)
    operator: str  # crosses_above, crosses_below, >, <, >=, <=, ==
    value: float | str  # numeric threshold, or "PRICE", or "SMA_200"

class TradeAction(BaseModel):
    type: Literal["BUY", "SELL"]
    asset_type: Literal["STK", "OPT", "FUT"] = "STK"
    quantity: int = Field(gt=0)
    order_type: Literal["MKT", "LMT"] = "MKT"
    limit_price: Optional[float] = None

class Rule(BaseModel):
    id: str = Field(default_factory=lambda: str(_uuid.uuid4()))
    name: str
    symbol: str
    enabled: bool = False
    conditions: list[Condition]
    logic: Literal["AND", "OR"] = "AND"
    action: TradeAction
    cooldown_minutes: int = 60
    last_triggered: Optional[str] = None

class RuleCreate(BaseModel):
    name: str
    symbol: str
    enabled: bool = False
    conditions: list[Condition]
    logic: Literal["AND", "OR"] = "AND"
    action: TradeAction
    cooldown_minutes: int = 60

class RuleUpdate(BaseModel):
    name: Optional[str] = None
    symbol: Optional[str] = None
    enabled: Optional[bool] = None
    conditions: Optional[list[Condition]] = None
    logic: Optional[Literal["AND", "OR"]] = None
    action: Optional[TradeAction] = None
    cooldown_minutes: Optional[int] = None
```

**Backend Existing API Endpoints (`backend/main.py`):**
```python
GET  /api/rules               # list_rules() -> list of Rule dicts
GET  /api/rules/{rule_id}     # get_rule_route() -> single Rule dict (404 if not found)
POST /api/rules               # create_rule(body: RuleCreate) -> Rule dict (201)
PUT  /api/rules/{rule_id}     # update_rule_route(body: RuleUpdate) -> updated Rule dict
DELETE /api/rules/{rule_id}   # delete_rule_route() -> {"deleted": true}
POST /api/rules/{rule_id}/toggle  # toggle_rule() -> {"id": ..., "enabled": bool}
```

**Backend Database (`backend/database.py`):**
Rules stored as JSON blobs: `INSERT OR REPLACE INTO rules (id, data) VALUES (?, ?)`.
Functions: `get_rules()`, `get_rule(rule_id)`, `save_rule(rule)`, `delete_rule(rule_id)`.
Seeded with 3 starter rules on first run.

**Backend Rule Engine (`backend/rule_engine.py`):**
- `_evaluate_condition(cond, df, cache)` — evaluates one condition against last bar of DataFrame.
- `evaluate_rule(rule, df)` — evaluates all conditions with AND/OR logic, checks cooldown.
- `evaluate_all(rules, bars_by_symbol)` — evaluates all enabled rules, returns triggered list.
- Operators: `crosses_above`, `crosses_below`, `>`, `<`, `>=`, `<=`, `==`.
- Values can be numeric, `"PRICE"`, or `"SMA_200"` style strings (parsed by `indicators.resolve_value()`).

**Backend Indicators (`backend/indicators.py`):**
- `calculate(df, indicator, params)` — returns `pd.Series` for the indicator.
- Supported: RSI (length), SMA (length), EMA (length), MACD (fast, slow, signal), BBANDS (length, std, band), ATR (length), STOCH (k, d, smooth_k), PRICE (no params).
- `detect_cross(series_a, series_b)` — returns `"above"`, `"below"`, or `None`.
- `resolve_value(value, df, cache)` — resolves `"PRICE"`, `"SMA_200"`, or numeric to comparable form.

**Frontend Types (`dashboard/src/types/index.ts`):**
```typescript
type Indicator = 'RSI' | 'SMA' | 'EMA' | 'MACD' | 'BBANDS' | 'ATR' | 'STOCH' | 'PRICE'

interface Condition {
  indicator: Indicator
  params: Record<string, number | string>
  operator: string
  value: number | string
}

interface TradeAction {
  type: OrderAction      // 'BUY' | 'SELL'
  asset_type: AssetType  // 'STK' | 'OPT' | 'FUT'
  quantity: number
  order_type: OrderType  // 'MKT' | 'LMT'
  limit_price?: number
}

interface Rule {
  id: string
  name: string
  symbol: string
  enabled: boolean
  conditions: Condition[]
  logic: 'AND' | 'OR'
  action: TradeAction
  cooldown_minutes: number
  last_triggered?: string
}

interface RuleCreate {
  name: string
  symbol: string
  enabled?: boolean
  conditions: Condition[]
  logic?: 'AND' | 'OR'
  action: TradeAction
  cooldown_minutes?: number
}
```

**Frontend Existing API Functions (`dashboard/src/services/api.ts`):**
```typescript
export const fetchRules    = () => get<Rule[]>('/api/rules')
export const fetchRule     = (id: string) => get<Rule>(`/api/rules/${id}`)
export const createRule    = (body: RuleCreate) => post<Rule>('/api/rules', body)
export const updateRule    = (id: string, body: Partial<Rule>) => put<Rule>(`/api/rules/${id}`, body)
export const deleteRule    = (id: string) => del<{ deleted: boolean }>(`/api/rules/${id}`)
export const toggleRule    = (id: string) => post<{ id: string; enabled: boolean }>(`/api/rules/${id}/toggle`)
```

**Frontend Store (`dashboard/src/store/index.ts` — useBotStore):**
```typescript
interface BotState {
  rules: Rule[]
  setRules: (r: Rule[]) => void
  updateRule: (r: Rule) => void
  // ... other bot state
}
```

**Frontend App.tsx — Current Rules Placeholder:**
```tsx
function RulesPage() {
  return (
    <div className="flex items-center justify-center h-64 text-terminal-ghost font-mono text-sm">
      Rules engine — coming soon
    </div>
  )
}
// In PageSwitch: case 'rules': return <RulesPage />
```

**Frontend Sidebar (`dashboard/src/components/layout/Sidebar.tsx`):**
Rules already has a nav item with `route: 'rules'` and label `'Rules'`.

### Terminal Theme (Tailwind)
All new UI must use the existing terminal dark theme palette:
- Backgrounds: `terminal-bg` (#080d18), `terminal-surface` (#0e1726), `terminal-elevated` (#131f33)
- Borders: `terminal-border` (#1c2e4a), `terminal-muted` (#243650)
- Input fields: `terminal-input` (#0a1525)
- Text: `terminal-text` (#dce8f5), `terminal-dim` (#5f7a9d), `terminal-ghost` (#384d6b)
- Colors: `terminal-green` (#00e07a), `terminal-red` (#ff3d5a), `terminal-blue` (#4f91ff), `terminal-amber` (#f59e0b), `terminal-purple` (#a78bfa)
- Font: JetBrains Mono (`font-mono`), sizes 10-11px for labels, 12-13px for UI text
- Shadows: `shadow-terminal`, `shadow-glow-green`, `shadow-glow-red`, `shadow-glow-blue`

## What to Build (Stage 6)

### 1. New Backend Endpoints (minimal -- most CRUD exists)

**Add `POST /api/rules/{rule_id}/duplicate` to `backend/main.py`:**
- Fetch the existing rule by ID (404 if not found).
- Deep copy the rule: create a new `Rule` with a new UUID.
- Append `" (copy)"` to the name.
- Set `enabled = False` and `last_triggered = None` on the copy.
- Save the copy via `save_rule()`.
- Return the new rule dict with status 201.

**Add `GET /api/rules/{rule_id}/preview` to `backend/main.py`:**
- Fetch the rule by ID (404 if not found).
- Fetch current OHLCV bars for the rule's symbol using `_yf_bars()` (try Yahoo Finance first, fall back to mock data with `get_mock_ohlcv()`).
- Convert bars to a pandas DataFrame with columns `[time, open, high, low, close, volume]`.
- For each condition in the rule:
  - Call `indicators.calculate(df, cond.indicator, cond.params)` to get the indicator series.
  - Get the current value (last non-NaN value in the series).
  - Resolve the target value using `indicators.resolve_value(cond.value, df, {})`.
  - Determine if the condition is met by calling `rule_engine._evaluate_condition(cond, df, {})`.
  - Build a result dict: `{indicator, params, operator, target, current_value, met}`.
- Determine overall `would_fire` by applying the rule's AND/OR logic to all condition results.
- Return: `{conditions: [...], would_fire: bool, symbol, last_price}` where `last_price` is `df["close"].iloc[-1]`.

**Add `POST /api/rules/preview` to `backend/main.py` (for unsaved rules):**
- Accepts a `RuleCreate` body (same as create, but does not save).
- Performs the same preview logic as the `GET` version above.
- This allows previewing rules before they are saved.

**Add `RulePreview` response model to `backend/models.py`:**
```python
class ConditionPreview(BaseModel):
    indicator: str
    params: dict[str, Any]
    operator: str
    target: float | str
    current_value: float | None
    met: bool

class RulePreview(BaseModel):
    conditions: list[ConditionPreview]
    would_fire: bool
    symbol: str
    last_price: float
```

### 2. Rules Page (`dashboard/src/pages/RulesPage.tsx`)

Completely replaces the "coming soon" placeholder. This is the main rules management page.

**Header section:**
- Title: "Trading Rules" in `text-xl font-semibold text-terminal-text`
- Subtitle: rule count and enabled count (e.g., "3 rules, 1 enabled")
- "Create Rule" button: primary blue style (`bg-terminal-blue hover:bg-terminal-blue/80 text-white`)
- Clicking "Create Rule" opens the RuleBuilder slide-over in create mode

**Rule list (uses `RuleList.tsx` sub-component):**
- Renders each rule as a card/row
- Each card shows: name (bold), symbol (badge), status badge (green "Enabled" / dim "Disabled"), condition count, last triggered time (relative, e.g., "2 hours ago" or "Never")
- Actions per rule card:
  - **Enable/Disable toggle**: switch/toggle button, calls `toggleRule()` API
  - **Edit**: opens RuleBuilder slide-over in edit mode, pre-filled with rule data
  - **Duplicate**: calls `POST /api/rules/{id}/duplicate`, toasts on success, refreshes list
  - **Delete**: confirmation prompt (use `window.confirm` or a small confirm UI), calls `deleteRule()` API, toasts "Rule deleted"
  - **Backtest**: button labeled "Backtest" — navigates to `/backtest?ruleId={id}` (uses `useUIStore.setRoute('backtest')` and stores the ruleId for BacktestPage to pick up)
- Sort by: name, symbol, enabled status, last triggered

**Empty state:**
- When no rules exist: centered message "No rules yet. Create your first rule or start from a template." with a "Create Rule" button and a link/button to scroll to templates.

**Templates section at bottom:**
- Heading: "Quick Start Templates"
- Grid of template cards (see RuleTemplates component below)

**Loading state:**
- Show loading skeleton while `fetchRules()` is in progress

### 3. Rule List (`dashboard/src/components/rules/RuleList.tsx`)

Renders the list of rules as cards. Receives `rules: Rule[]` and action callbacks as props.

Each rule card layout:
```
┌─────────────────────────────────────────────────────────┐
│ [Toggle]  RSI Oversold Bounce          AAPL   Enabled   │
│           3 conditions (AND)    Last: 2 hours ago       │
│           BUY 100 STK @ MKT                             │
│                                [Backtest] [Edit] [Dup] [Del]│
└─────────────────────────────────────────────────────────┘
```

- Use `bg-terminal-surface border border-terminal-border rounded-lg` for cards.
- Toggle uses green/dim styling matching the bot toggle pattern from TradeBotPage.
- Action buttons are small icon buttons with tooltips.
- Condition summary: "{N} conditions ({AND/OR})".
- Action summary: "{BUY/SELL} {qty} {asset_type} @ {MKT/LMT}".

### 4. Rule Builder (`dashboard/src/components/rules/RuleBuilder.tsx`)

Opens as a **slide-over panel from the right** (or full-width modal) when creating or editing a rule. This is the main visual builder.

**Panel structure:**
- Overlay: `fixed inset-0 bg-black/60 z-40` backdrop
- Panel: `fixed right-0 top-0 h-screen w-[600px] max-w-full bg-terminal-surface border-l border-terminal-border z-50 overflow-y-auto`
- Header: title ("Create Rule" or "Edit Rule: {name}"), close button (X icon)
- Footer: "Save" button (primary blue) and "Cancel" button (ghost style)

**Sections inside the panel:**

**A. Name & Symbol:**
- Name: text input, auto-generated default like `"RSI Strategy for AAPL"` based on first condition indicator and symbol
- Symbol: text input (uppercase), with suggestions dropdown from the active watchlist symbols in `useMarketStore`. As user types, filter watchlist symbols that match.

**B. Conditions (uses ConditionGroup component):**
- AND/OR logic toggle at the top
- List of ConditionBlock components
- "Add Condition" button at the bottom (`+ Add Condition`)
- Minimum 1 condition required

**C. Action (uses ActionConfig component):**
- BUY/SELL, quantity, order type, limit price

**D. Settings:**
- Cooldown: numeric input in minutes, default 60
- Label: "Cooldown between triggers (minutes)"

**E. Preview (uses RulePreview component):**
- Shows live preview at the bottom
- Only visible when symbol is set and at least one condition exists

**Form state management:**
- Use React `useState` for local form state (not Zustand — the form is transient)
- On "Save": if creating, call `createRule()` API; if editing, call `updateRule()` API
- Toast on success ("Rule created" / "Rule updated")
- Close panel and refresh rules list on success
- Validate before save: name required, symbol required, at least 1 condition, quantity > 0

### 5. Condition Block (`dashboard/src/components/rules/ConditionBlock.tsx`)

A single condition row within the builder. This is the core UX element.

**Layout:**
```
[Indicator ▾] [Dynamic Params...] [Operator ▾] [Value input] [✕]
```

**Indicator dropdown:**
Options: `RSI`, `SMA`, `EMA`, `MACD`, `BBANDS`, `ATR`, `STOCH`, `PRICE`
- Styled select or custom dropdown with `bg-terminal-input border-terminal-border`

**Dynamic parameter inputs per indicator** (this is key UX -- params change based on indicator):

| Indicator | Params | Defaults |
|-----------|--------|----------|
| RSI | `length` (number) | 14 |
| SMA | `length` (number) | 20 |
| EMA | `length` (number) | 20 |
| MACD | `fast` (number), `slow` (number), `signal` (number) | 12, 26, 9 |
| BBANDS | `length` (number), `std` (number), `band` (select: upper/middle/lower) | 20, 2, "mid" |
| ATR | `length` (number) | 14 |
| STOCH | `k_period` (number), `d_period` (number) | 14, 3 |
| PRICE | _(no params)_ | -- |

When the user changes the indicator, reset params to defaults for that indicator. Param inputs are compact number inputs with labels.

**Operator dropdown:**
Options: `>`, `<`, `>=`, `<=`, `==`, `crosses_above`, `crosses_below`
- Display as readable labels: "Greater than", "Less than", "Greater or equal", "Less or equal", "Equals", "Crosses above", "Crosses below"
- Store the operator code (e.g., `">"`, `"crosses_above"`)

**Value input:**
- Default: numeric input field
- Toggle/option to compare against another indicator (e.g., `"SMA_200"`, `"PRICE"`)
- When comparing to another indicator, show a text input or select with suggestions: `PRICE`, `SMA_20`, `SMA_50`, `SMA_200`, `EMA_12`, `EMA_26`
- Store as `number` for numeric or `string` for indicator comparison

**Remove button:** Small `X` icon to remove this condition from the list.

**Match Stage 3 FilterBuilder pattern:** The ConditionBlock should follow a similar visual pattern to Stage 3's FilterBuilder rows for UI consistency across the app (same input styling, same +/- button approach, same compact row layout).

### 6. Condition Group (`dashboard/src/components/rules/ConditionGroup.tsx`)

Wraps multiple ConditionBlock components with AND/OR logic.

**Layout:**
```
  [AND ● | ○ OR]           <- toggle at top

  ┌ Condition 1 ──────────────────────────┐
  │ [RSI] [length: 14] [<] [30]       [✕] │
  └────────────────────────────────────────┘
       AND                              <- label between conditions
  ┌ Condition 2 ──────────────────────────┐
  │ [SMA] [length: 50] [crosses_above] [SMA_200] [✕] │
  └────────────────────────────────────────┘

  [+ Add Condition]
```

**AND/OR toggle:**
- Two pill buttons side by side: "AND" and "OR"
- Active one uses `bg-terminal-blue text-white`, inactive uses `bg-terminal-muted text-terminal-dim`
- "AND" means ALL conditions must be met; "OR" means ANY condition fires the rule
- Between each condition, show the logic label ("AND" or "OR") in small dim text

**Props:**
```typescript
interface ConditionGroupProps {
  conditions: Condition[]
  logic: 'AND' | 'OR'
  onConditionsChange: (conditions: Condition[]) => void
  onLogicChange: (logic: 'AND' | 'OR') => void
}
```

### 7. Action Config (`dashboard/src/components/rules/ActionConfig.tsx`)

Configures the trade action for the rule.

**Layout:**
```
  Action:     [● BUY | ○ SELL]
  Quantity:   [____100____] shares
  Order Type: [● Market | ○ Limit]
  Limit Price: [$_____.____]    (only shown when Limit selected)
```

**Action toggle:**
- BUY: green pill (`bg-terminal-green/20 text-terminal-green border-terminal-green/50` when active)
- SELL: red pill (`bg-terminal-red/20 text-terminal-red border-terminal-red/50` when active)

**Quantity:**
- Numeric input, minimum 1
- Label: "shares"

**Order type:**
- Toggle: "Market" / "Limit"
- Market is default

**Limit price:**
- Only visible when order type is "Limit" (`order_type === 'LMT'`)
- Numeric input with $ prefix

**Asset type:** Default to `"STK"` (stocks). Can optionally show a small dropdown for STK/OPT/FUT but STK is the primary use case.

**Props:**
```typescript
interface ActionConfigProps {
  action: TradeAction
  onChange: (action: TradeAction) => void
}
```

### 8. Rule Preview (`dashboard/src/components/rules/RulePreview.tsx`)

Shows a live preview of whether the rule would fire against current market data. Displayed at the bottom of the RuleBuilder.

**For saved rules:** Calls `GET /api/rules/{id}/preview`.
**For unsaved/edited rules:** Calls `POST /api/rules/preview` with the current form state as a `RuleCreate` body.

**Layout:**
```
  ── Live Preview ──────────────────────────────────
  AAPL last price: $189.42

  ✓ RSI(14) = 28.3 < 30         [MET]
  ✕ SMA(50) = 185.2 > SMA(200)  [NOT MET]

  ● This rule would NOT FIRE (AND logic: 1/2 conditions met)
  ─────────────────────────────────────────────────
```

**Behavior:**
- Auto-refreshes when symbol or conditions change (debounced, ~800ms delay after last change)
- Shows a loading spinner while fetching
- For each condition: indicator name with params, `current_value`, operator, target value, met/not-met badge
  - Met: green badge `bg-terminal-green/15 text-terminal-green`
  - Not met: red badge `bg-terminal-red/15 text-terminal-red`
- Overall verdict: "This rule would FIRE" (green text) or "This rule would NOT FIRE" (red text)
- If preview fails (no data for symbol), show a dim message: "Preview unavailable — no market data for {symbol}"

**Props:**
```typescript
interface RulePreviewProps {
  ruleId?: string              // for saved rules
  formState?: RuleCreate       // for unsaved rules
  symbol: string
}
```

### 9. Rule Templates (`dashboard/src/components/rules/RuleTemplates.tsx`)

Clickable template cards at the bottom of the RulesPage. Templates are pre-defined `RuleCreate` objects -- NOT a separate backend system. They exist purely in the frontend.

**Templates to include (6 total):**

1. **RSI Oversold Bounce**
   - Description: "Buy when RSI drops below 30"
   - Conditions: `[{indicator: "RSI", params: {length: 14}, operator: "crosses_below", value: 30}]`
   - Action: `{type: "BUY", asset_type: "STK", quantity: 100, order_type: "MKT"}`
   - Logic: "AND"

2. **Golden Cross**
   - Description: "Buy when SMA 50 crosses above SMA 200"
   - Conditions: `[{indicator: "SMA", params: {length: 50}, operator: "crosses_above", value: "SMA_200"}]`
   - Action: `{type: "BUY", asset_type: "STK", quantity: 50, order_type: "MKT"}`
   - Logic: "AND"

3. **RSI Overbought Exit**
   - Description: "Sell when RSI rises above 70"
   - Conditions: `[{indicator: "RSI", params: {length: 14}, operator: "crosses_above", value: 70}]`
   - Action: `{type: "SELL", asset_type: "STK", quantity: 100, order_type: "MKT"}`
   - Logic: "AND"

4. **Bollinger Band Squeeze**
   - Description: "Buy when price drops below lower Bollinger Band"
   - Conditions: `[{indicator: "PRICE", params: {}, operator: "<", value: "BBANDS_20"}, {indicator: "RSI", params: {length: 14}, operator: "<", value: 40}]`
   - Note: use `BBANDS` lower band — since `resolve_value` does not handle `BBANDS_20` directly, use the condition: `{indicator: "BBANDS", params: {length: 20, std: 2, band: "lower"}, operator: ">", value: "PRICE"}`
   - Alternatively, use two conditions: BBANDS lower > PRICE and RSI < 40
   - Action: `{type: "BUY", asset_type: "STK", quantity: 100, order_type: "MKT"}`
   - Logic: "AND"

5. **MACD Crossover**
   - Description: "Buy when MACD line crosses above signal line"
   - Conditions: `[{indicator: "MACD", params: {fast: 12, slow: 26, signal: 9}, operator: "crosses_above", value: 0}]`
   - Action: `{type: "BUY", asset_type: "STK", quantity: 50, order_type: "MKT"}`
   - Logic: "AND"

6. **Volume Breakout**
   - Description: "Buy when price is above SMA 20 and RSI is between 50-70"
   - Conditions: `[{indicator: "PRICE", params: {}, operator: ">", value: "SMA_20"}, {indicator: "RSI", params: {length: 14}, operator: ">", value: 50}, {indicator: "RSI", params: {length: 14}, operator: "<", value: 70}]`
   - Action: `{type: "BUY", asset_type: "STK", quantity: 100, order_type: "MKT"}`
   - Logic: "AND"

**Template card layout:**
```
┌──────────────────────────┐
│  RSI Oversold Bounce     │
│  Buy when RSI drops      │
│  below 30                │
│  [Use Template]          │
└──────────────────────────┘
```

- Cards: `bg-terminal-elevated border border-terminal-border rounded-lg p-4 hover:border-terminal-blue/50 cursor-pointer transition-colors`
- Clicking a card opens the RuleBuilder slide-over, pre-filled with the template data
- Symbol defaults to `"AAPL"` for all templates (user can change in the builder)

### 10. "Backtest This Rule" Integration

**On each rule card and in the RuleBuilder, add a "Backtest" button.**

When clicked:
- Navigate to the Backtest page by calling `useUIStore.setRoute('backtest')` (or the app's navigation mechanism)
- Pass the rule ID via a store field or URL query mechanism
- The BacktestPage should detect the incoming `ruleId` and:
  1. Fetch the rule via `GET /api/rules/{id}`
  2. Extract the rule's conditions
  3. Pre-fill the StrategyBuilder's entry conditions from the rule's conditions
  4. Pre-fill the symbol from the rule's symbol
  5. Set a default exit strategy (e.g., inverse of entry conditions, or leave blank for user to configure)

**Implementation approach:**
- Add a `backtestRuleId: string | null` field to `useBotStore` (or `useUIStore`)
- `setBacktestRuleId(id: string | null)` action
- When "Backtest" is clicked: `setBacktestRuleId(rule.id)` then `setRoute('backtest')`
- BacktestPage on mount: if `backtestRuleId` is set, fetch the rule and pre-fill, then clear the field

### 11. Store Extensions (`dashboard/src/store/index.ts`)

Extend `useBotStore` with rule management actions:

```typescript
interface BotState {
  // ... existing fields ...
  rules: Rule[]
  rulesLoading: boolean
  selectedRuleId: string | null      // for editing
  ruleBuilderOpen: boolean           // slide-over state
  backtestRuleId: string | null      // for backtest integration

  setRules: (r: Rule[]) => void
  updateRule: (r: Rule) => void
  addRule: (r: Rule) => void
  removeRule: (id: string) => void
  setRulesLoading: (v: boolean) => void
  setSelectedRuleId: (id: string | null) => void
  setRuleBuilderOpen: (v: boolean) => void
  setBacktestRuleId: (id: string | null) => void
}
```

### 12. Tests (`backend/tests/test_rules.py`)

Create pytest tests using FastAPI `TestClient` (from `httpx`):

**Test duplicate endpoint:**
- Create a rule via `POST /api/rules`
- Duplicate it via `POST /api/rules/{id}/duplicate`
- Assert the response has a new ID, name ends with `" (copy)"`, `enabled` is `False`, `last_triggered` is `None`
- Assert the duplicate has the same conditions, action, symbol as the original
- Assert `GET /api/rules` now returns 2 rules (original + copy)
- Test 404 for non-existent rule ID

**Test preview endpoint:**
- Create a rule via `POST /api/rules`
- Call `GET /api/rules/{id}/preview`
- Assert response has `conditions` array, `would_fire` bool, `symbol` string, `last_price` number
- Each condition result has `indicator`, `params`, `operator`, `target`, `current_value`, `met`
- Test 404 for non-existent rule ID

**Test unsaved preview endpoint:**
- Call `POST /api/rules/preview` with a `RuleCreate` body
- Assert same response structure as saved preview
- Test with various indicators (RSI, SMA, PRICE)

## Dependencies to Install

**Backend** (no new packages -- uses existing `yfinance`, `pandas`, `numpy`, `pydantic`, `aiosqlite`, `pytest`, `httpx`).

**Frontend** (no new packages -- all UI is built with existing TailwindCSS, Zustand, clsx).

## Files to Create
- `dashboard/src/pages/RulesPage.tsx`
- `dashboard/src/components/rules/RuleList.tsx`
- `dashboard/src/components/rules/RuleBuilder.tsx`
- `dashboard/src/components/rules/ConditionBlock.tsx`
- `dashboard/src/components/rules/ConditionGroup.tsx`
- `dashboard/src/components/rules/ActionConfig.tsx`
- `dashboard/src/components/rules/RulePreview.tsx`
- `dashboard/src/components/rules/RuleTemplates.tsx`
- `backend/tests/test_rules.py`

## Files to Modify
- `backend/main.py` — add `POST /api/rules/{rule_id}/duplicate`, `GET /api/rules/{rule_id}/preview`, `POST /api/rules/preview` endpoints
- `backend/models.py` — add `ConditionPreview` and `RulePreview` response models
- `dashboard/src/App.tsx` — replace the inline `RulesPage` placeholder with an import of the real `RulesPage` component
- `dashboard/src/store/index.ts` — extend `useBotStore` with `rulesLoading`, `selectedRuleId`, `ruleBuilderOpen`, `backtestRuleId`, `addRule`, `removeRule`, `setRulesLoading`, `setSelectedRuleId`, `setRuleBuilderOpen`, `setBacktestRuleId`
- `dashboard/src/services/api.ts` — add `duplicateRule(id)`, `previewRule(id)`, `previewUnsavedRule(body)` functions
- `dashboard/src/types/index.ts` — add `ConditionPreview`, `RulePreview` types

## Definition of Done
1. Rules page shows all rules as cards with name, symbol, status badge, condition count, last triggered time
2. User can create a new rule via the visual builder: select indicator, set dynamic params per indicator, choose operator, set value
3. Multiple conditions with AND/OR toggle work correctly and save properly
4. Action config (BUY/SELL toggle, quantity, Market/Limit order type, limit price) saves correctly
5. Rule preview shows current indicator values and whether each condition is met, with overall fire/not-fire verdict
6. Preview auto-refreshes (debounced) when symbol or conditions change
7. Enable/disable toggle works and updates immediately in the UI
8. Duplicate creates a deep copy with `" (copy)"` suffix, disabled, with new UUID
9. Delete removes the rule after confirmation, shows toast
10. At least 6 templates are available and pre-fill the builder on click
11. "Backtest this rule" navigates to BacktestPage with conditions pre-filled from the rule
12. Created/edited rules appear in the bot's evaluation loop when enabled (existing rule engine already handles this -- rules just need to be saved to the database correctly)
13. All 3 seeded starter rules still work and appear in the list
14. `pytest backend/tests/test_rules.py` passes all tests
15. All new UI uses terminal dark theme consistently
16. Toast notifications appear on: rule created, rule updated, rule duplicated, rule deleted, toggle success, API errors
17. No TypeScript errors
18. All existing functionality still works (IBKR, simulation, mock mode, bot evaluation, other pages)

## Important Notes
- The rules CRUD API already exists in `main.py` -- do NOT recreate or modify the existing `GET/POST/PUT/DELETE /api/rules` or `POST /api/rules/{id}/toggle` endpoints. Only ADD the new `duplicate` and `preview` endpoints.
- Reuse the `Condition`, `TradeAction`, `Rule`, `RuleCreate`, `RuleUpdate` models exactly as defined in `models.py`. Do not change their structure.
- The `ConditionBlock` pattern should follow Stage 3's `FilterBuilder` for visual consistency: same input styling, same compact row layout, same +/- button approach.
- Dynamic indicator params are the KEY UX feature: when user selects RSI, show only `length`. When user selects MACD, show `fast`, `slow`, `signal`. When user selects BBANDS, show `length`, `std`, and `band` dropdown. When user selects PRICE, show no params.
- The preview is a powerful feature -- it shows the user LIVE whether their rule would fire right now against real market data. Make it prominent in the builder.
- Templates are just pre-defined `RuleCreate` objects in frontend code, not a separate backend system or database table.
- Use `useToast()` from Stage 1 for all success/error feedback.
- The slide-over panel is preferred over a full-page modal for the RuleBuilder -- it allows the user to see the rules list behind it.
- For the "Backtest this rule" integration: the simplest approach is a store field that BacktestPage reads on mount. Clear it after reading to avoid stale state.
- Test everything with `SIM_MODE=true` and `MOCK_MODE=true` (no IBKR needed). The preview endpoint should fall back to mock data if Yahoo Finance is unavailable.
- Keep the `rules` directory flat under `dashboard/src/components/rules/` -- no deeper nesting needed.
