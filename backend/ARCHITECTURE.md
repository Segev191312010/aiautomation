# Trading Bot System Architecture

## Overview

This is a sophisticated algorithmic trading system with AI-driven decision making, comprehensive risk management, and real-time market integration via Interactive Brokers (IBKR).

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           EXTERNAL SYSTEMS                                   │
├─────────────────┬─────────────────┬───────────────────────────────────────────┤
│  Interactive    │   Yahoo Finance │     Anthropic AI (Claude Models)          │
│  Brokers (TWS)  │   (Fallback)    │     - Sonnet 4 (Primary)                  │
│                 │                 │     - Haiku 4.5 (Fallback)                │
└────────┬────────┴────────┬────────┴──────────────────┬──────────────────────┘
         │                 │                           │
         ▼                 ▼                           ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CORE TRADING ENGINE                                  │
├───────────────┬───────────────┬───────────────┬───────────────┬─────────────┤
│  Bot Runner   │   Order       │   Position    │   Market      │   IBKR      │
│  (Main Loop)  │   Executor    │   Tracker     │   Data        │   Scanner   │
├───────────────┼───────────────┼───────────────┼───────────────┼─────────────┤
│ • Cycle mgmt  │ • IBKR API    │ • ATR stops   │ • Price feeds │ • Server-   │
│ • Rule eval   │ • Order mgmt  │ • Trail stops │ • Historical  │   side scan │
│ • AI merge    │ • Fill events │ • MA exits    │ • Fallback    │ • 6K+ stocks│
│ • Broadcast   │ • Resubmit    │ • Watermarks  │ • Caching     │ • <1s scan  │
└───────┬───────┴───────┬───────┴───────┬───────┴───────┬───────┴──────┬──────┘
        │               │               │               │              │
        ▼               ▼               ▼               ▼              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      AI / MACHINE LEARNING LAYER                             │
├───────────────┬───────────────┬───────────────┬───────────────┬─────────────┤
│  AI Advisor   │   Decision    │   AI Params   │   Portfolio   │   Candidate │
│               │   Ledger      │               │   Allocator   │   Selector  │
├───────────────┼───────────────┼───────────────┼───────────────┼─────────────┤
│ • Strategy    │ • Outcome     │ • Dynamic     │ • Risk        │ • Rule + AI │
│   optim       │   tracking    │   exit params │   distrib     │   merge     │
│ • Regime      │ • P&L         │ • ATR multi   │ • Position    │ • Priority  │
│   detect      │   scoring     │   tuning      │   sizing      │   queue     │
│ • Shadow/     │ • Hit rate    │ • Replay      │ • Sector      │ • Deduplic  │
│   live gate   │   analysis    │   config      │   limits      │             │
└───────┬───────┴───────┬───────┴───────┬───────┴───────┬───────┴──────┬──────┘
        │               │               │               │              │
        ▼               ▼               ▼               ▼              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        SAFETY & RISK LAYER                                   │
├───────────────┬───────────────┬───────────────────────────────────────────────┤
│  Safety Gate  │   Safety      │   Circuit Breaker                             │
│               │   Kernel      │                                               │
├───────────────┼───────────────┼───────────────────────────────────────────────┤
│ • Pre-trade   │ • Violation   │ • Emergency stop                              │
│   checks      │   detection   │ • Drawdown limits                             │
│ • Position    │ • Risk limits │ • Daily risk caps                             │
│   limits      │ • Auth check  │ • Manual intervention                         │
│ • Sector      │ • Mode verify │ • Kill switch                                 │
│   exposure    │               │                                               │
└───────┬───────┴───────┬───────┴───────────────────────┬───────────────────────┘
        │               │                               │
        ▼               ▼                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         DATA LAYER                                           │
├───────────────────────────────┬─────────────────────────────────────────────┤
│        SQLite Database        │         Cache Layer                         │
├───────────────────────────────┼─────────────────────────────────────────────┤
│ • trades (entry/exit)         │ • Scanner results (5s TTL)                  │
│ • open_positions              │ • Market data                               │
│ • rules                       │ • WebSocket push cache                      │
│ • ai_decision_items           │                                             │
│ • ai_decision_runs            │                                             │
│ • portfolio_snapshots           │                                             │
└───────────────┬───────────────┴───────────────────────┬───────────────────────┘
                │                                       │
                ▼                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      API & COMMUNICATION LAYER                               │
