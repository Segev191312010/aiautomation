# Trading Platform Documentation

## Overview

A comprehensive algorithmic trading platform built with Python (FastAPI backend) and React (frontend). The platform supports live trading via Interactive Brokers (IBKR), paper trading simulation, backtesting, and AI-powered trading strategies.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND (React)                                │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐            │
│  │  Dashboard  │ │   Rules     │ │  Positions  │ │  Backtest   │            │
│  │    (SPA)    │ │   Editor    │ │   Monitor   │ │   Engine    │            │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘            │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ HTTP / WebSocket
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           BACKEND (FastAPI)                                │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         API Layer                                   │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │   │
│  │  │  Rules   │ │  Trades  │ │  Alerts  │ │  Health  │ │  Autopilot│  │   │
│  │  │  Router  │ │  Router  │ │  Router  │ │  Router  │ │  Router   │  │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      Service Layer                                  │   │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐       │   │
│  │  │ Bot Runner │ │ Order Exec │ │ Risk Mgr   │ │ Sim Engine │       │   │
│  │  └────────────┘ └────────────┘ └────────────┘ └────────────┘       │   │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐       │   │
│  │  │  IBKR      │ │ Market Data│ │ Indicators │ │  AI/ML     │       │   │
│  │  │  Client    │ │  Service   │ │  Engine    │ │  Engine    │       │   │
│  │  └────────────┘ └────────────┘ └────────────┘ └────────────┘       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      Data Layer (SQLite/aiosqlite)                  │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │   │
│  │  │   Rules  │ │  Trades  │ │ Positions│ │  Alerts  │ │ Backtests│    │   │
│  │  │   Table  │ │  Table   │ │  Table   │ │  Table   │ │  Table   │    │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ TWS API
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Interactive Brokers (IBKR)                         │
│                    ┌─────────────────────────────┐                         │
│                    │   Trader Workstation (TWS)    │                         │
│                    │      or IB Gateway          │                         │
│                    └─────────────────────────────┘                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Key Components

### 1. Configuration (`config.py`)

Central configuration management using Pydantic settings:

```python
class Settings(BaseSettings):
    # Trading modes
    SIM_MODE: bool = True                    # Virtual/paper trading
    IS_PAPER: bool = True                    # IBKR paper account
    
    # Autopilot modes: OFF, PAPER, LIVE
    AUTOPILOT_MODE: str = "OFF"
    AI_AUTONOMY_ENABLED: bool = False
    AI_SHADOW_MODE: bool = True
    
    # Risk limits
    MAX_POSITIONS: int = 10
    MAX_POSITION_PCT: float = 0.20           # 20% per position
    MAX_SECTOR_PCT: float = 0.30             # 30% per sector
    MAX_TOTAL_DRAWDOWN: float = 0.10         # 10% max drawdown
    
    # Bot settings
    BOT_INTERVAL_SECONDS: int = 60
    BOT_ENABLED: bool = False
    
    # Database
    DB_PATH: str = "trading.db"
    
    # API Keys
    ANTHROPIC_API_KEY: str = ""              # For AI features
    JWT_SECRET: str = ""                     # Auth token signing
```

### 2. Database Layer (`db/`)

**Core (`db/core.py`)**: Connection management with WAL mode, transactions

```python
@asynccontextmanager
async def get_db():
    """Open a DB connection with WAL mode and busy_timeout configured."""
    async with aiosqlite.connect(cfg.DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=FULL")
        await db.execute("PRAGMA busy_timeout=10000")
        await db.execute("PRAGMA foreign_keys=ON")
        yield db

@asynccontextmanager
async def transaction():
    """Atomic multi-step operation — BEGIN IMMEDIATE, auto-COMMIT or ROLLBACK."""
    async with aiosqlite.connect(cfg.DB_PATH) as db:
        # ... setup ...
        await db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
```

**Module Structure:**
- `db/rules.py` - Trading rule CRUD operations
- `db/trades.py` - Trade execution history
- `db/positions.py` - Open position tracking
- `db/alerts.py` - Price alerts and notifications
- `db/backtests.py` - Backtest results storage
- `db/screener.py` - Stock screener presets

### 3. Bot Runner (`bot_runner.py`)

Main trading loop that executes every `BOT_INTERVAL_SECONDS`:

```
Cycle Flow:
1. Clear bar cache
2. Expand universe rules into symbol lists
3. Fetch bars for all required symbols (IBKR → Yahoo Finance fallback)
4. Evaluate all rules (single-symbol and universe)
5. Execute triggered rules via order_executor
6. Update last_triggered / symbol_cooldowns
7. Broadcast status event via WebSocket
```

