# Stage 5 Session Prompt: Alerts & Notifications

You are working on a trading platform built with **FastAPI** (backend) and **React 18 + TypeScript + Zustand + TailwindCSS** (dashboard). The project is at `C:\Users\segev\sdvesdaW\trading`.

## Current State
- **Backend** (`backend/`): FastAPI with 40+ endpoints, IBKR integration (ib_insync), 8 technical indicators (RSI, SMA, EMA, MACD, BBANDS, ATR, STOCH, PRICE), rule engine with AND/OR logic + cooldown, order execution, virtual trading simulation, historical replay, mock GBM data, real-time WebSocket, SQLite persistence. Auth scaffold with JWT + demo user. Settings system with JSON blob storage. Screener with cached bar data. Backtesting engine with bar-by-bar evaluation, equity curves, and performance metrics.
- **Dashboard** (`dashboard/`): React 18 + Vite + Zustand + TailwindCSS. Pages: Dashboard, TradeBotPage, MarketPage, SimulationPage, SettingsPage, ScreenerPage, BacktestPage. Uses lightweight-charts for candlesticks. Dark terminal theme. Toast notification system, error boundaries, loading skeletons. Advanced charting with multi-pane layout, drawing tools, volume panel, crosshair sync.
- **Database**: SQLite with tables: `rules` (id, data JSON), `trades` (id, rule_id, symbol, action, timestamp, data JSON), `sim_account`, `sim_positions`, `sim_orders`, `users`, `screener_presets`, `backtests`. All tables have `user_id` column.
- **3 operating modes**: IBKR Live, IBKR Paper, Simulation (offline with mock data).
- **WebSocket**: Two WS endpoints — `/ws` (general events: bot, filled, ibkr_state, signal, replay_bar, replay_done, sim_order, sim_reset) and `/ws/market-data` (per-symbol quote push). `ConnectionManager` class broadcasts to all connected clients. `wsService` singleton on frontend with typed subscribe/dispatch.

## What to Build (Stage 5)

### 1. Alert Engine (`backend/alert_engine.py`)

**Create `backend/alert_engine.py`:**
- Independent async loop that runs regardless of whether bot_runner is started or stopped
- Follow the same pattern as `bot_runner.py`: module-level `_running`, `_task`, `_broadcast` callback, `start()`/`stop()`/`set_broadcast()` functions
- Configurable check interval via `cfg.ALERT_CHECK_INTERVAL_SECONDS` (default 30)
- The main `_loop()` cycles every `ALERT_CHECK_INTERVAL_SECONDS`:
  1. Fetch all enabled alerts from database (call `get_alerts()`)
  2. Collect unique symbols from enabled alerts
  3. For each symbol, fetch current price: try `get_latest_price(symbol)` from `market_data.py` if IBKR connected, else use `get_mock_price(symbol)` from `mock_data.py`, else try yfinance `yf.Ticker(symbol).fast_info.last_price` in a thread
  4. For each alert, evaluate whether condition is met:
     - **Price alerts** (indicator="PRICE"): compare current price against threshold using the alert's operator (>, <, >=, <=, crosses_above, crosses_below)
     - **Technical alerts** (indicator=RSI/SMA/EMA/etc.): fetch 60D daily bars (use yahoo finance `_yf_bars` pattern or mock), build DataFrame, call `indicators.calculate(df, indicator, params)`, compare last value against threshold
  5. When an alert fires:
     - Mark `last_triggered` timestamp on the alert
     - If `alert_type == "one_shot"`: set `enabled = False` (auto-disable)
     - If `alert_type == "recurring"`: check cooldown — skip if `last_triggered + cooldown_minutes > now`
     - Log to `alert_history` table via `save_alert_history()`
     - Broadcast `alert_fired` event via WebSocket `_broadcast()`
- Add a comment at the top of the loop: `# NOTE: Migrate to Celery/RQ if scanning >500 symbols in multi-user mode`
- For cross detection on price alerts: maintain a simple `_prev_prices: dict[str, float]` cache at module level, compare previous vs current price relative to threshold