├───────────────┬───────────────┬─────────────────────────────────────────────┤
│  WebSocket    │   REST API    │   React Dashboard                           │
│  Server       │               │                                               │
├───────────────┼───────────────┼─────────────────────────────────────────────┤
│ • Real-time   │ • Rule CRUD   │ • Live positions                            │
│   events      │ • Trade hist  │ • Trade history                             │
│ • Fill notif  │ • AI insights │ • Rule management                           │
│ • Position    │ • Health      │ • Performance charts                          │
│   updates     │   checks      │ • Manual controls                           │
└───────────────┴───────────────┴─────────────────────────────────────────────┘
```

## Component Details

### 1. Core Trading Engine

#### Bot Runner (`bot_runner.py`)
**Responsibilities:**
- Main event loop orchestrating the trading cycle
- Market scanning and symbol discovery
- Rule evaluation and AI decision merging
- Position exit monitoring
- WebSocket broadcasting

**Key Methods:**
- `run_bot_cycle()`: Main entry point for each trading cycle
- `_process_entries()`: Evaluates rules and places entry orders
- `_process_exits()`: Monitors open positions for exit signals
- `_merge_rule_and_ai_candidates()`: Combines rule-based and AI signals

**Configuration:**
- `BOT_INTERVAL_SECONDS`: Cycle frequency (default: 15 min)
- `MAX_TRADES_PER_CYCLE`: Throttle entries per cycle

---

#### Order Executor (`order_executor.py`)
**Responsibilities:**
- IBKR API integration for order placement
- Order lifecycle management (submit → fill/cancel)
- Extended hours handling (MKT → LIMIT conversion)
- Fill event processing and callbacks

**Key Methods:**
- `place_order()`: Main order submission with safety checks
- `cancel_order()`: Order cancellation
- `_handle_fill()`: Fill event processing
- `resubmit_market_as_limit()`: Extended hours support

**Safety Features:**
- Pre-submission safety gate validation
- Position existence checks (prevents duplicate entries)
- Order ID tracking and reconciliation

---

#### Position Tracker (`position_tracker.py`)
**Responsibilities:**
- Open position management
- ATR-based stop loss calculation
- Trailing stop logic
- Moving average exit signals
- High/low watermark tracking

**Key Methods:**
- `register_position()`: Create tracked position from fill
- `check_exits()`: Evaluate exit conditions
- `update_watermarks()`: Update high/low prices
- `calculate_stop_prices()`: Compute ATR stops

**Exit Types:**
1. **Hard Stop**: `entry_price - ATR_STOP_MULT × ATR(14)` (never moves)
2. **Trailing Stop**: `high_watermark - ATR_TRAIL_MULT × ATR(14)_current`
3. **MA Exit**: Price crosses below/above moving average

---

#### Market Data (`market_data.py`)
**Responsibilities:**
- Historical price data retrieval
- Real-time quote fetching
- Multi-source fallback (IBKR → Yahoo)
- Data caching

**Key Methods:**
- `get_historical_bars()`: OHLCV data for indicators
- `get_quote()`: Current price quotes
- `warm_cache()`: Pre-fetch common symbols

---

#### IBKR Scanner (`ibkr_scanner.py`)
**Responsibilities:**
- Server-side market scanning via IBKR API
- Pre-built scan templates (hot stocks, gainers, etc.)
- Result caching for performance

**Scan Templates:**
- `hot_us_stocks`: High volume movers
- `top_gainers/losers`: Percentage change leaders
- `gap_up/down`: Opening gap scanners
- `high_opt_volume`: Options activity
- `new_highs`: 52-week highs

### 2. AI / Machine Learning Layer

#### AI Advisor (`ai_advisor.py`)
**Responsibilities:**
- Strategy optimization based on performance
- Market regime detection
- Shadow → Live promotion decisions
- Parameter tuning recommendations

**Key Methods:**
- `run_optimization_cycle()`: Periodic strategy review
- `detect_market_regime()`: Bull/bear/neutral classification
- `evaluate_promotion_readiness()`: Shadow to live gating

**Models Used:**
- `AI_MODEL_OPTIMIZER`: Strategy optimization (Sonnet 4)
- `AI_MODEL_NARRATIVE`: Market narrative (Sonnet 4)
- `AI_MODEL_REGIME`: Regime detection (Sonnet 4)
- `AI_MODEL_FALLBACK`: Lightweight fallback (Haiku 4.5)

---

#### AI Decision Ledger (`ai_decision_ledger.py`)
**Responsibilities:**
- Track all AI decisions with outcomes
- P&L attribution and scoring
- Hit rate calculation
- Decision replay support

**Key Methods:**
- `log_decision()`: Record new AI decision
- `attach_realized_trade()`: Link trade to decision
- `get_decision_performance()`: Calculate hit rates
- `get_promotion_readiness_metrics()`: Shadow promotion data

---

#### AI Parameters (`ai_parameters.py`)
**Responsibilities:**
- Dynamic exit parameter optimization
- ATR multiplier tuning
- Regime-specific parameter sets
- Parameter persistence

**Key Methods:**
- `get_params_for_symbol()`: Get optimized params for symbol
- `update_params_from_outcome()`: Learn from trade outcomes
- `get_regime_params()`: Regime-specific settings

---

#### Portfolio Allocator (`portfolio_allocator.py`)
**Responsibilities:**
- Risk distribution across candidates
- Position sizing based on account equity
- Sector concentration limits
- Kelly criterion sizing

**Key Methods:**
- `allocate()`: Distribute risk across candidates
- `calculate_position_size()`: Compute shares for trade
- `check_sector_limits()`: Enforce sector caps

---

#### Candidate Selector (`candidate_selector.py`)
**Responsibilities:**
- Merge rule-based and AI-generated signals
- Priority queue management
- Deduplication
- Confidence scoring

**Key Methods:**
- `merge_candidates()`: Combine rule + AI signals
- `prioritize()`: Sort by confidence/priority
- `deduplicate()`: Remove duplicate symbols

### 3. Safety & Risk Layer

#### Safety Gate (`safety_gate.py`)
**Responsibilities:**
- Runtime safety orchestration
- Normalize safety check results
- Error handling

**Key Methods:**
- `evaluate_runtime_safety()`: Main safety check entry point

---

#### Safety Kernel (`safety_kernel.py`)
**Responsibilities:**
- Comprehensive pre-trade validation
- Position limit enforcement
- Sector exposure checks
- Autopilot authority verification
- Drawdown monitoring

**Checks Performed:**
1. **Autopilot Authority**: Mode matches (OFF/PAPER/LIVE)
2. **Position Limits**: `MAX_POSITIONS_TOTAL`, `MAX_POSITIONS_PER_SECTOR`
3. **Daily Risk**: `MAX_DAILY_RISK` not exceeded
4. **Drawdown**: `MAX_TOTAL_DRAWDOWN` not breached
5. **Sector Concentration**: `ENABLE_PORTFOLIO_CONCENTRATION_ENFORCEMENT`
6. **Duplicate Prevention**: No existing position for symbol

**Configuration:**
- `AUTOPILOT_MODE`: OFF | PAPER | LIVE
- `IS_PAPER`: true/false (IBKR paper account)
- `SIM_MODE`: true/false (virtual trading)

---

#### Circuit Breaker (`circuit_breaker.py`)
**Responsibilities:**
- Emergency stop functionality
- Drawdown-based halting
- Manual intervention triggers
- System health monitoring

**Triggers:**
- Total drawdown exceeds `MAX_TOTAL_DRAWDOWN` (18%)
- Daily risk exceeds `MAX_DAILY_RISK` (3%)
- Consecutive AI failures exceed threshold
- Manual kill switch

### 4. Order Lifecycle Management

#### Order Lifecycle (`services/order_lifecycle.py`)
**Responsibilities:**
- Trade record persistence
- Entry position registration
- Exit trade finalization
- Atomic database operations

**Key Methods:**
- `persist_filled_trade_record()`: Save fill to DB
- `register_entry_position_from_fill()`: Create tracked position
- `stamp_exit_trade_context()`: Link exit to entry
- `finalize_filled_exit_trade()`: Complete exit, calculate P&L

**P&L Calculation:**
```python
# Long position
realized_pnl = (exit_price - entry_price) * quantity - fees
pnl_pct = ((exit_price / entry_price) - 1) * 100

