# Stage 7 Session Prompt: Risk Management & Portfolio Analytics

You are working on a trading platform built with **FastAPI** (backend) and **React 18 + TypeScript + Zustand + TailwindCSS** (dashboard). The project is at `C:\Users\segev\sdvesdaW\trading`.

## Current State
- **Backend** (`backend/`): FastAPI with 40+ endpoints, IBKR integration (ib_insync), 8 technical indicators (RSI, SMA, EMA, MACD, BBANDS, ATR, STOCH, PRICE), rule engine with AND/OR logic + cooldown, order execution, virtual trading simulation, historical replay, mock GBM data, real-time WebSocket, SQLite persistence.
- **Dashboard** (`dashboard/`): React 18 + Vite + Zustand + TailwindCSS. Pages: Dashboard, TradeBotPage, MarketPage, SimulationPage, ScreenerPage, BacktestPage, AlertsPage, RulesPage, SettingsPage. Uses lightweight-charts for candlesticks. Dark terminal theme. Multi-pane charts with drawing tools. Stock screener with filters. Event-driven backtester. Alert engine with WebSocket notifications. Visual rule builder with condition groups.
- **Database**: SQLite with tables: `rules`, `trades`, `sim_account`, `sim_positions`, `sim_orders`, `users`, `screener_presets`, `alerts`, `alert_history`, `backtests`.
- **3 operating modes**: IBKR Live, IBKR Paper, Simulation (offline with mock data).
- **Stages 1-6 complete**: Foundation/auth scaffold, advanced charting (chart core, drawing tools, multi-pane sync), stock screener, backtesting engine, alerts & notifications, rule builder UI.

## What to Build (Stage 7)

### 1. Analytics Engine (Backend)

**Create `backend/analytics.py`:**

```python
# Portfolio allocation breakdown
async def get_portfolio_allocation(positions) -> list[dict]:
    """
    Takes list of position dicts (from sim or IBKR).
    Returns: [{symbol, qty, value, weight_pct, sector}]
    Total portfolio value computed from all positions + cash.
    """

# Sector breakdown via yfinance
async def get_sector_breakdown(positions) -> list[dict]:
    """
    Map each symbol to its sector using yfinance ticker.info (cached in memory).
    Hardcode fallbacks for common symbols:
      AAPL=Technology, MSFT=Technology, GOOGL=Technology, META=Technology,
      AMZN=Consumer Cyclical, TSLA=Consumer Cyclical, NVDA=Technology,
      JPM=Financial Services, BAC=Financial Services, JNJ=Healthcare,
      SPY=ETF, QQQ=ETF, IWM=ETF, DIA=ETF
    Cache yfinance results in a module-level dict to avoid repeated API calls.
    Returns: [{sector, total_value, weight_pct, symbols: [{symbol, value}]}]
    """

# Correlation matrix
async def get_correlation_matrix(symbols: list[str], period: str = "60d") -> dict:
    """
    Fetch 60 days of daily close prices for each symbol via yfinance.
    Compute pairwise Pearson correlation of daily returns.
    Returns: {symbols: [...], matrix: [[1.0, 0.8, ...], [0.8, 1.0, ...], ...]}
    """

# P&L data from trade history
async def get_pnl_data(trades: list[dict], period: str = "ALL") -> dict:
    """
    Process trade history to compute daily P&L.
    Group trades by date, sum realized P&L per day.
    Compute cumulative return from initial equity.
    Filter by period: 30d, 90d, 1Y, ALL.
    Returns: {
        daily_pnl: [{date, pnl}],
        cumulative_return: [{date, return_pct}],
        total_return_pct, best_day: {date, pnl}, worst_day: {date, pnl}
    }
    """
```

### 2. Risk Engine (Backend)

**Create `backend/risk.py`:**