**Key implementation detail — reuse `_evaluate_condition` pattern from `rule_engine.py`:**
```python
from indicators import calculate, detect_cross, resolve_value
from models import Condition

def _evaluate_alert_condition(cond: Condition, current_price: float, df: pd.DataFrame | None) -> bool:
    """Evaluate a single alert condition. For PRICE alerts, df can be None."""
    if cond.indicator == "PRICE":
        # Simple scalar comparison against current price
        ...
    else:
        # Technical indicator — requires df
        if df is None or df.empty:
            return False
        series = calculate(df, cond.indicator, cond.params)
        ...
```

### 2. Alert Models (`backend/models.py`)

**Add to `backend/models.py`:**

```python
# ---------------------------------------------------------------------------
# Alert models
# ---------------------------------------------------------------------------

class Alert(BaseModel):
    id: str = Field(default_factory=lambda: str(_uuid.uuid4()))
    user_id: str = "demo"
    name: str
    symbol: str
    condition: Condition  # reuses existing Condition model
    alert_type: Literal["one_shot", "recurring"] = "one_shot"
    cooldown_minutes: int = 60  # only used for recurring alerts
    enabled: bool = True
    last_triggered: Optional[str] = None  # ISO datetime
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AlertCreate(BaseModel):
    name: str
    symbol: str
    condition: Condition
    alert_type: Literal["one_shot", "recurring"] = "one_shot"
    cooldown_minutes: int = 60
    enabled: bool = True


class AlertUpdate(BaseModel):
    name: Optional[str] = None
    symbol: Optional[str] = None
    condition: Optional[Condition] = None
    alert_type: Optional[Literal["one_shot", "recurring"]] = None
    cooldown_minutes: Optional[int] = None
    enabled: Optional[bool] = None


class AlertHistory(BaseModel):
    id: str = Field(default_factory=lambda: str(_uuid.uuid4()))
    alert_id: str
    alert_name: str
    symbol: str
    condition_summary: str  # e.g. "AAPL PRICE > 250.00"
    price_at_trigger: float
    fired_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
```

**Add `datetime` and `timezone` imports** at the top of models.py (they are not currently imported).

### 3. Database Tables (`backend/database.py`)

**Add to `backend/database.py`:**

New table schemas (same JSON blob pattern as rules):
```python
_CREATE_ALERTS = """
CREATE TABLE IF NOT EXISTS alerts (
    id      TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'demo',
    data    TEXT NOT NULL
);
"""

_CREATE_ALERT_HISTORY = """
CREATE TABLE IF NOT EXISTS alert_history (
    id        TEXT PRIMARY KEY,
    alert_id  TEXT NOT NULL,
    fired_at  TEXT NOT NULL,
    data      TEXT NOT NULL
);
"""
```

Add both `CREATE` statements to `init_db()`.

**Add CRUD functions:**
```python
# ---------------------------------------------------------------------------
# Alerts CRUD
# ---------------------------------------------------------------------------

async def get_alerts(user_id: str = "demo") -> list[Alert]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT data FROM alerts WHERE user_id=?", (user_id,)) as cursor:
            rows = await cursor.fetchall()
    return [Alert.model_validate(json.loads(r[0])) for r in rows]


async def get_alert(alert_id: str) -> Alert | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT data FROM alerts WHERE id=?", (alert_id,)) as cur:
            row = await cur.fetchone()
    return Alert.model_validate(json.loads(row[0])) if row else None


async def save_alert(alert: Alert) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO alerts (id, user_id, data) VALUES (?, ?, ?)",
            (alert.id, alert.user_id, alert.model_dump_json()),
        )
        await db.commit()


async def delete_alert(alert_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
        await db.commit()
        return cur.rowcount > 0


async def get_alert_history(user_id: str = "demo", limit: int = 100) -> list[AlertHistory]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT ah.data FROM alert_history ah
               JOIN alerts a ON ah.alert_id = a.id
               WHERE a.user_id = ?
               ORDER BY ah.fired_at DESC LIMIT ?""",
            (user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
    return [AlertHistory.model_validate(json.loads(r[0])) for r in rows]


async def save_alert_history(entry: AlertHistory) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO alert_history (id, alert_id, fired_at, data) VALUES (?, ?, ?, ?)",
            (entry.id, entry.alert_id, entry.fired_at, entry.model_dump_json()),
        )
        await db.commit()
```

