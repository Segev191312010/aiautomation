# Stage 4 Session Prompt: Backtesting Engine (CRITICAL)

You are working on a trading platform built with **FastAPI** (backend) and **React 18 + TypeScript + Zustand + TailwindCSS** (dashboard). The project is at `C:\Users\segev\sdvesdaW\trading`.

## Current State
- **Backend** (`backend/`): FastAPI with 40+ endpoints, IBKR integration (ib_insync), 8 technical indicators (RSI, SMA, EMA, MACD, BBANDS, ATR, STOCH, PRICE), rule engine with AND/OR logic + cooldown, order execution, virtual trading simulation, historical replay, mock GBM data, real-time WebSocket, SQLite persistence.
- **Dashboard** (`dashboard/`): React 18 + Vite + Zustand + TailwindCSS. Pages: Dashboard, TradeBotPage, MarketPage, SimulationPage, RulesPage (placeholder), SettingsPage (placeholder). Uses lightweight-charts for candlesticks. Dark terminal theme. Watchlist grid, comparison overlay.
- **Database**: SQLite with tables: `rules` (id, data JSON), `trades` (id, rule_id, symbol, action, timestamp, data JSON), `sim_account`, `sim_positions`, `sim_orders`.
- **3 operating modes**: IBKR Live, IBKR Paper, Simulation (offline with mock data).
- **Stages 1-3 complete**: Foundation/auth scaffold, advanced charting with multi-pane, screener.

## What to Build (Stage 4)

This is the user's **HIGHEST PRIORITY** feature. It must work correctly.

### Architecture Decisions (MUST follow these)
- **Event-driven (bar-by-bar)** — NOT vectorized. Process each bar sequentially to model realistic execution.
- **Indicator warmup** — Skip first N bars per indicator. SMA(200) needs 200 bars before valid signal. No signals generated during warmup.
- **Look-ahead bias prevention** — Rule engine receives `df[:current_bar_index+1]` slice ONLY. Cannot peek at future bars.
- **Execution-agnostic** — Backtester uses the SAME `_evaluate_condition()` from `rule_engine.py` as the live bot, ensuring backtest results approximate live behavior.

### 1. Rule Engine Refactor (modify `backend/rule_engine.py`)

**Current `_evaluate_condition()` signature:**
```python
def _evaluate_condition(cond: Condition, df: pd.DataFrame, cache: dict) -> bool:
```

**Refactor to accept an optional DataFrame slice parameter:**
- The function already receives `df` and operates on it. The change is at the **call site** in the backtester — the backtester passes `df[:i+1]` as the `df` argument.
- However, `evaluate_rule()` currently calls `_evaluate_condition()` with the full df. The backtester needs a way to evaluate conditions WITHOUT the cooldown/enabled checks from `evaluate_rule()`.
- Add a new public function:

```python
def evaluate_conditions(conditions: list[Condition], df: pd.DataFrame, logic: str = "AND") -> bool:
    """
    Evaluate a list of conditions against a DataFrame slice.
    Used by backtester — no cooldown, no enabled check.

    Args:
        conditions: List of Condition objects
        df:         DataFrame slice (e.g., df[:i+1] for bar-by-bar)
        logic:      "AND" or "OR"

    Returns:
        True if conditions are met per the logic operator.
    """
    if df.empty or len(df) < 2:
        return False
    cache: dict = {}
    results = [_evaluate_condition(c, df, cache) for c in conditions]
    if logic == "AND":
        return all(results)
    return any(results)  # OR
```

- `_evaluate_condition()` itself does NOT change — it already works on whatever `df` is passed in.
- `evaluate_rule()` does NOT change — `bot_runner.py` still calls it the same way.
- Backward compatibility is guaranteed because no existing function signatures change.

### 2. Backtesting Engine (create `backend/backtester.py`)

**Core function:**
```python
async def run_backtest(
    entry_conditions: list[Condition],
    exit_conditions: list[Condition],
    symbol: str,
    period: str,           # "1y", "2y", "5y", "10y", "max"
    interval: str,         # "1d", "1h", "5m"
    initial_capital: float,
    position_size_pct: float,  # 0-100, percentage of equity to use per trade
    stop_loss_pct: float,      # 0-100, 0 = disabled
    take_profit_pct: float,    # 0-100, 0 = disabled
    condition_logic: str = "AND",  # "AND" or "OR" — applied to BOTH entry and exit condition groups
) -> dict:
```