```python
import numpy as np

def calc_portfolio_beta(positions: list[dict], benchmark: str = "SPY") -> float | None:
    """
    Portfolio beta vs SPY using 60-day daily returns.
    1. Fetch 60d daily closes for each held symbol + SPY via yfinance
    2. Compute daily returns for each position
    3. Weight returns by position weight
    4. portfolio_beta = cov(portfolio_returns, benchmark_returns) / var(benchmark_returns)
    Returns None if no positions held.
    """

def calc_var_95(portfolio_value: float, daily_returns: list[float]) -> float:
    """
    Value at Risk at 95% confidence using historical simulation.
    Sort daily returns, take the 5th percentile.
    VaR = portfolio_value * abs(percentile_5)
    Returns dollar amount at risk.
    """

def calc_sharpe(returns: list[float], risk_free: float = 0.0) -> float:
    """
    Annualized Sharpe ratio.
    sharpe = (mean(returns) - risk_free/252) / std(returns) * sqrt(252)
    Returns 0.0 if insufficient data or zero std dev.
    """

def calc_sortino(returns: list[float], risk_free: float = 0.0) -> float:
    """
    Sortino ratio (uses downside deviation only).
    downside = returns where return < risk_free/252
    sortino = (mean(returns) - risk_free/252) / std(downside) * sqrt(252)
    Returns 0.0 if no downside returns.
    """

def calc_max_drawdown(equity_curve: list[float]) -> float:
    """
    Maximum drawdown as a percentage (0.0 to 1.0).
    Track running peak, compute drawdown at each point.
    Return the largest drawdown.
    """

def calc_position_size(
    method: str,           # "kelly" | "fixed_fractional"
    capital: float,
    risk_pct: float,       # e.g. 0.02 for 2%
    stop_distance: float,  # dollars per share
    win_rate: float = 0.0, # for Kelly
    avg_win: float = 0.0,  # for Kelly
    avg_loss: float = 0.0, # for Kelly
) -> dict:
    """
    Kelly criterion: f = (win_rate * avg_win - (1-win_rate) * avg_loss) / avg_win
        recommended_shares = floor(capital * f / stop_distance)
        Cap Kelly fraction at 0.25 (quarter Kelly for safety)
    Fixed fractional: size = capital * risk_pct / stop_distance
        recommended_shares = floor(size)

    Returns: {recommended_shares: int, recommended_value: float, method_used: str, kelly_fraction?: float}
    """
```

### 3. Backend Endpoints

**Add to `backend/main.py`:**

```python
# ── Analytics endpoints ──────────────────────────────────────────────────

@app.get("/api/analytics/portfolio")
async def analytics_portfolio():
    """
    Returns portfolio allocation data.
    Response: {
        allocations: [{symbol, qty, value, weight_pct, sector}],
        sectors: [{sector, total_value, weight_pct, symbols: [{symbol, value}]}],
        total_value: float,
        cash: float,
        cash_pct: float
    }
    In SIM_MODE: use sim_engine positions and account.
    In IBKR mode: use ibkr positions and account.
    """

@app.get("/api/analytics/risk")
async def analytics_risk():
    """
    Returns risk metrics for current portfolio.
    Response: {
        portfolio_beta: float | null,
        var_95: float,
        max_drawdown_pct: float,
        sharpe_ratio: float,
        sortino_ratio: float
    }
    If no positions, return null/0 values (not errors).
    """

@app.get("/api/analytics/pnl")
async def analytics_pnl(period: str = "ALL"):
    """
    Query param: period = "30d" | "90d" | "1Y" | "ALL"
    Response: {
        daily_pnl: [{date: str, pnl: float}],
        cumulative_return: [{date: str, return_pct: float}],
        total_return_pct: float,
        best_day: {date: str, pnl: float},
        worst_day: {date: str, pnl: float}
    }
    Uses trade history (both rules trades + sim orders).
    """

@app.post("/api/analytics/position-size")
async def analytics_position_size(body: PositionSizeRequest):
    """
    Body: {
        method: "kelly" | "fixed_fractional",
        capital: float,
        risk_pct: float,
        stop_distance: float,
        win_rate?: float,
        avg_win?: float,
        avg_loss?: float
    }
    Response: {
        recommended_shares: int,
        recommended_value: float,
        method_used: str,
        kelly_fraction?: float
    }
    """

@app.put("/api/trades/{trade_id}/annotate")
async def annotate_trade(trade_id: str, body: TradeAnnotation):
    """
    Body: {notes?: str, tags?: list[str]}
    Updates the trade's JSON blob with notes/tags fields.
    Returns updated trade.
    """
```