**Update imports** at the top of database.py — add `Alert, AlertHistory` to the import from `models`.

### 4. Config (`backend/config.py`)

**Add to the `Config` class:**
```python
    # ── Alerts ────────────────────────────────────────────────────────────────
    ALERT_CHECK_INTERVAL_SECONDS: int = int(os.getenv("ALERT_CHECK_INTERVAL_SECONDS", "30"))
```

Place it after the `BOT_INTERVAL_SECONDS` line.

### 5. Backend Endpoints (`backend/main.py`)

**Add alert endpoints to `main.py`:**

```python
# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@app.get("/api/alerts")
async def list_alerts():
    alerts = await get_alerts()
    return [a.model_dump() for a in alerts]


@app.post("/api/alerts", status_code=201)
async def create_alert_route(body: AlertCreate):
    alert = Alert(**body.model_dump())
    await save_alert(alert)
    return alert.model_dump()


@app.get("/api/alerts/history")
async def list_alert_history(limit: int = 100):
    history = await get_alert_history(limit=limit)
    return [h.model_dump() for h in history]


@app.put("/api/alerts/{alert_id}")
async def update_alert_route(alert_id: str, body: AlertUpdate):
    alert = await get_alert(alert_id)
    if not alert:
        raise HTTPException(404, "Alert not found")
    updated = alert.model_copy(update=body.model_dump(exclude_none=True))
    await save_alert(updated)
    return updated.model_dump()


@app.delete("/api/alerts/{alert_id}")
async def delete_alert_route(alert_id: str):
    if not await delete_alert(alert_id):
        raise HTTPException(404, "Alert not found")
    return {"deleted": True}


@app.post("/api/alerts/{alert_id}/toggle")
async def toggle_alert(alert_id: str):
    alert = await get_alert(alert_id)
    if not alert:
        raise HTTPException(404, "Alert not found")
    alert.enabled = not alert.enabled
    await save_alert(alert)
    return {"id": alert_id, "enabled": alert.enabled}


@app.post("/api/alerts/test")
async def test_alert_notification():
    """Fire a test alert notification for UI testing."""
    test_payload = {
        "type": "alert_fired",
        "alert_id": "test",
        "name": "Test Alert",
        "symbol": "TEST",
        "condition_summary": "Test notification",
        "price": 0.0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await _broadcast(test_payload)
    return {"sent": True}
```

**IMPORTANT**: Place the `GET /api/alerts/history` route BEFORE the `PUT /api/alerts/{alert_id}` route. FastAPI matches routes top-to-bottom, and `/api/alerts/history` would be captured by `/api/alerts/{alert_id}` if `{alert_id}` comes first. The correct ordering is:
1. `GET /api/alerts` (list)
2. `POST /api/alerts` (create)
3. `GET /api/alerts/history` (history — fixed path, must come before `{alert_id}`)
4. `PUT /api/alerts/{alert_id}` (update)
5. `DELETE /api/alerts/{alert_id}` (delete)
6. `POST /api/alerts/{alert_id}/toggle` (toggle)
7. `POST /api/alerts/test` (test — fixed path, must come before `{alert_id}` if you use POST)

**Update imports in `main.py`:**
- Add to database imports: `get_alerts, get_alert, save_alert, delete_alert, get_alert_history, save_alert_history`
- Add to models imports: `Alert, AlertCreate, AlertUpdate, AlertHistory`
- Add `import alert_engine` at the top alongside `import bot_runner`