**Algorithm (bar-by-bar):**
1. Fetch historical data via `yfinance.download(symbol, period=period, interval=interval)`
2. Convert to DataFrame with columns: `time`, `open`, `high`, `low`, `close`, `volume`
3. Determine warmup period: scan all indicators in entry + exit conditions, find max lookback:
   - SMA/EMA/BBANDS: `length` param (default 20)
   - RSI/ATR: `length` param (default 14)
   - MACD: `slow` param (default 26) + `signal` param (default 9) = 35
   - STOCH: `k` param (default 14) + `smooth_k` (default 3) + `d` (default 3) = 20
   - For any condition referencing another indicator as `value` (e.g., "SMA_200"), also include that indicator's lookback
4. Iterate from bar `warmup_period` to bar `len(df)-1`:
   - Slice: `df_slice = df.iloc[:i+1].copy()`
   - If NOT in position:
     - Evaluate entry conditions: `evaluate_conditions(entry_conditions, df_slice, condition_logic)`
     - If True → BUY:
       - `qty = floor((equity * position_size_pct / 100) / current_close)`
       - `commission = cfg.SIM_COMMISSION` (from `config.py`, default $1.00 per order)
       - Record entry: price, qty, bar index, timestamp
       - Deduct cost + commission from cash
   - If IN position:
     - Check stop-loss: if `stop_loss_pct > 0` and `current_low <= entry_price * (1 - stop_loss_pct/100)` → SELL at stop price
     - Check take-profit: if `take_profit_pct > 0` and `current_high >= entry_price * (1 + take_profit_pct/100)` → SELL at take-profit price
     - Else evaluate exit conditions: `evaluate_conditions(exit_conditions, df_slice, condition_logic)`
     - If exit triggered → SELL at `current_close`
     - Deduct commission on sell
   - Track equity: `cash + (position_qty * current_close)` — mark-to-market
   - Append to equity curve: `{time: bar_timestamp, equity: current_equity}`
5. If still in position at end → close at last bar's close price
6. Compute buy-and-hold curve: initial_capital * (close[i] / close[warmup_period]) for each bar
7. Compute all metrics (see section 3)
8. Return result dict

**Return format:**
```python
{
    "symbol": str,
    "period": str,
    "interval": str,
    "initial_capital": float,
    "final_equity": float,
    "equity_curve": [{"time": int, "equity": float, "drawdown_pct": float}, ...],  # unix timestamps
    "buy_hold_curve": [{"time": int, "equity": float}, ...],
    "trades": [
        {
            "entry_date": str,       # ISO datetime
            "exit_date": str,
            "entry_price": float,
            "exit_price": float,
            "qty": int,
            "pnl": float,           # dollar P&L
            "pnl_pct": float,       # percentage return
            "duration_bars": int,
            "duration_days": float,
            "exit_reason": str,      # "signal" | "stop_loss" | "take_profit" | "end_of_data"
        },
        ...
    ],
    "metrics": { ... },  # see section 3
    "warmup_period": int,
    "total_bars": int,
    "entry_conditions": [...],  # echo back for saving
    "exit_conditions": [...],
    "condition_logic": str,
    "position_size_pct": float,
    "stop_loss_pct": float,
    "take_profit_pct": float,
}
```

### 3. Metrics Computation

Compute these metrics inside `backtester.py`. All based on the equity curve and trade list:

- **Total Return %**: `(final_equity - initial_capital) / initial_capital * 100`
- **CAGR**: `(final_equity / initial_capital) ^ (252 / trading_days) - 1` (annualized, 252 trading days/year)
- **Sharpe Ratio**: annualized, `mean(daily_returns) / std(daily_returns) * sqrt(252)`, risk-free rate = 0
- **Sortino Ratio**: `mean(daily_returns) / downside_deviation * sqrt(252)` where downside_deviation = `std(negative_returns_only)`
- **Calmar Ratio**: `CAGR / max_drawdown_pct` (if max drawdown is 0, return 0)
- **Max Drawdown %**: peak-to-trough maximum decline in equity curve
- **Win Rate**: `profitable_trades / total_trades * 100`
- **Profit Factor**: `sum(winning_pnl) / abs(sum(losing_pnl))` (if no losses, return 999.99)
- **Number of Trades**: total completed trades
- **Average Win**: mean P&L of profitable trades
- **Average Loss**: mean P&L of losing trades
- **Longest Winning Streak**: consecutive profitable trades
- **Longest Losing Streak**: consecutive losing trades
- **Average Trade Duration**: mean duration in days across all trades

