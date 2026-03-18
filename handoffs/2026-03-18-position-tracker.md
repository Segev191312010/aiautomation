# Session Handoff — 2026-03-18

## What Was Built

**Position Tracker & Exit Management System** — a new module that monitors open positions and automatically generates exit orders based on ATR stops and indicator signals.

### New Files
- `backend/position_tracker.py` (229 lines) — core exit engine
- `backend/tests/test_position_tracker.py` — test suite
- `backend/data/universes/us_all.json` — **empty placeholder** for a full US equity universe (needs to be populated)
- `dashboard/src/components/auth/RegisterPage.tsx` — user registration UI (new, uncommitted)

### Modified Files

| File | Change Summary |
|------|---------------|
| `backend/models.py` | Added `OpenPosition` model; added `"us_all"` to valid universes |
| `backend/database.py` | Added `open_positions` table + full CRUD (`save_open_position`, `get_open_positions`, `get_open_position`, `delete_open_position`) |
| `backend/config.py` | Added `ATR_STOP_MULT` (3.0), `ATR_TRAIL_MULT` (2.0), `POSITION_SIZE_PCT` (0.5%), `BOT_INTERVAL_SECONDS` (900) |
| `backend/bot_runner.py` | Wired in exit phase (`_process_exits`), watermark updates, liquidity pre-screen for `us_all` universe, dynamic position sizing via NetLiquidation |
| `backend/order_executor.py` | MKT→LIMIT conversion for extended-hours orders; removed hardcoded bracket orders |
| `backend/indicators.py` | Added `MACD_SIGNAL` and `MACD_HIST` as selectable indicator lines in the rule engine |
| `backend/main.py` | Added `GET /api/positions/tracked` endpoint; wired `on_fill` hook to auto-register positions; fixed `asyncio.set_event_loop` for Python 3.14 compat |
| `backend/tests/conftest.py` | Minor fixture updates |

---

## Architecture: How the Exit System Works

```
Bot cycle (every 15 min)
  │
  ├── 1. Load open_positions from DB
  ├── 2. Fetch bars (open positions always included, regardless of rules)
  ├── 3. _process_exits()
  │       ├── update_watermarks() → update high_watermark, persist
  │       └── for each position:
  │             check_exits() → OR logic across 6 conditions:
  │               1. Hard stop  (entry - ATR_STOP_MULT × ATR14_at_entry)
  │               2. Trail stop (watermark - ATR_TRAIL_MULT × ATR14_current)
  │               3. EMA(21) cross below
  │               4. SMA(50) cross below
  │               5. RSI > 70 (exhaustion)
  │               6. MACD histogram < 0
  │             → if triggered: place exit order, delete from DB
  └── 4. Run entry rules as before
```

On fill: `on_fill` hook → `register_position()` → saves `OpenPosition` to DB with ATR snapshot and watermark.

---

## Outstanding Issues / Next Steps

1. **`us_all.json` is empty** — the bot will pre-screen an empty list. Needs to be populated with ~8,000+ US tickers. The pre-screener (`_prescreen_universe`) is ready and will filter down to liquid names (close > $5, avg vol > 500k) on first run.

2. **RegisterPage.tsx is uncommitted** — new auth registration UI. Needs to be wired into the login page toggle and the backend `POST /api/auth/register` endpoint (check if that endpoint exists).

3. **None of these changes are committed** — all ~715 line net additions are staged/unstaged. Run quality gates before committing:
   ```bash
   cd backend && python -m pytest tests/ -v
   cd dashboard && npm run typecheck && npm run build
   ```

4. **Extended-hours MKT→LIMIT conversion** — the order executor now fetches a live price and converts market orders to limit orders (±0.5% slippage) outside regular hours. Watch for edge cases where price fetch fails and falls through to a raw MKT order.

5. **`/api/positions/tracked`** — new endpoint returns live enriched stop levels. Consider wiring it into the TradeBotPage or a new "Positions" panel in the dashboard.