**Update lifespan in `main.py`:**
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──
    await init_db()
    await sim_engine.initialize()

    bot_runner.set_broadcast(_broadcast)
    alert_engine.set_broadcast(_broadcast)    # <-- ADD THIS
    sim_engine.set_broadcast(_broadcast)
    replay_engine.set_broadcast(_broadcast)
    ibkr.set_broadcast(_broadcast)

    # ... existing fill handler, IBKR connect ...

    # Start alert engine (runs independently of bot)
    await alert_engine.start()                # <-- ADD THIS

    yield

    # ── Shutdown ──
    await alert_engine.stop()                 # <-- ADD THIS
    await bot_runner.stop()
    await replay_engine.stop()
    await ibkr.disconnect()
```

### 6. WebSocket Integration

**The `alert_fired` event payload broadcast from `alert_engine.py`:**
```python
await _broadcast({
    "type": "alert_fired",
    "alert_id": alert.id,
    "name": alert.name,
    "symbol": alert.symbol,
    "condition_summary": condition_summary,  # e.g. "AAPL PRICE > 250.00"
    "price": current_price,
    "timestamp": datetime.now(timezone.utc).isoformat(),
})
```

This follows the exact same pattern as existing `bot`, `filled`, and `signal` events — all broadcast through the same `ConnectionManager.broadcast()` method on the `/ws` WebSocket.

### 7. Frontend Types (`dashboard/src/types/index.ts`)

**Add to `dashboard/src/types/index.ts`:**

```typescript
// ── Alerts ──────────────────────────────────────────────────────────────────

export type AlertType = 'one_shot' | 'recurring'

export interface Alert {
  id: string
  user_id: string
  name: string
  symbol: string
  condition: Condition  // reuses existing Condition interface
  alert_type: AlertType
  cooldown_minutes: number
  enabled: boolean
  last_triggered?: string
  created_at: string
}

export interface AlertCreate {
  name: string
  symbol: string
  condition: Condition
  alert_type?: AlertType
  cooldown_minutes?: number
  enabled?: boolean
}

export interface AlertUpdate {
  name?: string
  symbol?: string
  condition?: Condition
  alert_type?: AlertType
  cooldown_minutes?: number
  enabled?: boolean
}

export interface AlertHistory {
  id: string
  alert_id: string
  alert_name: string
  symbol: string
  condition_summary: string
  price_at_trigger: number
  fired_at: string
}

export interface AlertFiredEvent {
  type: 'alert_fired'
  alert_id: string
  name: string
  symbol: string
  condition_summary: string
  price: number
  timestamp: string
}
```

**Update `WsEventType` union — add `'alert_fired'`:**
```typescript
export type WsEventType =
  | 'pong'
  | 'ibkr_state'
  | 'bot'
  | 'signal'
  | 'filled'
  | 'error'
  | 'bar'
  | 'quote'
  | 'replay_bar'
  | 'replay_done'
  | 'sim_order'
  | 'sim_reset'
  | 'alert_fired'    // <-- ADD THIS
```

**Update `AppRoute` type — add `'alerts'`:**
```typescript
export type AppRoute = 'dashboard' | 'tradebot' | 'market' | 'simulation' | 'rules' | 'settings' | 'alerts'
```

### 8. API Functions (`dashboard/src/services/api.ts`)

**Add to `dashboard/src/services/api.ts`:**

```typescript
import type { Alert, AlertCreate, AlertUpdate, AlertHistory } from '@/types'

// ── Alerts ───────────────────────────────────────────────────────────────────

export const fetchAlerts       = () => get<Alert[]>('/api/alerts')
export const createAlert       = (body: AlertCreate) => post<Alert>('/api/alerts', body)
export const updateAlert       = (id: string, body: AlertUpdate) => put<Alert>(`/api/alerts/${id}`, body)
export const deleteAlert       = (id: string) => del<{ deleted: boolean }>(`/api/alerts/${id}`)
export const toggleAlert       = (id: string) => post<{ id: string; enabled: boolean }>(`/api/alerts/${id}/toggle`)
export const fetchAlertHistory = (limit = 100) => get<AlertHistory[]>(`/api/alerts/history?limit=${limit}`)
export const testAlertNotification = () => post<{ sent: boolean }>('/api/alerts/test')
```

### 9. Alert Store (`dashboard/src/store/index.ts`)

**Add a new `useAlertStore` to `dashboard/src/store/index.ts`:**

```typescript
import type { Alert, AlertHistory } from '@/types'