Return as dict:
```python
{
    "total_return_pct": float,
    "cagr": float,
    "sharpe_ratio": float,
    "sortino_ratio": float,
    "calmar_ratio": float,
    "max_drawdown_pct": float,
    "win_rate": float,
    "profit_factor": float,
    "num_trades": int,
    "avg_win": float,
    "avg_loss": float,
    "longest_win_streak": int,
    "longest_lose_streak": int,
    "avg_trade_duration_days": float,
}
```

### 4. Pydantic Models (modify `backend/models.py`)

Add these models at the end of the file:

```python
# ---------------------------------------------------------------------------
# Backtesting models
# ---------------------------------------------------------------------------

class BacktestRequest(BaseModel):
    symbol: str
    period: str = "2y"
    interval: str = "1d"
    entry_conditions: list[Condition]
    exit_conditions: list[Condition]
    condition_logic: Literal["AND", "OR"] = "AND"
    initial_capital: float = 100000.0
    position_size_pct: float = 100.0
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0


class BacktestTrade(BaseModel):
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    qty: int
    pnl: float
    pnl_pct: float
    duration_bars: int
    duration_days: float
    exit_reason: str  # "signal" | "stop_loss" | "take_profit" | "end_of_data"


class BacktestMetrics(BaseModel):
    total_return_pct: float
    cagr: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    num_trades: int
    avg_win: float
    avg_loss: float
    longest_win_streak: int
    longest_lose_streak: int
    avg_trade_duration_days: float


class BacktestResult(BaseModel):
    id: str = Field(default_factory=lambda: str(_uuid.uuid4()))
    symbol: str
    period: str
    interval: str
    initial_capital: float
    final_equity: float
    equity_curve: list[dict]       # [{time, equity, drawdown_pct}]
    buy_hold_curve: list[dict]     # [{time, equity}]
    trades: list[BacktestTrade]
    metrics: BacktestMetrics
    warmup_period: int
    total_bars: int
    entry_conditions: list[Condition]
    exit_conditions: list[Condition]
    condition_logic: str
    position_size_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    created_at: str = ""           # ISO datetime, set on save


class BacktestSaveRequest(BaseModel):
    name: str
    result: BacktestResult
```

### 5. Database (modify `backend/database.py`)

Add a new table and CRUD functions for saved backtests:

**Table schema:**
```sql
CREATE TABLE IF NOT EXISTS backtests (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'demo',
    name       TEXT NOT NULL,
    strategy_data TEXT NOT NULL,  -- JSON: entry_conditions, exit_conditions, params
    result_data   TEXT NOT NULL,  -- JSON: full BacktestResult
    created_at TEXT NOT NULL
);
```

**Add these functions:**
```python
async def save_backtest(backtest_id: str, user_id: str, name: str, strategy_data: str, result_data: str, created_at: str) -> None:
    ...

async def get_backtests(user_id: str = "demo", limit: int = 50) -> list[dict]:
    """Return list of {id, name, symbol, created_at, metrics_summary}."""
    ...

async def get_backtest(backtest_id: str) -> dict | None:
    """Return full backtest with strategy_data and result_data."""
    ...

async def delete_backtest(backtest_id: str) -> bool:
    ...
```

Add the `CREATE TABLE` statement to `init_db()`.

### 6. Backend Endpoints (modify `backend/main.py`)

Add these endpoints:

```python
# ── Backtesting ───────────────────────────────────────────────────────────────

@app.post("/api/backtest/run")
async def api_backtest_run(req: BacktestRequest):
    """Run a backtest and return results. Does NOT save automatically."""
    from backtester import run_backtest
    result = await run_backtest(
        entry_conditions=req.entry_conditions,
        exit_conditions=req.exit_conditions,
        symbol=req.symbol,
        period=req.period,
        interval=req.interval,
        initial_capital=req.initial_capital,
        position_size_pct=req.position_size_pct,
        stop_loss_pct=req.stop_loss_pct,
        take_profit_pct=req.take_profit_pct,
        condition_logic=req.condition_logic,
    )
    return result


@app.post("/api/backtest/save")
async def api_backtest_save(req: BacktestSaveRequest):
    """Save a backtest result for later retrieval."""
    created_at = datetime.now(timezone.utc).isoformat()
    strategy_data = json.dumps({
        "entry_conditions": [c.model_dump() for c in req.result.entry_conditions],
        "exit_conditions": [c.model_dump() for c in req.result.exit_conditions],
        "condition_logic": req.result.condition_logic,
        "position_size_pct": req.result.position_size_pct,
        "stop_loss_pct": req.result.stop_loss_pct,
        "take_profit_pct": req.result.take_profit_pct,
    })
    result_data = req.result.model_dump_json()
    await database.save_backtest(
        backtest_id=req.result.id,
        user_id="demo",
        name=req.name,
        strategy_data=strategy_data,
        result_data=result_data,
        created_at=created_at,
    )
    return {"id": req.result.id, "saved": True}


@app.get("/api/backtest/history")
async def api_backtest_history():
    """List saved backtests."""
    return await database.get_backtests(user_id="demo")


@app.get("/api/backtest/{backtest_id}")
async def api_backtest_get(backtest_id: str):
    """Retrieve a specific saved backtest."""
    result = await database.get_backtest(backtest_id)
    if not result:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return result


@app.delete("/api/backtest/{backtest_id}")
async def api_backtest_delete(backtest_id: str):
    """Delete a saved backtest."""
    deleted = await database.delete_backtest(backtest_id)
    return {"deleted": deleted}
```

Add `import json` and `from models import BacktestRequest, BacktestSaveRequest` at the top of `main.py` (alongside existing imports).

### 7. Backtest Page (create `dashboard/src/pages/BacktestPage.tsx`)

**Layout:**
- Top bar: Symbol input (text field), Period selector (dropdown: 1Y, 2Y, 5Y), Interval selector (dropdown: 1D, 1H), "Run Backtest" button (blue, with loading spinner while running), "Save" button (appears after results)
- Two-column layout below top bar:
  - **Left panel (40% width)**: Strategy Builder (entry conditions, exit conditions, parameters)
  - **Right panel (60% width)**: Results area — shows placeholder text "Configure a strategy and run a backtest" until results arrive, then shows:
    - Equity curve chart
    - Metrics panel (grid of KPI cards)
    - Trade log table
- On mobile/narrow screens: stack vertically (left panel on top, results below)

**State management:**
- Use `useBacktestStore` from Zustand (see section 13)
- Loading state: show spinner on Run Backtest button, disable it while running
- Error state: show error message in results area if backtest fails

### 8. Strategy Builder (create `dashboard/src/components/backtest/StrategyBuilder.tsx`)

**Structure:**
- Two sections with headers: "Entry Conditions" and "Exit Conditions"
- Each section has:
  - A list of condition rows
  - An "Add Condition" button (+ icon)
  - AND/OR toggle (small button group) per section — BUT for simplicity, use ONE condition_logic toggle that applies to both entry and exit groups
- Each condition row: `[Indicator dropdown] [Params] [Operator dropdown] [Value input] [Delete button]`
  - **Indicator dropdown**: RSI, SMA, EMA, MACD, BBANDS, ATR, STOCH, PRICE
  - **Params**: dynamic based on indicator:
    - RSI: length (default 14)
    - SMA: length (default 20)
    - EMA: length (default 20)
    - MACD: fast (12), slow (26), signal (9)
    - BBANDS: length (20), std (2.0), band (upper/mid/lower)
    - ATR: length (14)
    - STOCH: k (14), d (3), smooth_k (3)
    - PRICE: no params
  - **Operator dropdown**: >, <, >=, <=, ==, crosses_above, crosses_below
  - **Value input**: number input OR text input for referencing another indicator (e.g., "SMA_200", "PRICE")
- Style: dark terminal theme, consistent with existing components. Use `bg-terminal-bg` for row backgrounds, `border-terminal-border`, `text-terminal-text`.

### 9. Backtest Parameters (create `dashboard/src/components/backtest/BacktestParams.tsx`)

- Grid of parameter inputs below the Strategy Builder:
  - Initial Capital: number input, default 100000
  - Position Size %: number input, default 100 (full equity)
  - Stop Loss %: number input, default 0 (disabled)
  - Take Profit %: number input, default 0 (disabled)
- Each input has a label and a small help text underneath
- Style: compact, inline with terminal theme

