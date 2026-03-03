"""
Pydantic data models for the trading bot API.
"""
from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, model_validator
import uuid as _uuid


# ---------------------------------------------------------------------------
# Rule models
# ---------------------------------------------------------------------------

class Condition(BaseModel):
    indicator: Literal["RSI", "SMA", "EMA", "MACD", "BBANDS", "ATR", "STOCH", "PRICE"]
    params: dict[str, Any] = Field(default_factory=dict)
    # operators: crosses_above, crosses_below, >, <, >=, <=, ==
    operator: str
    # numeric threshold, or "PRICE" to compare against current price
    value: float | str


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
    last_triggered: Optional[str] = None  # ISO datetime string


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


# ---------------------------------------------------------------------------
# Account / Position models  (live IBKR)
# ---------------------------------------------------------------------------

class AccountSummary(BaseModel):
    balance: float           # net liquidation value
    cash: float
    margin_used: float
    unrealized_pnl: float
    realized_pnl: float
    currency: str = "USD"
    is_mock: bool = False


class Position(BaseModel):
    symbol: str
    asset_type: str
    qty: float
    avg_cost: float
    market_price: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float


# ---------------------------------------------------------------------------
# Trade log model
# ---------------------------------------------------------------------------

class Trade(BaseModel):
    id: str = Field(default_factory=lambda: str(_uuid.uuid4()))
    rule_id: str
    rule_name: str
    symbol: str
    action: Literal["BUY", "SELL"]
    asset_type: str
    quantity: int
    order_type: str
    limit_price: Optional[float]
    fill_price: Optional[float]
    status: Literal["PENDING", "FILLED", "CANCELLED", "ERROR"] = "PENDING"
    order_id: Optional[int] = None
    timestamp: str  # ISO datetime


# ---------------------------------------------------------------------------
# Bot / status models
# ---------------------------------------------------------------------------

class BotStatus(BaseModel):
    running: bool
    ibkr_connected: bool
    rules_enabled: int
    last_run: Optional[str] = None
    next_run: Optional[str] = None


class PriceBar(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int


# ---------------------------------------------------------------------------
# WebSocket event models
# ---------------------------------------------------------------------------

class WsEvent(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Normalized market quote  (unified format — IBKR, Yahoo, or Mock)
# ---------------------------------------------------------------------------

class MarketQuote(BaseModel):
    symbol: str
    price: float
    change: float
    change_pct: float
    year_high: Optional[float] = None
    year_low: Optional[float] = None
    market_cap: Optional[float] = None
    avg_volume: Optional[float] = None
    volume: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    last_update: str
    is_mock: bool = False


# ---------------------------------------------------------------------------
# Simulation models
# ---------------------------------------------------------------------------

class SimAccountState(BaseModel):
    """Current state of the virtual paper-trading account."""
    cash: float
    initial_cash: float
    net_liquidation: float
    positions_value: float
    unrealized_pnl: float
    realized_pnl: float
    total_return_pct: float = 0.0
    is_sim: bool = True


class SimPositionState(BaseModel):
    """A virtual position held in the sim account."""
    symbol: str
    qty: float
    avg_cost: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    pnl_pct: float


class SimOrderRecord(BaseModel):
    """A completed virtual order."""
    id: str
    symbol: str
    action: Literal["BUY", "SELL"]
    qty: float
    price: float
    commission: float
    pnl: Optional[float]
    timestamp: str


# ---------------------------------------------------------------------------
# Historical replay playback state
# ---------------------------------------------------------------------------

class PlaybackState(BaseModel):
    active: bool = False
    symbol: str = ""
    speed: int = 1          # 1 | 2 | 5 | 10 | 20
    current_index: int = 0
    total_bars: int = 0
    start_ts: Optional[int] = None
    current_ts: Optional[int] = None
    end_ts: Optional[int] = None
    progress: float = 0.0   # 0.0 – 1.0


# ---------------------------------------------------------------------------
# Auth / User models
# ---------------------------------------------------------------------------

class User(BaseModel):
    id: str
    email: str
    created_at: str
    settings: dict = Field(default_factory=dict)


class AuthToken(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------------------------------------------------------------------------
# Screener models
# ---------------------------------------------------------------------------

ScreenerIndicator = Literal[
    "RSI", "SMA", "EMA", "MACD", "BBANDS", "ATR", "STOCH",
    "PRICE", "VOLUME", "CHANGE_PCT",
]

ScreenerOperator = Literal[
    "GT", "LT", "GTE", "LTE", "CROSSES_ABOVE", "CROSSES_BELOW",
]


class FilterValue(BaseModel):
    type: Literal["number", "indicator"]
    number: float | None = None
    indicator: ScreenerIndicator | None = None  # type: ignore[assignment]
    params: dict[str, Any] = Field(default_factory=dict)
    multiplier: float = 1.0

    @model_validator(mode="after")
    def check_type_match(self):
        if self.type == "number" and self.number is None:
            raise ValueError("number required when type is number")
        if self.type == "indicator" and self.indicator is None:
            raise ValueError("indicator required when type is indicator")
        return self


class ScanFilter(BaseModel):
    indicator: ScreenerIndicator  # type: ignore[assignment]
    params: dict[str, Any] = Field(default_factory=dict)
    operator: ScreenerOperator  # type: ignore[assignment]
    value: FilterValue


class ScanRequest(BaseModel):
    universe: Literal["sp500", "nasdaq100", "etfs", "custom"]
    symbols: list[str] | None = None
    filters: list[ScanFilter] = Field(min_length=1)
    interval: str = "1d"
    period: str = "1y"
    limit: int = Field(default=100, le=500)


class ScanResultRow(BaseModel):
    symbol: str
    price: float
    change_pct: float
    volume: int
    indicators: dict[str, float]


class ScanResponse(BaseModel):
    results: list[ScanResultRow]
    skipped_symbols: list[str]


class EnrichRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=200)


class EnrichResult(BaseModel):
    symbol: str
    name: str
    sector: str | None = None
    market_cap: float | None = None


class ScreenerPreset(BaseModel):
    id: str = Field(default_factory=lambda: str(_uuid.uuid4()))
    name: str
    filters: list[ScanFilter]
    built_in: bool = False
    user_id: str = "demo"
    created_at: str = ""


# ---------------------------------------------------------------------------
# Backtesting models
# ---------------------------------------------------------------------------

class BacktestRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    period: Literal["1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"] = "2y"
    interval: Literal["1m", "5m", "15m", "30m", "1h", "1d", "1wk", "1mo"] = "1d"
    entry_conditions: list[Condition] = Field(min_length=1)
    exit_conditions: list[Condition]
    condition_logic: Literal["AND", "OR"] = "AND"
    initial_capital: float = Field(default=100_000.0, gt=0, le=10_000_000)
    position_size_pct: float = Field(default=100.0, gt=0, le=100)
    stop_loss_pct: float = Field(default=0.0, ge=0, le=50)
    take_profit_pct: float = Field(default=0.0, ge=0, le=100)


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
    created_at: str = ""


class BacktestSaveRequest(BaseModel):
    name: str
    result: BacktestResult