// ── Alert store ──────────────────────────────────────────────────────────────

interface AlertState {
  alerts:      Alert[]
  history:     AlertHistory[]
  unreadCount: number
  recentFired: AlertFiredEvent[]  // last 5 fired alerts for bell dropdown

  setAlerts:      (a: Alert[]) => void
  addAlert:       (a: Alert) => void
  updateAlert:    (a: Alert) => void
  removeAlert:    (id: string) => void
  setHistory:     (h: AlertHistory[]) => void
  pushFired:      (ev: AlertFiredEvent) => void
  markRead:       () => void
}

export const useAlertStore = create<AlertState>((set) => ({
  alerts:      [],
  history:     [],
  unreadCount: 0,
  recentFired: [],

  setAlerts:   (a) => set({ alerts: a }),
  addAlert:    (a) => set((s) => ({ alerts: [a, ...s.alerts] })),
  updateAlert: (a) => set((s) => ({ alerts: s.alerts.map((x) => (x.id === a.id ? a : x)) })),
  removeAlert: (id) => set((s) => ({ alerts: s.alerts.filter((x) => x.id !== id) })),
  setHistory:  (h) => set({ history: h }),
  pushFired:   (ev) => set((s) => ({
    recentFired: [ev, ...s.recentFired].slice(0, 5),
    unreadCount: s.unreadCount + 1,
  })),
  markRead:    () => set({ unreadCount: 0 }),
}))
```

**Import `AlertFiredEvent` from `@/types` at the top of the store file.**

### 10. WebSocket Hook (`dashboard/src/hooks/useWebSocket.ts`)

**Add `alert_fired` handler in `useWebSocket()`:**

After the existing `unFill` subscription, add:

```typescript
import { useAlertStore } from '@/store'
import type { AlertFiredEvent } from '@/types'

// Inside useWebSocket():
const pushFired = useAlertStore((s) => s.pushFired)

// Inside the useEffect, after unFill:
const unAlertFired = wsService.subscribe('alert_fired', (ev: WsEvent) => {
  const fired = ev as unknown as AlertFiredEvent
  pushFired(fired)

  // Browser push notification (if permission granted)
  if (Notification.permission === 'granted') {
    new Notification(`Alert: ${fired.name}`, {
      body: `${fired.symbol} — ${fired.condition_summary}\nPrice: $${fired.price.toFixed(2)}`,
      icon: '/favicon.ico',
    })
  }
})