### 4. Trade Journal Enhancement

**Modify `backend/models.py` — extend Trade model:**
- Add `notes: Optional[str] = None` field
- Add `tags: list[str] = Field(default_factory=list)` field
- These are stored in the existing `data` JSON blob, so no schema migration needed

**Add new Pydantic models to `backend/models.py`:**
```python
class PortfolioAllocation(BaseModel):
    symbol: str
    qty: float
    value: float
    weight_pct: float
    sector: str

class SectorBreakdown(BaseModel):
    sector: str
    total_value: float
    weight_pct: float
    symbols: list[dict]  # [{symbol, value}]

class RiskMetrics(BaseModel):
    portfolio_beta: Optional[float]
    var_95: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float

class PnLDataPoint(BaseModel):
    date: str
    pnl: float

class PnLSummary(BaseModel):
    daily_pnl: list[PnLDataPoint]
    cumulative_return: list[dict]  # [{date, return_pct}]
    total_return_pct: float
    best_day: Optional[PnLDataPoint]
    worst_day: Optional[PnLDataPoint]

class PositionSizeRequest(BaseModel):
    method: Literal["kelly", "fixed_fractional"]
    capital: float = Field(gt=0)
    risk_pct: float = Field(gt=0, le=1.0)
    stop_distance: float = Field(gt=0)
    win_rate: Optional[float] = Field(None, ge=0, le=1.0)
    avg_win: Optional[float] = Field(None, ge=0)
    avg_loss: Optional[float] = Field(None, ge=0)

class PositionSizeResult(BaseModel):
    recommended_shares: int
    recommended_value: float
    method_used: str
    kelly_fraction: Optional[float] = None

class TradeAnnotation(BaseModel):
    notes: Optional[str] = None
    tags: Optional[list[str]] = None
```

**Modify `backend/database.py`:**
```python
async def update_trade_annotation(trade_id: str, notes: str | None, tags: list[str] | None) -> Trade | None:
    """
    Load trade by id, update notes/tags in JSON blob, save back.
    Returns updated Trade or None if not found.
    """
```

### 5. Analytics Page (Frontend)

**Create `dashboard/src/pages/AnalyticsPage.tsx`:**
- Top row: 6 KPI cards (reuse `KPICard` component from `@/components/tradebot/KPICard`):
  - Total Value, Cash, Unrealized P&L, Realized P&L, Sharpe Ratio, Max Drawdown
- Period selector row: 30D | 90D | 1Y | ALL (default: ALL) — syncs with P&L data
- Left column: `AllocationChart` + sector breakdown toggle
- Right column: `PnLChart` (daily bars + cumulative line)
- Bottom row: `RiskMetricsGrid` + `PositionSizer`
- Below: `TradeJournal` (enhanced trade log)
- Data fetching: poll `/api/analytics/portfolio`, `/api/analytics/risk`, `/api/analytics/pnl?period=X` on mount + 30s interval
- Use `useAnalyticsStore` for state management

Layout structure:
```
+---KPI---+---KPI---+---KPI---+---KPI---+---KPI---+---KPI---+
|  Total  |  Cash   | Unreal  | Realized| Sharpe  | Max DD  |
+---------+---------+---------+---------+---------+---------+
|      [ 30D ]  [ 90D ]  [ 1Y ]  [ ALL ]                   |
+-------------------+---------------------------------------+
|  AllocationChart  |         PnLChart                      |
|  (donut + sector) |  (histogram + cumulative line)        |
+-------------------+---------------------------------------+
|  RiskMetricsGrid            |  PositionSizer              |
+-----------------------------+-----------------------------+
|  TradeJournal (full width)                                |
+-----------------------------------------------------------+
```

### 6. Allocation Chart

