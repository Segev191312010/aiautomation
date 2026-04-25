# Risk Management System

## Overview

The risk management system provides comprehensive protection against excessive losses through position sizing limits, sector concentration controls, correlation-based adjustments, and drawdown monitoring.

## Risk Limits Configuration

```python
class RiskLimits(BaseModel):
    # Position limits
    max_positions: int = 10                    # Maximum open positions
    max_position_pct: float = 0.20            # Max 20% of equity per position
    max_sector_pct: float = 0.30              # Max 30% in any sector
    
    # Drawdown controls
    max_total_drawdown: float = 0.10          # Halt trading at 10% drawdown
    max_daily_loss: float = 0.05              # Halt at 5% daily loss
    
    # Correlation limits
    max_correlation: float = 0.80               # Position reduction for correlated assets
    correlation_lookback: int = 60          # Days for correlation calculation
    
    # Leverage
    max_leverage: float = 1.0                 # No leverage by default
    
    # Volatility adjustment
    volatility_lookback: int = 20             # Days for volatility calc
    volatility_target: float = 0.15           # 15% annualized volatility target
```

## Sector Classification

```python
_SECTOR_MAP = {
    # Technology
    "AAPL": "Tech", "MSFT": "Tech", "GOOGL": "Tech", "GOOG": "Tech",
    "NVDA": "Tech", "META": "Tech", "AVGO": "Tech", "ORCL": "Tech",
    
    # Financials
    "JPM": "Finance", "BAC": "Finance", "GS": "Finance", "MS": "Finance",
    "WFC": "Finance", "C": "Finance", "BLK": "Finance",
    
    # Healthcare
    "JNJ": "Health", "UNH": "Health", "PFE": "Health", "ABBV": "Health",
    "MRK": "Health", "LLY": "Health", "TMO": "Health",
    
    # Consumer Discretionary
    "AMZN": "ConsDisc", "TSLA": "ConsDisc", "HD": "ConsDisc", "NKE": "ConsDisc",
    
    # Energy
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy",
    
    # Consumer Staples
    "PG": "Staples", "KO": "Staples", "PEP": "Staples", "WMT": "Staples",
    
    # Utilities
    "NEE": "Utilities", "DUK": "Utilities", "SO": "Utilities",
    
    # Real Estate
    "AMT": "RealEstate", "PLD": "RealEstate",
    
    # Industrials
    "CAT": "Industrials", "UNP": "Industrials", "HON": "Industrials",
    
    # Materials
    "LIN": "Materials", "APD": "Materials", "SHW": "Materials",
    
    # ETFs
    "SPY": "ETF", "QQQ": "ETF", "IWM": "ETF",
}
```

## Dynamic Sector Lookup

For symbols not in the static map:

```python
def get_sector(symbol: str) -> str | None:
    """Get sector with TTL-cached yfinance fallback."""
    # 1. Check static map
    if sym in _SECTOR_MAP:
        return _SECTOR_MAP[sym]
    
    # 2. Check cache
    cached = _dynamic_sector_cache.get(sym)
    if cached and not expired:
        return cached[0]
    
    # 3. Fetch from yfinance (24h TTL)
    try:
        info = yf.Ticker(sym).info
        sector = info.get("sector", "Unknown")
        _dynamic_sector_cache[sym] = (sector, time.monotonic())
        return sector
    except:
        return "Unknown"
```

## Pre-Trade Risk Checks

### 1. Position Count Check
```python
def check_position_count(current_positions: int) -> bool:
    """Reject if at max positions."""
    return current_positions < limits.max_positions
```

### 2. Position Size Check
```python
def check_position_size(
    symbol: str,
    quantity: int,
    price: float,
    equity: float,
    current_positions: dict
) -> tuple[bool, str]:
    """
    Check if new position exceeds limits.
    Returns (allowed, reason).
    """
    new_position_value = quantity * price
    new_position_pct = new_position_value / equity
    
    # Check global limit
    if new_position_pct > limits.max_position_pct:
        return False, f"Position size {new_position_pct:.1%} exceeds limit {limits.max_position_pct:.1%}"
    
    # Check existing position
    existing = current_positions.get(symbol, 0)
    if existing > 0:
        total_pct = (existing + new_position_value) / equity
        if total_pct > limits.max_position_pct:
            return False, f"Total position would be {total_pct:.1%}"
    
    return True, "OK"
```