// In the cleanup return:
return () => {
  unIBKR()
  unBot()
  unFill()
  unAlertFired()  // <-- ADD
  unReplay()
  unReplayDone()
  wsService.disconnect()
}
```

**Note**: The toast notification for `alert_fired` should also be triggered here. If a toast system (from Stage 1) is wired into the hook or available globally, call it:
```typescript
// If useToast is available as a global/context:
toast.info(`Alert fired: ${fired.name} — ${fired.symbol} $${fired.price.toFixed(2)}`)
```
If the toast system is context-based (not usable in hooks), dispatch the toast via the store or a global event emitter. Alternatively, use `window.dispatchEvent(new CustomEvent('toast', { detail: { type: 'info', message: ... } }))` and listen in the ToastProvider.

### 11. Alerts Page (`dashboard/src/pages/AlertsPage.tsx`)

**Create `dashboard/src/pages/AlertsPage.tsx`:**
- Two tabs at the top: **"Active Alerts"** and **"History"**
- Tab styling consistent with terminal theme (underline active tab, `text-terminal-blue`)
- **Active Alerts tab:**
  - Renders `<AlertList />` component
  - "Create Alert" button (top-right, terminal-blue styled) opens `<AlertForm />` in a modal/slide-over
- **History tab:**
  - Renders `<AlertHistoryTable />` component
- On mount: fetch alerts via `fetchAlerts()` and history via `fetchAlertHistory()`, store in `useAlertStore`
- Loading skeleton while fetching

### 12. Alert Form (`dashboard/src/components/alerts/AlertForm.tsx`)

**Create `dashboard/src/components/alerts/AlertForm.tsx`:**
- Modal or slide-over panel (consistent with terminal theme)
- Fields:
  - **Name** — text input, auto-generates default like `"AAPL > $250"` or `"SPY RSI(14) < 30"` based on selected condition
  - **Symbol** — text input with autocomplete dropdown pulling symbols from `useMarketStore().watchlists` (flatten all watchlist symbols into a unique list)
  - **Condition Type** — dropdown selector: `Price Level`, `RSI`, `SMA`, `EMA`, `MACD`, `BBANDS`, `ATR`, `STOCH`
  - **For Price Level**: operator dropdown (`above (>)`, `below (<)`, `crosses above`, `crosses below`) + price value input
  - **For technical indicators**: show relevant params (RSI: length; MACD: fast/slow/signal; BBANDS: length/std/band; SMA/EMA: length; ATR: length; STOCH: k/d/smooth_k) + operator dropdown + threshold value input
  - **Alert Type** — radio/toggle: `One-shot` (fires once then disables) vs `Recurring` (stays enabled)
  - **Cooldown** — number input for minutes, only shown when `Recurring` is selected
- "Create" / "Save" button calls `createAlert()` or `updateAlert()` API
- "Cancel" button closes the form
- Toast on success/failure
- When editing an existing alert, pre-fill all fields from the alert object

### 13. Alert List (`dashboard/src/components/alerts/AlertList.tsx`)

**Create `dashboard/src/components/alerts/AlertList.tsx`:**
- Renders a list/table of active alerts from `useAlertStore().alerts`
- Each row shows: name, symbol, condition summary (formatted), alert type badge, enabled toggle, last triggered time
- Actions per row: toggle (enable/disable switch), edit (pencil icon, opens AlertForm), delete (trash icon with confirmation)
- Empty state: "No alerts configured. Create one to get notified."
- Terminal theme styling: `bg-terminal-surface`, `border-terminal-border`, monospace text for values

### 14. Alert History Table (`dashboard/src/components/alerts/AlertHistoryTable.tsx`)

**Create `dashboard/src/components/alerts/AlertHistoryTable.tsx`:**
- Table showing fired alerts from `useAlertStore().history`
- Columns: Fired At (formatted timestamp), Alert Name, Symbol, Condition, Price at Trigger
- Sort by `fired_at` descending (most recent first)
- Empty state: "No alerts have fired yet."
- Terminal theme table styling consistent with existing tables (e.g., trade log in TradeBotPage)

### 15. Alert Bell (`dashboard/src/components/alerts/AlertBell.tsx`)

**Create `dashboard/src/components/alerts/AlertBell.tsx`:**
- Bell icon (SVG) with optional red badge showing `unreadCount` from `useAlertStore`
- Badge is a small red circle with white number, positioned top-right of the bell icon (absolute positioned)
- Badge hidden when `unreadCount === 0`
- On click: toggle a dropdown panel showing the 5 most recent fired alerts from `recentFired`
- Each item in dropdown: alert name, symbol, price, relative timestamp ("2m ago", "1h ago")
- "View All" link at bottom of dropdown — calls `setRoute('alerts')` to navigate to Alerts page
- On dropdown open: call `markRead()` to reset unread count
- Click outside dropdown closes it (use a ref + click-outside listener)
- Dropdown styled with terminal theme: `bg-terminal-surface`, `border-terminal-border`, shadow

**Place in Header.tsx** — between the mode badges `<div>` and the IBKR button:
```tsx
import AlertBell from '@/components/alerts/AlertBell'