# Short position
realized_pnl = (entry_price - exit_price) * quantity - fees
pnl_pct = ((entry_price / exit_price) - 1) * 100
```

---

#### Order Recovery (`services/order_recovery.py`)
**Responsibilities:**
- Pending order reconciliation
- Exit retry logic
- Status normalization
- Timeout handling

**Key Methods:**
- `reconcile_trade_status_update()`: Apply broker status updates
- `evaluate_pending_exit_resolution()`: Decide action for pending exit
- `mark_exit_retry_state()`: Track retry attempts
- `normalize_trade_status()`: Standardize status strings

**Exit Retry Logic:**
- `MAX_EXIT_ATTEMPTS`: 3 attempts
- `EXIT_PENDING_TIMEOUT`: 90 seconds
- Force-close via MKT order after cap reached

---

#### Bot Exits (`bot_exits.py`)
**Responsibilities:**
- Hardened exit processing
- Exit order placement
- Retry cap enforcement
- Manual intervention escalation

**Key Methods:**
- `_process_exits()`: Main exit evaluation loop
- `_reconcile_pending_exit()`: Handle pending exit orders
- `_place_exit_order()`: Submit exit orders
- `_check_retry_cap()`: Escalate after max retries

### 5. Data Layer

#### Database Schema (SQLite)

**Core Tables:**
```sql
-- Trade records (entry and exit)
trades: id, rule_id, symbol, action, quantity, status, 
        fill_price, entry_price, exit_price, realized_pnl,
        closed_at, close_reason, decision_id, position_id