### 10. Equity Curve Chart (create `dashboard/src/components/backtest/EquityCurve.tsx`)

- Uses `lightweight-charts` `createChart()` with `addLineSeries()`
- **Two line series:**
  - Strategy equity: `terminal-blue` color (`#3b82f6`)
  - Buy-and-hold: `terminal-ghost` color (`#4a5568` or similar muted gray)
- Y-axis: dollar value (auto-scaled)
- X-axis: time
- Chart background: `terminal-bg` (`#0a0a0f`)
- Grid lines: subtle `terminal-border` color
- Tooltip/crosshair: show date, equity value, drawdown % (use `subscribeCrosshairMove`)
- **Trade markers**: green triangle-up markers at entry points, red triangle-down markers at exit points using `series.setMarkers()` on the equity line series
- Chart should be responsive (resize on container resize using `ResizeObserver`)
- Legend in top-left: "Strategy" (blue) and "Buy & Hold" (gray)

### 11. Metrics Panel (create `dashboard/src/components/backtest/MetricsPanel.tsx`)

- Import and reuse `KPICard` from `dashboard/src/components/tradebot/KPICard.tsx`
- Grid layout: 3 columns on desktop, 2 on tablet, 1 on mobile
- Cards (in order):
  1. **Total Return** — value: `XX.X%`, positive=true if >0
  2. **CAGR** — value: `XX.X%`, positive=true if >0
  3. **Sharpe Ratio** — value: `X.XX`, positive=true if >1
  4. **Sortino Ratio** — value: `X.XX`, positive=true if >1
  5. **Calmar Ratio** — value: `X.XX`, positive=true if >1
  6. **Max Drawdown** — value: `XX.X%`, positive=false (always red, drawdown is bad), prefix="-"
  7. **Win Rate** — value: `XX.X%`, positive=true if >50
  8. **Profit Factor** — value: `X.XX`, positive=true if >1
  9. **Trades** — value: `N`, neutral (no color)
- Below the KPI grid, show additional stats in a compact row:
  - Avg Win: $XX.XX | Avg Loss: $XX.XX | Win Streak: N | Lose Streak: N | Avg Duration: N days

### 12. Trade Log (create `dashboard/src/components/backtest/BacktestTradeLog.tsx`)

- Table with columns: #, Entry Date, Exit Date, Entry Price, Exit Price, Qty, P&L $, P&L %, Duration, Exit Reason
- Rows color-coded: green-tinted background for profitable trades, red-tinted for losing trades
  - Use `bg-terminal-green/5` and `bg-terminal-red/5` for subtle row coloring
- P&L values: green text for positive, red text for negative
- Click a trade row → callback to scroll the equity curve chart to that trade's time period (emit a `scrollToTime` event or use a ref)
- Show total/summary row at bottom: total P&L, average P&L %, total trades
- Paginate or virtualize if >50 trades (simple "Show more" button is fine)

### 13. Zustand Store (modify `dashboard/src/store/index.ts`)

Add a new `useBacktestStore`:

```typescript
// ── Backtest store ──────────────────────────────────────────────────────────

interface BacktestState {
  // Strategy configuration
  entryConditions: Condition[]
  exitConditions: Condition[]
  conditionLogic: 'AND' | 'OR'
  symbol: string
  period: string
  interval: string
  initialCapital: number
  positionSizePct: number
  stopLossPct: number
  takeProfitPct: number

  // Results
  result: BacktestResult | null
  loading: boolean
  error: string | null

  // History
  savedBacktests: BacktestHistoryItem[]

  // Actions
  setEntryConditions: (c: Condition[]) => void
  setExitConditions: (c: Condition[]) => void
  setConditionLogic: (l: 'AND' | 'OR') => void
  setSymbol: (s: string) => void
  setPeriod: (p: string) => void
  setInterval: (i: string) => void
  setInitialCapital: (v: number) => void
  setPositionSizePct: (v: number) => void
  setStopLossPct: (v: number) => void
  setTakeProfitPct: (v: number) => void
  setResult: (r: BacktestResult | null) => void
  setLoading: (v: boolean) => void
  setError: (e: string | null) => void
  setSavedBacktests: (b: BacktestHistoryItem[]) => void
  reset: () => void
}
```

