"""
Pydantic data models for the trading bot API.
"""
from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field
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