**Create `dashboard/src/components/analytics/AllocationChart.tsx`:**
- SVG-based donut chart (no extra dependencies)
- Each segment represents a position's weight in the portfolio
- Color palette: cycle through 8-10 distinct terminal-friendly colors
- Center text: total portfolio value formatted as USD
- Hover a segment: show tooltip with symbol, value, weight%
- Toggle button: "By Symbol" / "By Sector" view
- Sector view groups positions and shows sector names
- Legend below the chart with color swatches + symbol + percentage
- If no positions: show "No positions" placeholder text

SVG donut implementation approach:
```
- Use SVG <circle> elements with stroke-dasharray/stroke-dashoffset
- OR use SVG <path> elements with arc commands
- Radius ~80, stroke-width ~30 for donut effect
- Animate segments on mount with CSS transition
```

### 7. P&L Chart

**Create `dashboard/src/components/analytics/PnLChart.tsx`:**
- Use lightweight-charts (same library as the main trading chart)
- Daily P&L as histogram bars:
  - Green bars for positive P&L days
  - Red bars for negative P&L days
  - Use `addHistogramSeries()` from lightweight-charts
- Cumulative return as line series overlay on a separate price scale (right axis)
- Period selector: receives `period` prop from parent, fetches `/api/analytics/pnl?period=X`
- Summary stats below chart in a small row: Total Return %, Best Day, Worst Day
- If no trade data: show "No trades yet" placeholder
- Chart container: minimum height 300px, responsive width
- Apply terminal theme colors: `#0a0e17` background, grid lines `#1a1f2e`

### 8. Risk Metrics Grid

**Create `dashboard/src/components/analytics/RiskMetricsGrid.tsx`:**
- 5 KPI cards in a row (reuse `KPICard` from `@/components/tradebot/KPICard`):
  - **Beta**: value with 2 decimal places. Green if 0.8-1.2, amber if outside, "N/A" if null
  - **VaR (95%)**: dollar amount. Always red-tinted. Format as USD
  - **Sharpe Ratio**: 2 decimals. Green if > 1.0, amber if 0.5-1.0, red if < 0.5
  - **Sortino Ratio**: 2 decimals. Same color logic as Sharpe
  - **Max Drawdown**: percentage with 1 decimal. Green if < 10%, amber if 10-20%, red if > 20%
- Subtitle text under each card explaining the metric in 5-10 words
- Receives risk data as props from AnalyticsPage

### 9. Position Sizer

**Create `dashboard/src/components/analytics/PositionSizer.tsx`:**
- Card container with header "Position Sizer"
- Method selector: radio buttons or toggle for "Kelly Criterion" / "Fixed Fractional"
- Input fields (styled like existing QuickOrderForm on TradeBotPage):
  - **Available Capital** ($) — pre-filled from account cash if available
  - **Risk Per Trade** (%) — default 2%
  - **Stop Distance** ($) — required, no default
  - **Win Rate** (%) — only shown for Kelly method
  - **Average Win** ($) — only shown for Kelly method
  - **Average Loss** ($) — only shown for Kelly method
- "Calculate" button: calls `POST /api/analytics/position-size`
- Output section:
  - Recommended Shares: bold, large font
  - Recommended Value: formatted USD
  - Kelly Fraction (if Kelly method): shown as percentage
- Error state: show validation errors inline
- All inputs use terminal theme styling (bg-terminal-input, border-terminal-border, etc.)

### 10. Trade Journal

**Create `dashboard/src/components/analytics/TradeJournal.tsx`:**
- Section header: "Trade Journal"
- Enhanced table with columns: Date, Symbol, Side, Qty, Price, P&L, Status, Tags, Notes, Actions
- **Inline note editing**:
  - Each row has an "Add Note" button (pencil icon)
  - Clicking expands a text area below the row
  - Save button calls `PUT /api/trades/{id}/annotate` with `{notes: "..."}`
  - Cancel button collapses the text area
