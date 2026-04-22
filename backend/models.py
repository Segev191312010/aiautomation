"""
Pydantic data models for the trading bot API.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, model_validator
import uuid as _uuid


# ---------------------------------------------------------------------------
# Rule models
# ---------------------------------------------------------------------------

class Condition(BaseModel):
    indicator: Literal["RSI", "SMA", "EMA", "MACD", "BBANDS", "ATR", "STOCH", "PRICE", "VOLUME", "CHANGE_PCT"]
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


# Valid universe identifiers ("all" expands to sp500 + nasdaq100 + etfs)
_VALID_UNIVERSES = frozenset(["sp500", "nasdaq100", "etfs", "all", "us_all"])


class Rule(BaseModel):
    id: str = Field(default_factory=lambda: str(_uuid.uuid4()))
    name: str
    # Single-symbol rule: set symbol, leave universe=None.
    # Universe rule: set universe ("sp500"/"nasdaq100"/"etfs"/"all"), leave symbol="".
    symbol: str = ""
    universe: Optional[str] = None  # "sp500", "nasdaq100", "etfs", "all", or None
    enabled: bool = False
    conditions: list[Condition]
    logic: Literal["AND", "OR"] = "AND"
    action: TradeAction
    cooldown_minutes: int = 60
    last_triggered: Optional[str] = None  # ISO datetime string
    # Per-symbol cooldown for universe rules: {"AAPL": "<ISO>", ...}
    # Stored as part of the JSON blob; ignored for single-symbol rules.
    symbol_cooldowns: dict[str, str] = Field(default_factory=dict)
    status: Literal["draft", "paper", "active", "paused", "retired"] = "active"
    ai_generated: bool = False
    ai_reason: Optional[str] = None
    thesis: Optional[str] = None
    hold_style: Optional[Literal["intraday", "swing"]] = None
    version: int = 1
    created_by: str = "human"
    supersedes_rule_id: Optional[str] = None
    updated_at: Optional[str] = None
    origin_decision_id: Optional[str] = None  # S10: ai_decision_items.id that created this rule
    origin_run_id: Optional[str] = None       # S10: ai_decision_runs.id that created this rule
    replay_config: Optional[dict] = None      # W1-05: deterministic exit params for rule replay

    @model_validator(mode="after")
    def _check_symbol_or_universe(self) -> "Rule":
        has_symbol = bool(self.symbol and self.symbol.strip())
        has_universe = bool(self.universe and self.universe.strip())
        if not has_symbol and not has_universe:
            raise ValueError("Rule must have either 'symbol' or 'universe' set.")
        if has_symbol and has_universe:
            raise ValueError("Rule cannot have both 'symbol' and 'universe' set.")
        if has_universe and self.universe not in _VALID_UNIVERSES:
            raise ValueError(
                f"Invalid universe '{self.universe}'. Must be one of: {sorted(_VALID_UNIVERSES)}"
            )
        if self.status != "active":
            self.enabled = False
        if not self.updated_at:
            self.updated_at = datetime.now(timezone.utc).isoformat()
        return self


class RuleCreate(BaseModel):
    name: str
    symbol: str = ""
    universe: Optional[str] = None
    enabled: bool = False
    conditions: list[Condition]
    logic: Literal["AND", "OR"] = "AND"
    action: TradeAction
    cooldown_minutes: int = 60
    status: Literal["draft", "paper", "active", "paused", "retired"] = "active"
    ai_generated: bool = False
    ai_reason: Optional[str] = None
    thesis: Optional[str] = None
    hold_style: Optional[Literal["intraday", "swing"]] = None
    version: int = 1
    created_by: str = "human"
    supersedes_rule_id: Optional[str] = None

    @model_validator(mode="after")
    def _check_symbol_or_universe(self) -> "RuleCreate":
        has_symbol = bool(self.symbol and self.symbol.strip())
        has_universe = bool(self.universe and self.universe.strip())
        if not has_symbol and not has_universe:
            raise ValueError("RuleCreate must have either 'symbol' or 'universe' set.")
        if has_symbol and has_universe:
            raise ValueError("RuleCreate cannot have both 'symbol' and 'universe' set.")
        if has_universe and self.universe not in _VALID_UNIVERSES:
            raise ValueError(
                f"Invalid universe '{self.universe}'. Must be one of: {sorted(_VALID_UNIVERSES)}"
            )
        if self.status != "active":
            self.enabled = False
        return self


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    symbol: Optional[str] = None
    universe: Optional[str] = None
    enabled: Optional[bool] = None
    conditions: Optional[list[Condition]] = None
    logic: Optional[Literal["AND", "OR"]] = None
    action: Optional[TradeAction] = None
    cooldown_minutes: Optional[int] = None
    status: Optional[Literal["draft", "paper", "active", "paused", "retired"]] = None
    ai_generated: Optional[bool] = None
    ai_reason: Optional[str] = None
    thesis: Optional[str] = None
    hold_style: Optional[Literal["intraday", "swing"]] = None
    version: Optional[int] = None
    created_by: Optional[str] = None
    supersedes_rule_id: Optional[str] = None
    updated_at: Optional[str] = None


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
# Open position tracker model
# ---------------------------------------------------------------------------

class OpenPosition(BaseModel):
    """Tracks an open position for exit management (ATR stops + MA/indicator exits)."""
    id: str                          # equals entry Trade.id — 1-to-1 link
    symbol: str                      # stored UPPERCASE
    side: Literal["BUY", "SELL"]
    quantity: float                  # float to match sim engine
    entry_price: float
    entry_time: str                  # ISO datetime
    atr_at_entry: float              # ATR(14) at entry — used to compute hard_stop_price
    hard_stop_price: float           # entry_price - ATR_STOP_MULT × atr_at_entry (never moves)
    atr_stop_mult: float             # snapshot of cfg.ATR_STOP_MULT at entry
    atr_trail_mult: float            # snapshot of cfg.ATR_TRAIL_MULT at entry
    high_watermark: float            # highest close since entry (BUY) / lowest (SELL)
    rule_id: str
    rule_name: str
    user_id: str = "demo"
    # Exit coordination (JSON-backed, no SQL migration needed)
    exit_pending_order_id: int | None = None
    exit_attempts: int = 0
    last_exit_attempt_at: str | None = None
    last_exit_error: str | None = None


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
    source: Literal["rule", "ai_direct", "manual"] = "rule"
    ai_reason: Optional[str] = None
    ai_confidence: Optional[float] = None
    stop_price: Optional[float] = None
    invalidation: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    # ── S9 canonical outcome fields ──────────────────────────────
    mode: Optional[Literal["LIVE", "PAPER", "SIM"]] = None
    decision_id: Optional[str] = None          # originating/opening decision; stable across entry+exit
    position_id: Optional[str] = None          # entry trade id; links entry <-> exit
    opened_at: Optional[str] = None            # ISO when position opened
    closed_at: Optional[str] = None            # ISO when position closed
    entry_price: Optional[float] = None        # scoring entry price
    exit_price: Optional[float] = None         # scoring exit price
    fees: float = 0.0
    realized_pnl: Optional[float] = None       # side-aware: long=(exit-entry)*qty, short=(entry-exit)*qty, minus fees
    pnl_pct: Optional[float] = None            # long=((exit/entry)-1)*100, short=((entry/exit)-1)*100
    close_reason: Optional[str] = None         # hard_stop, trailing_stop, ma_exit, pending_fill, manual...
    outcome_quality: Optional[Literal["canonical", "legacy_enriched", "legacy_unverified"]] = None


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
# Normalized market quote  (unified format — IBKR or Yahoo)
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
    screener_score: float = 0.0
    setup: str = "mixed"
    relative_volume: float = 0.0
    momentum_20d: float = 0.0
    trend_strength: float = 0.0
    notes: list[str] = Field(default_factory=list)


class ScanResponse(BaseModel):
    results: list[ScanResultRow]
    skipped_symbols: list[str]
    elapsed_ms: int = 0
    total_symbols: int = 0


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
    exit_mode: Literal["simple", "atr_trail"] = "simple"
    atr_stop_mult: float = Field(default=0.0, ge=0, le=10)
    atr_trail_mult: float = Field(default=0.0, ge=0, le=10)
    start_date: str | None = None
    end_date: str | None = None


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
    exit_mode: str = "simple"
    atr_stop_mult: float = 0.0
    atr_trail_mult: float = 0.0
    created_at: str = ""


class BacktestSaveRequest(BaseModel):
    name: str
    result: BacktestResult


# ---------------------------------------------------------------------------
# Alert models
# ---------------------------------------------------------------------------

class Alert(BaseModel):
    id: str = Field(default_factory=lambda: str(_uuid.uuid4()))
    user_id: str = "demo"
    name: str
    symbol: str
    condition: Condition
    alert_type: Literal["one_shot", "recurring"] = "one_shot"
    cooldown_minutes: int = Field(default=60, ge=0)
    enabled: bool = True
    last_triggered: Optional[str] = None  # UTC ISO 8601
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class AlertCreate(BaseModel):
    name: str
    symbol: str
    condition: Condition
    alert_type: Literal["one_shot", "recurring"] = "one_shot"
    cooldown_minutes: int = Field(default=60, ge=0)
    enabled: bool = True


class AlertUpdate(BaseModel):
    name: Optional[str] = None
    symbol: Optional[str] = None
    condition: Optional[Condition] = None
    alert_type: Optional[Literal["one_shot", "recurring"]] = None
    cooldown_minutes: Optional[int] = Field(default=None, ge=0)
    enabled: Optional[bool] = None


class AlertHistory(BaseModel):
    id: str = Field(default_factory=lambda: str(_uuid.uuid4()))
    alert_id: str
    alert_name: str
    symbol: str
    condition_summary: str  # e.g. "RSI(14) < 30"
    price_at_trigger: float
    fired_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Stock Profile models
# ---------------------------------------------------------------------------

class StockOverview(BaseModel):
    symbol: str
    name: str
    exchange: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    description: Optional[str] = None
    employees: Optional[int] = None
    website: Optional[str] = None
    price: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None
    fetched_at: float


class StockKeyStats(BaseModel):
    market_cap: Optional[float] = None
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    trailing_pe: Optional[float] = None
    forward_pe: Optional[float] = None
    trailing_eps: Optional[float] = None
    forward_eps: Optional[float] = None
    volume: Optional[int] = None
    avg_volume: Optional[int] = None
    dividend_yield: Optional[float] = None
    beta: Optional[float] = None
    fifty_day_ma: Optional[float] = None
    two_hundred_day_ma: Optional[float] = None
    fetched_at: float


class StockFinancials(BaseModel):
    total_revenue: Optional[float] = None
    revenue_growth: Optional[float] = None
    net_income: Optional[float] = None
    operating_margins: Optional[float] = None
    gross_margins: Optional[float] = None
    profit_margins: Optional[float] = None
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    quarterly_revenue: Optional[list[dict]] = None
    quarterly_net_income: Optional[list[dict]] = None
    fetched_at: float


class StockAnalyst(BaseModel):
    recommendation_mean: Optional[float] = None
    recommendation_key: Optional[str] = None
    recommendation_period: Optional[str] = None
    strong_buy: Optional[int] = None
    buy: Optional[int] = None
    hold: Optional[int] = None
    sell: Optional[int] = None
    strong_sell: Optional[int] = None
    current_price: Optional[float] = None
    target_mean_price: Optional[float] = None
    target_high_price: Optional[float] = None
    target_low_price: Optional[float] = None
    target_median_price: Optional[float] = None
    num_analyst_opinions: Optional[int] = None
    fetched_at: float


class StockAnalystDetailGrade(BaseModel):
    date: str
    firm: str
    to_grade: str
    from_grade: str
    action: str
    price_target_action: Optional[str] = None
    price_target: Optional[float] = None
    prior_price_target: Optional[float] = None


class StockRecommendationSnapshot(BaseModel):
    period: str
    strong_buy: int
    buy: int
    hold: int
    sell: int
    strong_sell: int


class StockHolder(BaseModel):
    name: str
    shares: int
    pct: float
    value: Optional[float] = None
    date_reported: Optional[str] = None


class StockOwnership(BaseModel):
    held_pct_institutions: Optional[float] = None
    held_pct_insiders: Optional[float] = None
    top_holders: Optional[list[StockHolder]] = None
    mutual_fund_holders: Optional[list[StockHolder]] = None
    total_institutional_holders: Optional[int] = None
    fetched_at: float


class StockEvents(BaseModel):
    next_earnings_date: Optional[str] = None
    ex_dividend_date: Optional[str] = None
    fetched_at: float


class StockNarrative(BaseModel):
    strengths: list[str]
    risks: list[str]
    outlook: str
    fetched_at: float


# ---------------------------------------------------------------------------
# Swing Screener Dashboard models
# ---------------------------------------------------------------------------

class BreadthRow(BaseModel):
    label: str
    nasdaq100: float
    sp500: float
    composite: float
    billion_plus: float

class BreadthMetrics(BaseModel):
    rows: list[BreadthRow]
    timestamp: str

class GuruScreenerResult(BaseModel):
    symbol: str
    price: float
    change_pct: float
    volume: int
    rs_rank: float
    vcs: float | None = None
    setup_notes: list[str] = Field(default_factory=list)

class ATRMatrixRow(BaseModel):
    symbol: str
    name: str
    atr_pct: float
    price_vs_21ema_atr: float
    close: float
    atr_14: float

class Club97Entry(BaseModel):
    symbol: str
    price: float
    rs_day_pctile: float
    rs_week_pctile: float
    rs_month_pctile: float
    is_tml: bool = False

class StockbeeMover(BaseModel):
    symbol: str
    price: float
    change_pct: float
    volume: int
    avg_volume: int

class IndustryGroup(BaseModel):
    industry: str
    stock_count: int
    avg_weekly_return: float
    avg_monthly_return: float
    rs_vs_spy: float
    top_stocks: list[str] = Field(default_factory=list)

class StageDistribution(BaseModel):
    stage_1: int
    stage_2: int
    stage_3: int
    stage_4: int
    stage_1_symbols: list[str] = Field(default_factory=list)
    stage_2_symbols: list[str] = Field(default_factory=list)
    stage_3_symbols: list[str] = Field(default_factory=list)
    stage_4_symbols: list[str] = Field(default_factory=list)

class TrendGradeEntry(BaseModel):
    symbol: str
    price: float
    change_pct: float
    grade: str
    rs_composite: float

class TrendGradeDistribution(BaseModel):
    grades: dict[str, int]
    top_graded: list[TrendGradeEntry] = Field(default_factory=list)

class SwingDashboardResponse(BaseModel):
    breadth: BreadthMetrics | None = None
    guru_results: dict[str, list[GuruScreenerResult]] = Field(default_factory=dict)
    atr_matrix: list[ATRMatrixRow] = Field(default_factory=list)
    club97: list[Club97Entry] = Field(default_factory=list)
    stockbee: dict[str, list[StockbeeMover]] = Field(default_factory=dict)
    industries: list[IndustryGroup] = Field(default_factory=list)
    stages: StageDistribution | None = None
    grades: TrendGradeDistribution | None = None
