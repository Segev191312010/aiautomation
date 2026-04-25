# Trading Rules System

## Overview

The trading rules system is the core of the platform's strategy engine. Rules define when to enter or exit positions based on technical indicators, price action, or custom conditions.

## Rule Structure

```python
class Rule(BaseModel):
    id: str                          # Unique identifier (UUID)
    name: str                        # Human-readable name
    symbol: str                      # Trading symbol (e.g., "AAPL")
    enabled: bool = True             # Active/inactive flag
    created_at: str                  # ISO timestamp
    
    # Trigger conditions
    trigger: TriggerCondition        # When to fire
    
    # Action to take
    action: TradeAction              # What to do
    
    # Optional: Exit conditions
    exit_trigger: Optional[TriggerCondition] = None
    
    # Risk management
    max_position_pct: Optional[float] = None  # Override global limit
    cooldown_minutes: int = 0         # Minimum time between triggers
```

## Trigger Conditions

### Indicator-Based Triggers

```python
class TriggerCondition(BaseModel):
    type: str  # "indicator_cross", "indicator_value", "price", "time"
    
    # For indicator_cross
    indicator_a: str      # e.g., "PRICE", "SMA", "EMA", "RSI"
    params_a: dict        # e.g., {"length": 20}
    indicator_b: str      # e.g., "SMA"
    params_b: dict        # e.g., {"length": 50}
    cross_direction: str  # "above" or "below"
    
    # For indicator_value
    indicator: str
    params: dict
    operator: str         # ">", "<", ">=", "<=", "=="
    value: float
    
    # For price
    price_type: str       # "close", "open", "high", "low"
    operator: str
    value: float
    
    # For time
    time_condition: str   # "market_open", "market_close", "custom"
    time_value: str       # e.g., "09:30" for custom
```

### Examples

**Golden Cross (50-day SMA crosses above 200-day SMA):**
```json
{
  "type": "indicator_cross",
  "indicator_a": "SMA",
  "params_a": {"length": 50},
  "indicator_b": "SMA",
  "params_b": {"length": 200},
  "cross_direction": "above"
}
```

**RSI Oversold (RSI below 30):**
```json
{
  "type": "indicator_value",
  "indicator": "RSI",
  "params": {"length": 14},
  "operator": "<",
  "value": 30
}
```

**Price Breakout (Close above $150):**
```json
{
  "type": "price",
  "price_type": "close",
  "operator": ">",
  "value": 150
}
```

## Trade Actions

```python
class TradeAction(BaseModel):
    type: str           # "BUY", "SELL", "COVER", "SHORT"
    quantity: int       # Number of shares/contracts
    
    # Optional limit price
    limit_price: Optional[float] = None
    
    # Optional stop price
    stop_price: Optional[float] = None
    
    # Time in force
    tif: str = "DAY"    # "DAY", "GTC", "IOC", "FOK"
    
    # Extended hours
    extended_hours: bool = False
```

## Rule Types

### 1. Single Symbol Rules

Rules that monitor and trade a specific symbol:

```python
{
  "id": "rule-123",
  "name": "AAPL Golden Cross",
  "symbol": "AAPL",
  "enabled": True,
  "trigger": {
    "type": "indicator_cross",
    "indicator_a": "SMA",
    "params_a": {"length": 50},
    "indicator_b": "SMA",
    "params_b": {"length": 200},
    "cross_direction": "above"
  },
  "action": {
    "type": "BUY",
    "quantity": 100
  }
}
```

### 2. Universe Rules

Rules that scan multiple symbols and trigger on any match:

```python
{
  "id": "rule-456",
  "name": "Tech Sector RSI Scan",
  "symbol": "UNIVERSE:TECH",  # Special syntax
  "enabled": True,
  "trigger": {
    "type": "indicator_value",
    "indicator": "RSI",
    "params": {"length": 14},
    "operator": "<",
    "value": 30
  },
  "action": {
    "type": "BUY",
    "quantity": 50
  }
}
```

**Universe Types:**
- `UNIVERSE:TECH` - Technology sector
- `UNIVERSE:FINANCE` - Financial sector
- `UNIVERSE:HEALTH` - Healthcare sector
- `UNIVERSE:SP500` - S&P 500 constituents
- `UNIVERSE:NASDAQ100` - NASDAQ 100
- `UNIVERSE:CUSTOM:{id}` - User-defined watchlist

## Rule Evaluation Flow

```
1. Bot cycle starts
2. Expand universe rules to individual symbols
3. Fetch historical bars for all symbols
4. Calculate indicators for each symbol
5. Evaluate trigger conditions:
   
   For each rule:
   a. Check if rule is enabled
   b. Check cooldown period (last_triggered)
   c. Calculate indicator values
   d. Evaluate trigger condition
   e. If triggered, check risk limits
   f. If all checks pass, submit order
   
6. Update last_triggered timestamps
7. Broadcast results
```

## Rule Validation

Rules are validated before saving:

```python
# Validation checks:
1. Symbol format (valid ticker)
2. Indicator name exists
3. Parameters are valid for indicator
4. Action type is valid
5. Quantity is positive integer
6. Limit/stop prices are valid (if provided)
7. Cooldown is non-negative
```

## Rule Versioning

Rules support versioning for audit and rollback:

```python
# Save new version
POST /api/rules/{id}/versions

# List versions
GET /api/rules/{id}/versions

# Restore version
POST /api/rules/{id}/versions/{version_id}/restore
```

## Rule Templates

Built-in rule templates for common strategies:

### Moving Average Crossover
```python
{
  "name": "MA Crossover",
  "description": "Buy when fast MA crosses above slow MA",
  "template": {
    "trigger": {
      "type": "indicator_cross",
      "indicator_a": "SMA",
      "params_a": {"length": "{{fast_period}}"},
      "indicator_b": "SMA",
      "params_b": {"length": "{{slow_period}}"},
      "cross_direction": "above"
    },
    "action": {
      "type": "BUY",
      "quantity": "{{quantity}}"
    }
  },
  "parameters": {
    "fast_period": {"type": "integer", "default": 20, "min": 5, "max": 100},
    "slow_period": {"type": "integer", "default": 50, "min": 10, "max": 200},
    "quantity": {"type": "integer", "default": 100, "min": 1, "max": 10000}
  }
}
```

### RSI Mean Reversion
```python
{
  "name": "RSI Mean Reversion",
  "description": "Buy when RSI is oversold, sell when overbought",
  "template": {
    "trigger": {
      "type": "indicator_value",
      "indicator": "RSI",
      "params": {"length": "{{rsi_period}}"},
      "operator": "<",
      "value": "{{oversold_threshold}}"
    },
    "action": {
      "type": "BUY",
      "quantity": "{{quantity}}"
    },
    "exit_trigger": {
      "type": "indicator_value",
      "indicator": "RSI",
      "params": {"length": "{{rsi_period}}"},
      "operator": ">",
      "value": "{{overbought_threshold}}"
    }
  }
}
```

### Bollinger Bands Squeeze
```python
{
  "name": "Bollinger Squeeze",
  "description": "Buy when price touches lower band, sell at middle band",
  "template": {
    "trigger": {
      "type": "indicator_value",
      "indicator": "BBANDS",
      "params": {"length": "{{bb_length}}", "std": "{{bb_std}}", "band": "lower"},
      "operator": "<=",
      "value": "PRICE:low"
    },
    "action": {
      "type": "BUY",
      "quantity": "{{quantity}}"
    }
  }
}
```

## Rule Performance Metrics

Each rule tracks performance statistics:

```python
class RulePerformance(BaseModel):
    rule_id: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_profit: float
    avg_loss: float
    profit_factor: float
    total_pnl: float
    max_drawdown: float
    sharpe_ratio: float
    
    # Per-symbol breakdown
    symbol_performance: dict[str, SymbolPerformance]
```

## Rule Backtesting

Test rules against historical data:

```python
# Run backtest
POST /api/backtests
{
  "rule_id": "rule-123",
  "start_date": "2023-01-01",
  "end_date": "2023-12-31",
  "initial_capital": 100000,
  "commission": 0.001  # 0.1% per trade
}

# Results include:
# - Equity curve
# - Trade list with timestamps
# - Performance metrics
# - Drawdown analysis
# - Monthly returns
```

## Best Practices

### 1. Risk Management
- Always set position size limits
- Use cooldown periods to avoid over-trading
- Set exit conditions for every entry
- Test rules in simulation mode first

### 2. Indicator Selection
- Use indicators that complement each other
- Avoid over-optimization (curve fitting)
- Consider market regime when selecting indicators
- Test across different time periods

### 3. Rule Maintenance
- Review rule performance regularly
- Disable underperforming rules
- Update rules based on market changes
- Keep a log of rule modifications

### 4. Testing
- Backtest on at least 2 years of data
- Test on out-of-sample data
- Paper trade before going live
- Monitor slippage and execution quality

## API Examples

### Create Rule
```bash
curl -X POST http://localhost:8000/api/rules \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -d '{
    "name": "AAPL RSI Strategy",
    "symbol": "AAPL",
    "trigger": {
      "type": "indicator_value",
      "indicator": "RSI",
      "params": {"length": 14},
      "operator": "<",
      "value": 30
    },
    "action": {
      "type": "BUY",
      "quantity": 100
    },
    "cooldown_minutes": 60
  }'
```

### Enable/Disable Rule
```bash
# Disable
curl -X POST http://localhost:8000/api/rules/rule-123/disable \
  -H "Authorization: Bearer $JWT_TOKEN"

# Enable
curl -X POST http://localhost:8000/api/rules/rule-123/enable \
  -H "Authorization: Bearer $JWT_TOKEN"
```

### Get Rule Performance
```bash
curl http://localhost:8000/api/rules/rule-123/performance \
  -H "Authorization: Bearer $JWT_TOKEN"
```