Defaults: symbol="AAPL", period="2y", interval="1d", initialCapital=100000, positionSizePct=100, stopLossPct=0, takeProfitPct=0, conditionLogic="AND", entryConditions with one default row (RSI, length:14, <, 30), exitConditions with one default row (RSI, length:14, >, 70).

### 14. TypeScript Types (modify `dashboard/src/types/index.ts`)

Add at the end of the file:

```typescript
// ── Backtesting ──────────────────────────────────────────────────────────────

export interface BacktestRequest {
  symbol: string
  period: string
  interval: string
  entry_conditions: Condition[]
  exit_conditions: Condition[]
  condition_logic: 'AND' | 'OR'
  initial_capital: number
  position_size_pct: number
  stop_loss_pct: number
  take_profit_pct: number
}

export interface BacktestTrade {
  entry_date: string
  exit_date: string
  entry_price: number
  exit_price: number
  qty: number
  pnl: number
  pnl_pct: number
  duration_bars: number
  duration_days: number
  exit_reason: string
}

export interface BacktestMetrics {
  total_return_pct: number
  cagr: number
  sharpe_ratio: number
  sortino_ratio: number
  calmar_ratio: number
  max_drawdown_pct: number
  win_rate: number
  profit_factor: number
  num_trades: number
  avg_win: number
  avg_loss: number
  longest_win_streak: number
  longest_lose_streak: number
  avg_trade_duration_days: number
}

export interface BacktestResult {
  id: string
  symbol: string
  period: string
  interval: string
  initial_capital: number
  final_equity: number
  equity_curve: { time: number; equity: number; drawdown_pct: number }[]
  buy_hold_curve: { time: number; equity: number }[]
  trades: BacktestTrade[]
  metrics: BacktestMetrics
  warmup_period: number
  total_bars: number
  entry_conditions: Condition[]
  exit_conditions: Condition[]
  condition_logic: string
  position_size_pct: number
  stop_loss_pct: number
  take_profit_pct: number
  created_at?: string
}

export interface BacktestHistoryItem {
  id: string
  name: string
  symbol: string
  created_at: string
  total_return_pct: number
  num_trades: number
  sharpe_ratio: number
}
```

Also update the `AppRoute` type:
```typescript
export type AppRoute = 'dashboard' | 'tradebot' | 'market' | 'simulation' | 'rules' | 'settings' | 'backtest'
```

### 15. API Functions (modify `dashboard/src/services/api.ts`)

Add at the end of the file:

```typescript
// ── Backtesting ──────────────────────────────────────────────────────────────

export const runBacktest = (body: BacktestRequest) =>
  post<BacktestResult>('/api/backtest/run', body)

export const saveBacktest = (name: string, result: BacktestResult) =>
  post<{ id: string; saved: boolean }>('/api/backtest/save', { name, result })

export const fetchBacktestHistory = () =>
  get<BacktestHistoryItem[]>('/api/backtest/history')

export const fetchBacktest = (id: string) =>
  get<BacktestResult>(`/api/backtest/${id}`)

export const deleteBacktest = (id: string) =>
  del<{ deleted: boolean }>(`/api/backtest/${id}`)
```

Add the necessary type imports at the top of `api.ts`.

### 16. App Routing (modify `dashboard/src/App.tsx`)

- Import `BacktestPage` (lazy or direct)
- Add case in `PageSwitch`:
```typescript
case 'backtest': return <BacktestPage />
```

### 17. Sidebar Navigation (modify `dashboard/src/components/layout/Sidebar.tsx`)

Add a "Backtest" nav item in `NAV_ITEMS` array, positioned AFTER "Simulation" and BEFORE "Rules":

```typescript
{
  route: 'backtest',
  label: 'Backtest',
  icon: (
    <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
      <path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z" />
    </svg>
  ),
},
```

### 18. Tests (create `backend/tests/test_backtester.py`)

**Test cases to implement:**