### 3. Sector Concentration Check
```python
def check_sector_concentration(
    symbol: str,
    quantity: int,
    price: float,
    equity: float,
    positions: list[dict]
) -> tuple[bool, str]:
    """Check if adding position would exceed sector limit."""
    sector = get_sector(symbol)
    if not sector:
        return True, "Unknown sector - allowing"
    
    # Calculate current sector exposure
    sector_value = sum(
        p["qty"] * p["market_price"]
        for p in positions
        if get_sector(p["symbol"]) == sector
    )
    
    new_value = quantity * price
    total_sector_pct = (sector_value + new_value) / equity
    
    if total_sector_pct > limits.max_sector_pct:
        return False, f"Sector {sector} would be {total_sector_pct:.1%}"
    
    return True, "OK"
```

### 4. Correlation Check
```python
def check_correlation(
    symbol: str,
    quantity: int,
    price: float,
    positions: list[dict],
    bar_data: dict[str, pd.DataFrame]
) -> tuple[bool, float, str]:
    """
    Check correlation with existing positions.
    Returns (allowed, adjusted_size, reason).
    """
    if len(positions) == 0:
        return True, quantity, "No existing positions"
    
    # Calculate correlations
    symbol_returns = bar_data[symbol]["close"].pct_change().dropna()
    
    max_corr = 0
    for pos in positions:
        pos_symbol = pos["symbol"]
        if pos_symbol not in bar_data:
            continue
        
        pos_returns = bar_data[pos_symbol]["close"].pct_change().dropna()
        
        # Align and calculate correlation
        aligned = pd.concat([symbol_returns, pos_returns], axis=1).dropna()
        if len(aligned) > 20:
            corr = aligned.corr().iloc[0, 1]
            max_corr = max(max_corr, abs(corr))
    
    if max_corr > limits.max_correlation:
        # Reduce position size based on correlation
        reduction = (max_corr - limits.max_correlation) / (1 - limits.max_correlation)
        adjusted_qty = int(quantity * (1 - reduction * 0.5))  # 50% reduction at max
        
        if adjusted_qty < 1:
            return False, 0, f"Correlation {max_corr:.2f} too high"
        
        return True, adjusted_qty, f"Reduced due to correlation {max_corr:.2f}"
    
    return True, quantity, "OK"
```

### 5. Drawdown Check
```python
def check_drawdown(equity: float, peak_equity: float) -> bool:
    """True if drawdown exceeds limit → should halt trading."""
    if peak_equity <= 0:
        return False
    
    drawdown = (peak_equity - equity) / peak_equity
    return drawdown > limits.max_total_drawdown

def check_daily_loss(daily_pnl: float, equity: float) -> bool:
    """True if daily loss exceeds limit."""
    daily_loss_pct = abs(daily_pnl) / equity
    return daily_loss_pct > limits.max_daily_loss
```

## Account State Monitoring

```python
def get_account_state(ib) -> dict:
    """Pull complete account snapshot from IBKR."""
    equity = 0.0
    cash = 0.0
    realized_pnl = 0.0
    unrealized_pnl = 0.0
    
    for av in ib.accountValues():
        if av.currency != "USD":
            continue
        if av.tag == "NetLiquidation":
            equity = float(av.value)
        elif av.tag == "AvailableFunds":
            cash = float(av.value)
        elif av.tag == "RealizedPnL":
            realized_pnl = float(av.value)
        elif av.tag == "UnrealizedPnL":
            unrealized_pnl = float(av.value)
    
    daily_pnl = realized_pnl + unrealized_pnl
    
    positions = []
    for p in ib.portfolio():
        if p.position == 0:
            continue
        positions.append({
            "symbol": p.contract.symbol,
            "qty": p.position,
            "avg_cost": p.averageCost,
            "market_price": p.marketPrice,
            "market_value": p.marketValue,
            "sector": get_sector(p.contract.symbol) or "Unknown",
        })
    
    return {
        "equity": equity,
        "cash": cash,
        "daily_pnl": daily_pnl,
        "positions": positions
    }
```