-- Open positions (active tracking)
open_positions: id, symbol, side, quantity, entry_price,
                atr_at_entry, hard_stop_price, high_watermark,
                exit_pending_order_id, exit_attempts

-- Trading rules
rules: id, name, symbol/universe, conditions, action,
       enabled, status, ai_generated, version

-- AI decisions
ai_decision_items: id, run_id, symbol, decision_type,
                   confidence, thesis, projected_return

-- Decision runs
ai_decision_runs: id, mode, started_at, completed_at,
                  regime_detected, bull_bear_ratio

-- Portfolio snapshots
portfolio_snapshots: timestamp, cash, positions_value,
                     unrealized_pnl, realized_pnl
```

#### Cache Layer
- **Scanner Cache**: 5-second TTL for scan results
- **Market Data Cache**: Price data caching
- **WebSocket Cache**: Push optimization

### 6. API & Communication

#### WebSocket Server (`websocket_server.py`)
**Events Broadcast:**
- `fill`: Order fill notifications
- `exit`: Position exit events
- `error`: Critical errors
- `position_update`: Position changes
- `trade`: New trade execution

#### REST API (`main.py`)
**Key Endpoints:**
- `/api/rules/*`: Rule CRUD operations
- `/api/trades/*`: Trade history and management
- `/api/positions/*`: Open positions
- `/api/bot/*`: Bot control (start/stop/status)
- `/api/ai/*`: AI insights and decisions
- `/api/account/*`: Account summary
- `/api/scanner/*`: Market scanning

#### React Dashboard
**Features:**
- Real-time position monitoring
- Trade history with P&L
- Rule management interface
- AI decision visualization
- Performance charts
- Manual trading controls

## Trading Flow

### Entry Flow
```
1. Bot Runner initiates cycle
   └── run_scan() → Get symbols from IBKR Scanner
   
2. Fetch market data
   └── get_historical_bars() → OHLCV for indicators
   
3. Evaluate rules
   └── Rule conditions checked against price data
   └── AI Advisor generates candidates
   └── Candidate Selector merges signals
   
4. Risk allocation
   └── Portfolio Allocator sizes positions
   └── Sector limits enforced
   
5. Safety validation
   └── Safety Gate → Safety Kernel checks
   └── Position limits, drawdown, authority
   
6. Order placement
   └── place_order() submits to IBKR
   └── Order ID tracked in DB
   
7. Fill processing
   └── IBKR fill event → _handle_fill()
   └── persist_filled_trade_record()
   └── register_entry_position_from_fill()
   └── Position now tracked for exits
```

### Exit Flow
```
1. Bot Runner checks exits each cycle
   └── _process_exits() called
   
2. Update watermarks
   └── update_watermarks() → high/low tracking
   
3. Reconcile pending exits
   └── Check if pending orders filled
   └── Timeout handling (90s)
   
4. Evaluate exit conditions
   └── check_exits() → ATR stop, trailing stop, MA exit
   
5. Place exit order
   └── _place_exit_order() → SELL/BUY order
   └── Exit tracked on position
   
6. Fill processing
   └── finalize_filled_exit_trade()
   └── P&L calculated
   └── Position deleted from open_positions
   └── Decision ledger updated
```

## Configuration Hierarchy

### Environment Variables (`.env`)
```bash
# Trading Mode
AUTOPILOT_MODE=OFF|PAPER|LIVE    # AI authority level
IS_PAPER=true|false              # IBKR paper account
SIM_MODE=true|false              # Virtual trading (no IBKR)

# Risk Limits
MAX_TOTAL_DRAWDOWN=0.18        # 18% max drawdown
MAX_DAILY_RISK=0.03              # 3% daily risk limit
MAX_POSITIONS_TOTAL=100
MAX_POSITIONS_PER_SECTOR=3
RISK_PER_TRADE_PCT=1.0

# Exit Parameters
ATR_STOP_MULT=3.0                # Hard stop multiplier
ATR_TRAIL_MULT=2.0               # Trailing stop multiplier

# AI Configuration
AI_MODEL_OPTIMIZER=claude-sonnet-4-20250514
AI_OPTIMIZE_INTERVAL_SECONDS=3600
SHADOW_TO_LIVE_HIT_RATE=0.55     # Promotion threshold

# Connection
IBKR_HOST=127.0.0.1
IBKR_PORT=7497                   # 7497=TWS paper, 7496=TWS live
```

## Safety Features Summary

| Feature | Implementation | Config |
|---------|---------------|--------|
| Mode Gating | `AUTOPILOT_MODE` vs `IS_PAPER`/`SIM_MODE` | `.env` |
| Position Limits | Pre-trade check in Safety Kernel | `MAX_POSITIONS_*` |
| Sector Limits | Concentration enforcement | `MAX_POSITIONS_PER_SECTOR` |
| Drawdown Stop | Circuit breaker on `MAX_TOTAL_DRAWDOWN` | 18% default |
| Daily Risk Cap | Blocks new trades if exceeded | `MAX_DAILY_RISK` |
| Duplicate Prevention | Position existence check | - |
| Exit Retry Cap | Max 3 attempts, then force-close | `MAX_EXIT_ATTEMPTS` |
| Extended Hours | MKT→LIMIT conversion | Automatic |
| Order Timeout | 90s pending exit timeout | `EXIT_PENDING_TIMEOUT` |

## File Organization

```
backend/
├── main.py                    # FastAPI app, REST endpoints
├── bot_runner.py              # Main trading loop
├── order_executor.py          # IBKR order management
├── position_tracker.py        # Exit logic, stops
├── market_data.py             # Price feeds
├── ibkr_scanner.py            # Market scanning
├── ibkr_client.py             # IBKR connection
├── models.py                  # Pydantic models
├── config.py                  # Configuration
├── database.py                # DB operations
├── safety_kernel.py           # Safety checks
├── safety_gate.py             # Safety orchestration
├── circuit_breaker.py         # Emergency stops
├── ai_advisor.py              # AI strategy
├── ai_decision_ledger.py      # Outcome tracking
├── ai_parameters.py           # Dynamic params
├── portfolio_allocator.py     # Risk distribution
├── candidate_selector.py      # Signal merging
├── websocket_server.py        # Real-time events
├── services/
│   ├── order_lifecycle.py     # Fill processing
│   ├── order_recovery.py      # Reconciliation
│   ├── safety_gate.py           # Runtime safety
│   └── trade_outcomes.py      # P&L extraction
├── db/
│   ├── positions.py           # Position DB ops
│   └── core.py                # Transaction mgmt
└── ARCHITECTURE.md            # This file
```

## Key Design Patterns

1. **Safety-First**: All trades pass through Safety Kernel before execution
2. **Atomic Operations**: Database transactions for trade + position updates
3. **Event-Driven**: WebSocket broadcasts for real-time updates
4. **Defensive Programming**: Retry caps, timeouts, fallback data sources
5. **Separation of Concerns**: Clear boundaries between execution, tracking, and AI
6. **Configuration-Driven**: Behavior controlled via environment variables
7. **Observability**: Comprehensive logging and decision tracking

## Performance Characteristics

- **Scan Speed**: <1 second for 6,000+ stocks (IBKR server-side)
- **Cycle Time**: Configurable (default 15 minutes)
- **WebSocket Latency**: <500ms for fill notifications
- **Database**: SQLite with connection pooling
- **Caching**: 5-second TTL for scanner, price data cached