```python
import pytest
import pandas as pd
import numpy as np
from models import Condition
from rule_engine import evaluate_conditions, _evaluate_condition, evaluate_rule
from backtester import run_backtest, _compute_metrics, _determine_warmup

# 1. Test indicator warmup detection
def test_warmup_period_sma():
    """SMA(5) needs 5 bars warmup."""
    conditions = [Condition(indicator="SMA", params={"length": 5}, operator=">", value=100)]
    warmup = _determine_warmup(conditions, [])
    assert warmup >= 5

def test_warmup_period_rsi():
    """RSI(14) needs 14 bars warmup."""
    conditions = [Condition(indicator="RSI", params={"length": 14}, operator="<", value=30)]
    warmup = _determine_warmup(conditions, [])
    assert warmup >= 14

def test_warmup_period_combined():
    """Max of all indicator warmups."""
    entry = [Condition(indicator="SMA", params={"length": 200}, operator=">", value="SMA_50")]
    exit_ = [Condition(indicator="RSI", params={"length": 14}, operator=">", value=70)]
    warmup = _determine_warmup(entry, exit_)
    assert warmup >= 200

# 2. Test evaluate_conditions (new function from rule_engine refactor)
def test_evaluate_conditions_and_logic():
    """AND logic: all conditions must be true."""
    # Create a df where RSI would be < 30 (sharp decline)
    # ... construct known test data ...
    pass

def test_evaluate_conditions_or_logic():
    """OR logic: any condition can trigger."""
    pass

# 3. Test backward compatibility
def test_evaluate_rule_still_works():
    """Existing evaluate_rule() function works unchanged for bot_runner."""
    from models import Rule, TradeAction
    # ... create a rule, verify it evaluates correctly ...
    pass

# 4. Test look-ahead bias prevention
def test_no_look_ahead_bias():
    """Condition evaluation only sees data up to current bar."""
    # Create 20 bars of data. At bar 10, only bars 0-10 should be visible.
    # Verify by checking that indicator values computed at bar 10
    # match what you'd get from df[:11].
    pass

# 5. Test stop-loss
@pytest.mark.asyncio
async def test_stop_loss_triggers():
    """Stop-loss should exit position when price drops below threshold."""
    # Use a known price series where price drops significantly
    pass

# 6. Test take-profit
@pytest.mark.asyncio
async def test_take_profit_triggers():
    """Take-profit should exit position when price rises above threshold."""
    pass

# 7. Test metrics with known values
def test_metrics_calculation():
    """Verify metric calculations with manually computed expected values."""
    trades = [
        {"pnl": 100, "pnl_pct": 10, "duration_days": 5},
        {"pnl": -50, "pnl_pct": -5, "duration_days": 3},
        {"pnl": 200, "pnl_pct": 20, "duration_days": 10},
        {"pnl": -30, "pnl_pct": -3, "duration_days": 2},
    ]
    # Win rate should be 50%
    # Profit factor should be (100+200) / (50+30) = 3.75
    # Avg win = 150, avg loss = -40
    pass

# 8. Test full backtest end-to-end (requires network for yfinance)
@pytest.mark.asyncio
@pytest.mark.skipif(True, reason="Requires network access")
async def test_full_backtest_aapl():
    """Full backtest: buy AAPL when RSI(14)<30, sell when RSI(14)>70, 2Y daily."""
    entry = [Condition(indicator="RSI", params={"length": 14}, operator="<", value=30)]
    exit_ = [Condition(indicator="RSI", params={"length": 14}, operator=">", value=70)]
    result = await run_backtest(
        entry_conditions=entry,
        exit_conditions=exit_,
        symbol="AAPL",
        period="2y",
        interval="1d",
        initial_capital=100000,
        position_size_pct=100,
        stop_loss_pct=0,
        take_profit_pct=0,
    )
    assert result["metrics"]["num_trades"] >= 0
    assert len(result["equity_curve"]) > 0
    assert result["warmup_period"] >= 14
```

Make helper functions testable by exporting `_determine_warmup()` and `_compute_metrics()` from `backtester.py`.

## Dependencies to Install

**Backend** (add to `requirements.txt` if not already present):
```
yfinance>=0.2.36
```
(yfinance should already be installed from Stage 3 screener, but verify)

**Frontend** (no new packages needed — lightweight-charts is already installed)

## Files to Create
- `backend/backtester.py`
- `backend/tests/test_backtester.py`
- `dashboard/src/pages/BacktestPage.tsx`
- `dashboard/src/components/backtest/StrategyBuilder.tsx`
- `dashboard/src/components/backtest/BacktestParams.tsx`
- `dashboard/src/components/backtest/EquityCurve.tsx`
- `dashboard/src/components/backtest/MetricsPanel.tsx`
- `dashboard/src/components/backtest/BacktestTradeLog.tsx`