// In the Header JSX, after the mode badges div and before the IBKR button:
<AlertBell />
```

### 16. Browser Push Notifications

**In the alert creation flow (AlertForm.tsx `onSubmit`):**
```typescript
// Request notification permission on first alert creation
if (Notification.permission === 'default') {
  Notification.requestPermission()
}
```

**In `useWebSocket.ts` (already covered in section 10):**
- When `alert_fired` is received and `Notification.permission === 'granted'`, fire `new Notification(...)`
- If permission is `'denied'` or `'default'`, skip gracefully — no error, no prompt

### 17. Chart Integration (connects to Stage 2)

**If Stage 2 added a chart context menu or toolbar:**
- Add a "Create Alert at $X" option to the chart's right-click context menu
- When clicked: open `AlertForm` pre-filled with:
  - `symbol`: current chart symbol from `useMarketStore().selectedSymbol`
  - `condition.indicator`: `"PRICE"`
  - `condition.operator`: `">"`
  - `condition.value`: the price level at the click position (from `series.coordinateToPrice(y)`)
  - `name`: auto-generated `"AAPL > $250.00"`

**If Stage 2 did NOT add a context menu:**
- Add a button to the chart toolbar: bell icon with tooltip "Create Price Alert"
- When clicked: open AlertForm pre-filled with symbol and current price as the threshold

### 18. App Routing (`dashboard/src/App.tsx`)

**Add AlertsPage to the route switch:**

```typescript
import AlertsPage from '@/pages/AlertsPage'

// In PageSwitch():
case 'alerts': return <AlertsPage />
```

### 19. Sidebar Navigation (`dashboard/src/components/layout/Sidebar.tsx`)

**Add Alerts nav item to `NAV_ITEMS` array** — place it after "Rules" and before "Settings":

```typescript
{
  route: 'alerts',
  label: 'Alerts',
  icon: (
    <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
      <path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.63-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2zm-2 1H8v-6c0-2.48 1.51-4.5 4-4.5s4 2.02 4 4.5v6z" />
    </svg>
  ),
},
```

### 20. Header Update (`dashboard/src/components/layout/Header.tsx`)

**Update `PAGE_LABELS` map:**
```typescript
const PAGE_LABELS: Record<string, string> = {
  dashboard:  'Dashboard',
  tradebot:   'TradeBot Command Center',
  market:     'Market Analyzer',
  simulation: 'Simulation Engine',
  rules:      'Automation Rules',
  alerts:     'Alerts & Notifications',   // <-- ADD
  settings:   'Settings',
}
```

**Add AlertBell component** between mode badges and IBKR button:
```tsx
import AlertBell from '@/components/alerts/AlertBell'