## Risk Dashboard Metrics

```python
class RiskDashboard(BaseModel):
    # Current state
    total_equity: float
    cash_available: float
    open_positions: int
    
    # Exposure
    gross_exposure: float          # Sum of position values
    net_exposure: float             # Long - Short
    
    # Sector breakdown
    sector_exposure: dict[str, float]  # Sector -> % of equity
    
    # Risk metrics
    current_drawdown: float
    max_drawdown_30d: float
    daily_pnl: float
    daily_pnl_pct: float
    
    # Position details
    largest_position: dict
    largest_sector: dict
    
    # Correlation matrix
    correlation_matrix: dict[str, dict[str, float]]
    
    # Limits status
    limits_status: dict[str, dict]  # limit_name -> {current, limit, status}
```

## Emergency Procedures

### Emergency Stop
```python
async def emergency_stop(reason: str) -> None:
    """
    Immediate halt of all trading activity.
    1. Stop bot runner
    2. Cancel all pending orders
    3. Close all positions (optional)
    4. Lock autopilot to OFF
    5. Send alerts
    """
    await bot_runner.stop()
    await order_executor.cancel_all_orders()
    await autopilot.set_mode("OFF")
    await alerts.send_alert(f"EMERGENCY STOP: {reason}")
```

### Circuit Breakers
```python
CIRCUIT_BREAKERS = {
    "daily_loss": {
        "trigger": lambda state: state.daily_pnl_pct < -0.05,
        "action": "pause_trading",
        "duration": "1h"
    },
    "drawdown": {
        "trigger": lambda state: state.current_drawdown > 0.10,
        "action": "emergency_stop",
        "duration": "manual_reset"
    },
    "volatility_spike": {
        "trigger": lambda state: state.vix > 40,
        "action": "reduce_position_sizes",
        "factor": 0.5
    }
}
```

## Risk Configuration API

### Get Current Limits
```bash
GET /api/risk/limits
```

### Update Limits
```bash
PUT /api/risk/limits
{
  "max_positions": 15,
  "max_position_pct": 0.15,
  "max_sector_pct": 0.25
}
```

### Get Risk Dashboard
```bash
GET /api/risk/dashboard
```

### Get Sector Exposure
```bash
GET /api/risk/sectors
```

### Get Correlation Matrix
```bash
GET /api/risk/correlations?symbols=AAPL,MSFT,GOOGL
```

## Best Practices

### 1. Position Sizing
- Never risk more than 2% of equity on a single trade
- Use volatility-adjusted position sizing
- Consider correlation when adding positions

### 2. Sector Diversification
- No more than 30% in any single sector
- Monitor sector rotation trends
- Adjust sector limits based on market conditions

### 3. Drawdown Management
- Reduce position sizes after 5% drawdown
- Halt new positions at 10% drawdown
- Review strategy after 15% drawdown

### 4. Correlation Monitoring
- Recalculate correlations weekly
- Reduce exposure when correlations spike
- Use uncorrelated assets for diversification

### 5. Regular Review
- Review risk metrics daily
- Adjust limits based on market volatility
- Backtest risk rules quarterly

## Risk Reports

### Daily Risk Report
```python
{
  "date": "2024-01-15",
  "summary": {
    "starting_equity": 100000,
    "ending_equity": 100500,
    "daily_pnl": 500,
    "daily_return": 0.005
  },
  "positions": {
    "count": 5,
    "largest": {"symbol": "AAPL", "pct": 0.18},
    "smallest": {"symbol": "TSLA", "pct": 0.05}
  },
  "sectors": {
    "Tech": 0.35,
    "Finance": 0.20,
    "Health": 0.15
  },
  "risk_metrics": {
    "current_drawdown": 0.02,
    "volatility": 0.12,
    "var_95": 1500
  },
  "alerts": [
    "Sector concentration in Tech approaching limit"
  ]
}
```