## Files to Modify
- `backend/rule_engine.py` — add `evaluate_conditions()` public function
- `backend/main.py` — add 5 backtest endpoints
- `backend/database.py` — add `backtests` table + CRUD functions
- `backend/models.py` — add BacktestRequest, BacktestResult, BacktestTrade, BacktestMetrics, BacktestSaveRequest models
- `dashboard/src/App.tsx` — add backtest route to PageSwitch
- `dashboard/src/components/layout/Sidebar.tsx` — add Backtest nav item
- `dashboard/src/store/index.ts` — add useBacktestStore
- `dashboard/src/services/api.ts` — add backtest API functions + type imports
- `dashboard/src/types/index.ts` — add backtest types + update AppRoute

## Existing Code Reference

**`backend/config.py`** — `cfg.SIM_COMMISSION` is `float(os.getenv("SIM_COMMISSION", "1.0"))` (default $1.00 per order). Use this for backtest commission.

**`backend/indicators.py`** — `calculate(df, indicator, params)` returns a `pd.Series`. Supported indicators: RSI, SMA, EMA, MACD, BBANDS, ATR, STOCH, PRICE. The backtester calls this on each bar's slice.

**`backend/rule_engine.py`** — `_evaluate_condition(cond, df, cache)` evaluates one condition against the last bar of df. The backtester does NOT call this directly — it calls the new `evaluate_conditions()` which wraps it. `evaluate_rule()` and `evaluate_all()` remain unchanged for `bot_runner.py`.

**`backend/models.py`** — `Condition` model: `{indicator, params, operator, value}`. The entry/exit conditions for backtesting use this SAME model.

**`dashboard/src/components/tradebot/KPICard.tsx`** — Reusable KPI card with props: `{label, value, subLabel, positive, prefix, suffix, highlight}`. Import and use in MetricsPanel.

**`dashboard/src/store/index.ts`** — Zustand stores pattern: `create<StateInterface>((set, get) => ({...}))`. Follow the same pattern for `useBacktestStore`.

**`dashboard/src/types/index.ts`** — `Condition` type already exists: `{indicator: Indicator, params: Record<string, number|string>, operator: string, value: number|string}`. Reuse it for backtest conditions.

**`dashboard/src/services/api.ts`** — Uses a `req<T>(method, path, body)` wrapper. Exports `get`, `post`, `put`, `del` helpers. Follow the same pattern for backtest API functions.

## Definition of Done
1. User can define entry conditions ("RSI(14) < 30 AND SMA(50) > SMA(200)")
2. User can define exit conditions ("RSI(14) > 70 OR price < SMA(50)")
3. User can set symbol, period (1Y, 2Y, 5Y), interval (1D), initial capital, position size, SL/TP
4. Running backtest shows loading spinner, then displays results
5. Equity curve chart shows strategy vs buy-and-hold with two distinct colored lines
6. Metrics panel shows all 9+ metrics with correct values using KPICard components
7. Trade log shows all trades with entry/exit details, P&L, duration, and exit reason
8. Buy/sell markers appear on the equity curve at trade entry/exit points
9. Indicator warmup is enforced (no trades in warmup period)
10. No look-ahead bias (verified by test — condition evaluation only sees data up to current bar)
11. User can save a backtest with a name and reload it from history list
12. "Buy AAPL when RSI(14) < 30, sell when RSI(14) > 70" over 2Y daily data completes successfully
13. All pytest tests pass: `pytest backend/tests/test_backtester.py`
14. Existing bot_runner.py still works unchanged (backward compatible rule_engine)
15. Backtest page is accessible from sidebar navigation

## Important Notes
- This is the user's CRITICAL priority — it must work correctly.
- Bar-by-bar execution is slower but CORRECT — do not optimize prematurely.
- The `Condition` model from `models.py` is REUSED — entry/exit conditions use the same format as rules.
- Commission: use `cfg.SIM_COMMISSION` per trade (default $1.00, already exists in `config.py`).
- Backtests with 5Y daily data (~1260 bars) should complete in under 10 seconds.
- Use `yfinance` for historical data (same as the existing yahoo bars endpoint pattern).
- The strategy builder UI should be generic enough to be REUSED in Stage 6 (Rule Builder).
- Do NOT break existing functionality. This is additive.
- Keep the terminal dark theme consistent for all new UI components.
- Test everything with `SIM_MODE=true` and `MOCK_MODE=true` (no IBKR needed).