- **Tag chips**:
  - Display existing tags as small colored chips next to the symbol
  - Click "+" to add a tag: dropdown with presets ["momentum", "earnings", "reversal", "breakout", "scalp", "swing", "dividend"]
  - Also allow typing a custom tag
  - Tags are saved via `PUT /api/trades/{id}/annotate` with `{tags: [...]}`
  - Click "x" on a chip to remove that tag
- **Filter by tags**: tag filter bar above the table — click a tag to filter trades showing only that tag
- **Export to CSV**: button in the header area
  - Generates CSV with columns: Date, Symbol, Side, Qty, Price, P&L, Status, Tags, Notes
  - Downloads as `trade-journal-YYYY-MM-DD.csv`
- Table uses the same styling as the existing trade log on TradeBotPage (font-mono, terminal colors, etc.)
- Pagination or "show more" button if > 50 trades

### 11. Tests (Backend)

**Create `backend/tests/test_risk.py`:**
```python
# Test calc_sharpe with known returns
# Example: daily returns [0.01, -0.005, 0.008, -0.003, 0.012] over 5 days
# Manually compute expected Sharpe and assert within tolerance

# Test calc_sortino with known returns (should only use downside deviation)

# Test calc_max_drawdown with known equity curve
# Example: [100, 110, 105, 95, 100, 90, 115]
# Peak=110, trough=90 → drawdown = (110-90)/110 = 18.18%

# Test calc_var_95 with known distribution

# Test calc_position_size Kelly
# win_rate=0.6, avg_win=100, avg_loss=50
# Kelly f = (0.6*100 - 0.4*50)/100 = 0.40, capped at 0.25
# capital=100000, stop_distance=5 → shares = floor(100000*0.25/5) = 5000

# Test calc_position_size fixed fractional
# capital=100000, risk_pct=0.02, stop_distance=5 → shares = floor(100000*0.02/5) = 400

# Test calc_portfolio_beta returns None for empty positions
```

**Create `backend/tests/test_analytics.py`:**
```python
# Test get_pnl_data with mock trade list
# Test period filtering (30d, 90d, 1Y, ALL)
# Test get_portfolio_allocation computes correct weights
# Test sector fallback mapping for known symbols
# Test empty positions returns empty allocation
```

## Dependencies to Install

**Backend** (add to `requirements.txt`):
```
numpy>=1.26.0
```

Note: `yfinance` should already be installed from previous stages. `numpy` may already be present as a transitive dependency but add it explicitly for the risk calculations.

**Frontend** (no new packages needed — SVG donut chart is hand-rolled, lightweight-charts already installed, all UI built with existing TailwindCSS)

## Files to Create
- `backend/analytics.py`
- `backend/risk.py`
- `backend/tests/test_analytics.py`
- `backend/tests/test_risk.py`
- `dashboard/src/pages/AnalyticsPage.tsx`
- `dashboard/src/components/analytics/AllocationChart.tsx`
- `dashboard/src/components/analytics/PnLChart.tsx`
- `dashboard/src/components/analytics/RiskMetricsGrid.tsx`
- `dashboard/src/components/analytics/PositionSizer.tsx`
- `dashboard/src/components/analytics/TradeJournal.tsx`

## Files to Modify
- `backend/main.py` — add analytics/risk endpoints (`GET /api/analytics/portfolio`, `GET /api/analytics/risk`, `GET /api/analytics/pnl`, `POST /api/analytics/position-size`), add trade annotate endpoint (`PUT /api/trades/{id}/annotate`), import analytics and risk modules
- `backend/database.py` — add `update_trade_annotation()` function that loads a trade by ID, merges notes/tags into the JSON blob, and saves
- `backend/models.py` — add `PortfolioAllocation`, `SectorBreakdown`, `RiskMetrics`, `PnLDataPoint`, `PnLSummary`, `PositionSizeRequest`, `PositionSizeResult`, `TradeAnnotation` models; extend `Trade` with `notes: Optional[str] = None` and `tags: list[str] = Field(default_factory=list)`
- `dashboard/src/App.tsx` — add `'analytics'` case to the `PageSwitch` component, import `AnalyticsPage`
- `dashboard/src/components/layout/Sidebar.tsx` — add Analytics nav item with chart-pie icon (insert between Rules and Settings), add `'analytics'` to `AppRoute` usage
- `dashboard/src/store/index.ts` — add `useAnalyticsStore` with state for: portfolio allocation, risk metrics, pnl data, selected period, position sizer results, trade journal filters
- `dashboard/src/services/api.ts` — add `fetchPortfolioAnalytics()`, `fetchRiskMetrics()`, `fetchPnlData(period)`, `calcPositionSize(body)`, `annotateTrade(id, body)` API functions
- `dashboard/src/types/index.ts` — add `PortfolioAllocation`, `SectorBreakdown`, `RiskMetrics`, `PnLDataPoint`, `PnLSummary`, `PositionSizeRequest`, `PositionSizeResult`, `TradeAnnotation`, `AnalyticsPeriod` types; extend `Trade` type with `notes?: string` and `tags?: string[]`; add `'analytics'` to `AppRoute` union type