Key features:
- Event-driven architecture with EventBus
- Correlation matrix caching for risk management
- AI optimization hooks (runs before cycle if due)
- Health monitoring and metrics collection

### 4. Order Executor (`order_executor.py`)

Handles order placement with multiple safeguards:

**Pre-flight Checks:**
```python
MAX_ORDER_QTY = 10_000
MIN_PRICE = 0.01
MAX_PRICE = 1_000_000
MIN_ORDER_VALUE = 100
DEDUP_WINDOW = max(10, cfg.BOT_INTERVAL_SECONDS * 2)

# Validates:
# - Quantity bounds
# - Price bounds
# - Minimum order value
# - Duplicate order prevention (dedup window)
```

**Order Types:**
- Market orders
- Limit orders (with automatic price calculation for extended hours)
- Stop orders

**Safety Gates:**
- Autopilot authority check
- Safety gate validation
- Position existence checks for exits

### 5. Risk Manager (`risk_manager.py`)

Comprehensive risk management with sector tracking:

```python
# Sector concentration limits
_SECTOR_MAP = {
    "AAPL": "Tech", "MSFT": "Tech", "GOOGL": "Tech",
    "JPM": "Finance", "BAC": "Finance",
    "JNJ": "Health", "UNH": "Health",
    # ... etc
}

# Risk checks performed:
# 1. Position count limit
# 2. Position size limit (% of equity)
# 3. Sector concentration limit
# 4. Correlation-based position sizing
# 5. Drawdown monitoring
# 6. Daily loss limits
```

### 6. Simulation Engine (`simulation.py`)

Virtual paper trading when `SIM_MODE=true`:

**Database Tables:**
- `sim_account` - Virtual cash and P&L tracking
- `sim_positions` - Virtual position holdings
- `sim_orders` - Virtual order history

**Features:**
- Average-cost position tracking
- Commission deduction on fills
- Real-time P&L calculation
- Full WebSocket integration for UI updates

### 7. Market Data (`market_data.py`)

Multi-source data fetching with caching:

```python
# Primary: IBKR historical bars
# Fallback: Yahoo Finance (yfinance)

# Supported bar sizes:
_BAR_SIZE_MAP = {
    "1m": "1 min", "5m": "5 mins", "15m": "15 mins",
    "30m": "30 mins", "1h": "1 hour", "4h": "4 hours", "1D": "1 day"
}

# Features:
# - Per-symbol caching (cleared each bot cycle)
# - Real-time tick subscriptions
# - 5-second real-time bars
```

### 8. Indicators (`indicators.py`)

Pure pandas/numpy technical indicators:

```python
Supported Indicators:
- RSI (Relative Strength Index)
- SMA (Simple Moving Average)
- EMA (Exponential Moving Average)
- MACD (Moving Average Convergence Divergence)
- BBANDS (Bollinger Bands)
- ATR (Average True Range)
- STOCH (Stochastic Oscillator)
- PRICE (Raw price)

Cross Detection:
- detect_cross(series_a, series_b) → "above" | "below" | None
```

### 9. IBKR Client (`ibkr_client.py`)

Interactive Brokers integration:

```python
class IBKRClient:
    # Connection management with auto-reconnect
    # Order placement (Market, Limit, Stop)
    # Position tracking
    # Account value monitoring
    # Real-time market data subscriptions
```

### 10. WebSocket Manager (`ws_manager.py`)

Real-time communication with frontend:

```python
# Connection paths:
# /ws - General WebSocket (authenticated)
# /ws/market - Market data stream
# /ws/bot - Bot status updates

# Security:
# - JWT token validation via Sec-WebSocket-Protocol header
# - Origin validation against FRONTEND_ORIGIN
# - User-specific message routing
```

## API Endpoints

### Rules API
```
GET    /api/rules              # List all rules
POST   /api/rules              # Create new rule
GET    /api/rules/{id}         # Get rule details
PUT    /api/rules/{id}         # Update rule
DELETE /api/rules/{id}         # Delete rule
POST   /api/rules/{id}/enable  # Enable rule
POST   /api/rules/{id}/disable # Disable rule
```

### Trading API
```
POST   /api/trades             # Execute manual trade
GET    /api/trades             # Trade history
GET    /api/positions          # Open positions
DELETE /api/positions/{id}    # Close position
```

### Bot Control API
```
POST   /api/bot/start          # Start trading bot
POST   /api/bot/stop           # Stop trading bot
GET    /api/bot/status         # Bot status & health
```