// In JSX, after the mode badges <div> and before the IBKR <button>:
<AlertBell />
```

### 21. Tests (`backend/tests/test_alerts.py`)

**Create `backend/tests/test_alerts.py`:**

Test cases:
- **`test_alert_creation`**: POST /api/alerts creates an alert, GET /api/alerts returns it
- **`test_alert_toggle`**: POST /api/alerts/{id}/toggle flips enabled state
- **`test_alert_update`**: PUT /api/alerts/{id} updates fields
- **`test_alert_delete`**: DELETE /api/alerts/{id} removes it
- **`test_price_alert_evaluation`**: Unit test `_evaluate_alert_condition()` with PRICE indicator, operator ">", value 250, current_price 251 -> True; current_price 249 -> False
- **`test_technical_alert_evaluation`**: Unit test with RSI indicator, mock DataFrame with known RSI values, verify condition fires correctly
- **`test_one_shot_disables_after_fire`**: After a one_shot alert fires, verify `enabled` is set to `False`
- **`test_recurring_respects_cooldown`**: A recurring alert that fired 5 minutes ago with cooldown_minutes=60 should NOT fire again
- **`test_recurring_fires_after_cooldown`**: A recurring alert that fired 70 minutes ago with cooldown_minutes=60 SHOULD fire again
- **`test_alert_history_logged`**: When an alert fires, an AlertHistory entry is saved
- **`test_test_notification_endpoint`**: POST /api/alerts/test returns `{sent: true}` and broadcasts

Use `pytest` + FastAPI `TestClient` (via `httpx`). For unit tests of the evaluation logic, import `_evaluate_alert_condition` directly.

## Dependencies to Install

**Backend** (add to `requirements.txt` if not already present):
```
# No new dependencies — uses existing aiosqlite, pydantic, yfinance, pandas, numpy
```

**Frontend** (no new packages needed — all UI is built with existing TailwindCSS + Zustand):
```
# No new dependencies
```

## Files to Create
- `backend/alert_engine.py`
- `backend/tests/test_alerts.py`
- `dashboard/src/pages/AlertsPage.tsx`
- `dashboard/src/components/alerts/AlertForm.tsx`
- `dashboard/src/components/alerts/AlertList.tsx`
- `dashboard/src/components/alerts/AlertHistoryTable.tsx`
- `dashboard/src/components/alerts/AlertBell.tsx`

## Files to Modify
- `backend/main.py` — add alert endpoints (GET/POST/PUT/DELETE /api/alerts, toggle, history, test), import alert_engine, start/stop alert engine in lifespan, add alert_fired WS broadcast setup
- `backend/database.py` — add `alerts` + `alert_history` table creation in `init_db()`, add CRUD functions (get_alerts, get_alert, save_alert, delete_alert, get_alert_history, save_alert_history), update imports from models
- `backend/models.py` — add Alert, AlertCreate, AlertUpdate, AlertHistory models, add datetime/timezone imports
- `backend/config.py` — add `ALERT_CHECK_INTERVAL_SECONDS` to Config class
- `dashboard/src/App.tsx` — import AlertsPage, add `case 'alerts'` to PageSwitch
- `dashboard/src/components/layout/Sidebar.tsx` — add Alerts nav item (bell icon) between Rules and Settings
- `dashboard/src/components/layout/Header.tsx` — add `'alerts'` to PAGE_LABELS, add AlertBell component to JSX
- `dashboard/src/hooks/useWebSocket.ts` — add `alert_fired` event subscription, trigger pushFired + browser Notification
- `dashboard/src/store/index.ts` — add `useAlertStore` (alerts, history, unreadCount, recentFired, pushFired, markRead)
- `dashboard/src/services/api.ts` — add fetchAlerts, createAlert, updateAlert, deleteAlert, toggleAlert, fetchAlertHistory, testAlertNotification
- `dashboard/src/types/index.ts` — add Alert, AlertCreate, AlertUpdate, AlertHistory, AlertFiredEvent types, add 'alert_fired' to WsEventType, add 'alerts' to AppRoute

## Definition of Done
1. User can create a price alert "Notify when AAPL > $250"
2. User can create a technical alert "Notify when RSI(14) for SPY < 30"
3. When alert fires: in-app toast appears immediately
4. When alert fires: bell badge increments unread count
5. When alert fires: alert is logged in history with timestamp and price
6. Browser push notification appears if permission granted
7. One-shot alerts auto-disable after firing
8. Recurring alerts respect cooldown period
9. Alerts page shows active alerts with enable/disable toggle
10. Alert history shows fired alerts with all details
11. Bell dropdown shows recent alerts, "View All" navigates to page
12. Alert engine runs independently of bot (works even when bot is stopped)
13. `pytest backend/tests/test_alerts.py` passes all tests

## Important Notes
- Do NOT break existing functionality. This is additive.
- Alert engine is INDEPENDENT of bot_runner — must run even when bot is stopped. It starts automatically in the lifespan hook.
- Reuse the `Condition` model from `models.py` (same as rules and backtest). An alert has exactly ONE condition (not a list), which simplifies evaluation.
- Follow the same async loop pattern as `bot_runner._loop()` for the alert engine.
- Use the same WebSocket broadcast pattern as existing events (`_broadcast` callback set by `main.py`).
- The toast system from Stage 1 is the primary in-app notification mechanism.
- Browser notifications are a progressive enhancement — request permission on first alert creation, fire when event received, graceful no-op if denied.
- For price data in the alert engine: try IBKR `get_latest_price()` first, fall back to `get_mock_price()` if mock mode, fall back to yfinance `fast_info.last_price` as last resort.
- For technical indicator data: fetch 60D daily bars via yfinance (same pattern as `_yf_bars` in main.py) or mock data, build DataFrame, pass to `indicators.calculate()`.
- Keep the terminal dark theme consistent for all new UI components.
- Test everything with `SIM_MODE=true` and `MOCK_MODE=true` (no IBKR needed).