## Definition of Done
1. Analytics page shows allocation donut chart with correct weights per position
2. Sector breakdown shows positions grouped by sector (toggle between symbol/sector view)
3. Daily P&L histogram displays with green/red bars using lightweight-charts
4. Cumulative return line shows portfolio performance over time as overlay
5. Risk metrics (beta, VaR, Sharpe, Sortino, drawdown) compute and display correctly with color coding
6. Position sizer calculates correct values for both Kelly Criterion and Fixed Fractional methods
7. Trade journal shows trades with ability to add/edit notes inline
8. Trade tags can be added (from presets or custom) and used to filter the trade list
9. Period selector (30D/90D/1Y/ALL) filters P&L data and refreshes the chart
10. Export to CSV works for trade journal (downloads a properly formatted CSV file)
11. `pytest backend/tests/test_risk.py backend/tests/test_analytics.py` passes all tests with known values
12. Works in both IBKR and SIM modes — SIM_MODE uses sim positions/orders, IBKR mode uses live data

## Important Notes
- Do NOT break existing functionality. This is additive.
- Risk metrics need actual positions/trades to be meaningful. In SIM_MODE with mock data, use the sim positions from `sim_engine.get_positions()` and sim orders from `sim_engine.get_orders()`.
- Sector mapping: cache `yfinance` `ticker.info` results in a module-level dict. For common symbols (AAPL=Technology, MSFT=Technology, TSLA=Consumer Cyclical, SPY=ETF, etc.), hardcode fallbacks so the app works even when yfinance is slow or unavailable.
- Portfolio beta: if no positions held, return `null` (not an error). The frontend shows "N/A" for null beta.
- For the allocation chart, use SVG-based donut (simpler, no new dependencies). Use `<circle>` with `stroke-dasharray` and `stroke-dashoffset` for segments, or `<path>` with arc commands.
- Use lightweight-charts `addHistogramSeries()` for the P&L bar chart (same library already used for all other charts in the platform).
- Reuse the `KPICard` component from `dashboard/src/components/tradebot/KPICard.tsx` for risk metrics display — it already supports `positive` prop for green/red coloring and `highlight` for blue glow.
- Keep the terminal dark theme consistent for all new UI components. Use existing Tailwind classes: `bg-terminal-surface`, `border-terminal-border`, `text-terminal-text`, `text-terminal-ghost`, `text-terminal-dim`, `text-terminal-green`, `text-terminal-red`, `text-terminal-amber`, `text-terminal-blue`, `bg-terminal-input`, `bg-terminal-muted`.
- The existing `Trade` model stores data as a JSON blob in the `data` column. Adding `notes` and `tags` fields to the Pydantic model means they serialize into the same JSON blob automatically — no schema migration needed.
- For the P&L chart, both the histogram and cumulative line should use the same time axis. The cumulative line should use a separate price scale on the right side.
- Position sizer Kelly fraction should be capped at 0.25 (quarter Kelly) for safety — full Kelly is too aggressive for real trading.
- Test everything with `SIM_MODE=true` and `MOCK_MODE=true` (no IBKR needed).