### Autopilot API
```
GET    /api/autopilot/config   # Get autopilot configuration
POST   /api/autopilot/config   # Update configuration
POST   /api/autopilot/mode     # Set mode (OFF/PAPER/LIVE)
POST   /api/autopilot/emergency-stop    # Emergency stop
POST   /api/autopilot/emergency-reset   # Reset emergency
```

### Backtest API
```
POST   /api/backtests          # Run new backtest
GET    /api/backtests          # List backtests
GET    /api/backtests/{id}     # Get backtest results
DELETE /api/backtests/{id}     # Delete backtest
```

## Security Features

### 1. CORS Configuration
```python
_DEV_ALLOWED_ORIGINS = frozenset({
    "http://localhost:5173", "http://localhost:5174",
    "http://127.0.0.1:5173", "http://127.0.0.1:5174",
})

def _allowed_origins() -> list[str]:
    env = os.getenv("FRONTEND_ORIGIN", "")
    extra = {o.strip() for o in env.split(",") if o.strip()}
    return sorted(extra) if extra else sorted(_DEV_ALLOWED_ORIGINS)
```

### 2. JWT Authentication
```python
# Tokens passed via Sec-WebSocket-Protocol header for WebSocket
# HTTP API uses Authorization: Bearer <token> header
# Token validation on every protected endpoint
```

### 3. Rate Limiting
```python
RateLimitMiddleware:
- general_limit: 300 requests
- auth_limit: 10 requests (stricter for auth endpoints)
```

### 4. Autopilot Safety Matrix
```python
# Validates mode transitions:
# - LIVE mode requires JWT_SECRET (not default)
# - PAPER/LIVE require explicit configuration
# - Emergency stop can be triggered from any mode
```

## Event System

### Event Types
```python
class EventType(Enum):
    MARKET = "market"           # Price bar updates
    SIGNAL = "signal"           # Rule trigger signals
    ORDER = "order"             # Order submissions
    FILL = "fill"               # Order fills
    REGIME = "regime"           # Market regime changes
    METRIC = "metric"           # Performance metrics
```

### Event Flow
```
Market Data → EventBus → Bot Runner → Signal Evaluation
                              ↓
                    Order Executor → IBKR/Sim
                              ↓
                         Fill Event → Position Update → WebSocket Broadcast
```

## AI/ML Integration

### AI Optimizer (`ai_optimizer.py`)
- Periodic strategy optimization (configurable interval)
- Parameter tuning based on historical performance
- Risk-adjusted return maximization

### AI Learning (`ai_learning.py`)
- Trade outcome analysis
- Strategy performance tracking
- Continuous model improvement

### Direct AI Trading (`direct_ai_trader.py`)
- AI-generated trade decisions
- Human-in-the-loop approval (configurable)
- Full audit trail for compliance

## Development Setup

### Backend
```bash
cd backend
pip install -r requirements.txt
python main.py
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

### Environment Variables
```bash
# Required
DB_PATH=trading.db
JWT_SECRET=your-secret-key

# For live trading
IBKR_HOST=127.0.0.1
IBKR_PORT=7497  # 7496 for live, 7497 for paper

# For AI features
ANTHROPIC_API_KEY=your-api-key

# Frontend origin (production)
FRONTEND_ORIGIN=https://yourdomain.com
```

## Testing

### Unit Tests
```bash
pytest backend/tests/
```

### Integration Tests
```bash
# Requires IBKR paper account
pytest backend/tests/integration/ --ibkr-paper
```

### Backtest Validation
```bash
python -m backtest_engine --validate
```

## Deployment

### Docker
```dockerfile
# Multi-stage build for production
FROM python:3.11-slim as backend
# ... backend setup ...

FROM node:18-alpine as frontend
# ... frontend build ...

FROM nginx:alpine
# ... serve static files ...
```

### Production Checklist
- [ ] Set `SIM_MODE=false` for live trading
- [ ] Configure `FRONTEND_ORIGIN` for CORS
- [ ] Set strong `JWT_SECRET`
- [ ] Enable HTTPS/WSS
- [ ] Configure log rotation
- [ ] Set up monitoring/alerting
- [ ] Test emergency stop procedures

## Troubleshooting

### Common Issues

**IBKR Connection Failed**
```
- Verify TWS/Gateway is running
- Check host/port configuration
- Enable API connections in TWS settings
- Check firewall rules
```

**Database Locked**
```
- WAL mode is enabled by default
- Check for long-running transactions
- Verify no other processes accessing DB
```

**WebSocket Authentication Failed**
```
- Check JWT token expiration
- Verify Sec-WebSocket-Protocol header format
- Validate origin matches FRONTEND_ORIGIN
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit pull request

## License

MIT License - See LICENSE file for details

## Support

For issues and feature requests, please use the GitHub issue tracker.
