"""
Pydantic v2 response models for the AI Advisor API.

These contracts define the exact shape of every advisor endpoint response,
enforcing consistency between backend, frontend, and the AI optimizer.
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


# ── Score bucket (used in score analysis) ────────────────────────────────────

class ScoreBucket(BaseModel):
    range: str           # e.g. "50-55"
    count: int
    avg_pnl: float
    win_rate: float


# ── Rule Performance ─────────────────────────────────────────────────────────

class RulePerformanceResponse(BaseModel):
    rule_id: str
    rule_name: str
    total_trades: int
    win_rate: float          # 0-100 percent
    profit_factor: float
    total_pnl: float
    avg_pnl: float
    avg_win: float
    avg_loss: float
    avg_hold_hours: float    # average holding time in hours
    verdict: Literal["disable", "boost", "watch", "reduce", "hold"]
    status: Literal["good", "ok", "bad"]


# ── Sector Performance ───────────────────────────────────────────────────────

class SectorPerformanceResponse(BaseModel):
    sector: str
    trade_count: int
    win_rate: float          # 0-100 percent
    total_pnl: float
    verdict: Literal["avoid", "favor", "neutral"]


# ── Time Pattern ─────────────────────────────────────────────────────────────

class TimePatternResponse(BaseModel):
    hour: int
    trade_count: int
    win_rate: float          # 0-100 percent
    avg_pnl: float
    total_pnl: float


# ── Score Analysis ───────────────────────────────────────────────────────────

class ScoreAnalysisResponse(BaseModel):
    available: bool
    buckets: list[ScoreBucket]
    optimal_min_score: int
    current_min_score: int = 50


# ── Bracket Analysis ─────────────────────────────────────────────────────────

class BracketAnalysisResponse(BaseModel):
    total_closed: int
    sl_hits: int
    tp_hits: int
    other_exits: int
    sl_hit_pct: float
    tp_hit_pct: float
    brackets_too_tight: bool


# ── Recommendation ───────────────────────────────────────────────────────────

class RecommendationResponse(BaseModel):
    type: Literal["disable", "boost", "adjust", "warning"]
    priority: Literal["high", "medium", "low"]
    message: str
    rule_id: Optional[str] = None
    category: Literal["rule", "sector", "score", "bracket"]


# ── Auto-tune ────────────────────────────────────────────────────────────────

class AutoTuneResultResponse(BaseModel):
    applied: bool
    changes: list[str]
    warnings: list[str]
    rules_to_disable: list[str]


# ── PnL Summary ──────────────────────────────────────────────────────────────

class PnLSummaryResponse(BaseModel):
    """Matches compute_realized_pnl() output (minus matched_trades)."""
    model_config = {"extra": "allow"}  # tolerate extra keys from compute_realized_pnl
    total_pnl: float = 0
    win_rate: float = 0
    profit_factor: float = 0
    trade_count: int = 0
    best_trade: float = 0
    worst_trade: float = 0
    avg_win: float = 0
    avg_loss: float = 0


# ── Performance Metrics ──────────────────────────────────────────────────────

class PerformanceMetricsResponse(BaseModel):
    """Matches compute_performance_metrics() output from portfolio_analytics.py."""
    model_config = {"extra": "allow"}  # tolerate extra keys
    total_return: float = 0
    total_return_pct: float = 0
    sharpe_ratio: float = 0
    sortino_ratio: float = 0
    win_rate: float = 0
    profit_factor: float = 0
    avg_hold_time: str = "—"
    total_trades: int = 0
    best_trade: float = 0
    worst_trade: float = 0


# ── Full Advisor Report ──────────────────────────────────────────────────────

class AdvisorReportResponse(BaseModel):
    generated_at: str
    lookback_days: int
    pnl_summary: PnLSummaryResponse
    performance: PerformanceMetricsResponse
    rule_performance: list[RulePerformanceResponse]
    sector_performance: list[SectorPerformanceResponse]
    time_patterns: list[TimePatternResponse]
    score_analysis: ScoreAnalysisResponse
    bracket_analysis: BracketAnalysisResponse
    recommendations: list[RecommendationResponse]
    auto_tune_preview: AutoTuneResultResponse
    report: str
    trade_count: int
    data_warning: Optional[str] = None


# ── Guardrails ───────────────────────────────────────────────────────────────

class GuardrailConfigResponse(BaseModel):
    shadow_mode: bool = True
    ai_autonomy_enabled: bool = False
    max_rules_disabled_per_day: int = 2
    max_rules_enabled_per_day: int = 1
    max_position_size_increase_pct: float = 25.0
    max_weight_change_pct: float = 30.0
    max_atr_mult_change: float = 0.5
    min_score_floor: int = 35
    min_score_ceiling: int = 80
    max_changes_per_day: int = 10
    min_hours_between_changes: float = 4.0
    emergency_stop: bool = False
    # Shadow → Live gating
    shadow_to_live_min_decisions: int = 100
    shadow_to_live_min_days: int = 15
    shadow_to_live_hit_rate_threshold: float = 0.55
    shadow_to_live_effect_size_threshold: float = 0.0
    # Auto-tighten behavior
    auto_tighten_enabled: bool = False
    auto_tighten_bad_hit_rate_7d: float = 0.45
    auto_tighten_min_decisions_7d: int = 40
    auto_tighten_bad_hit_rate_30d: float = 0.50
    auto_tighten_min_decisions_30d: int = 100
    # Tightened state tracking
    guardrails_currently_tightened: bool = False
    tightened_at: Optional[str] = None
    tightened_reason: Optional[str] = None


# ── Audit Log ────────────────────────────────────────────────────────────────

class AuditLogEntryResponse(BaseModel):
    id: int
    timestamp: str
    action_type: str
    category: str
    description: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    reason: Optional[str] = None
    confidence: Optional[float] = None
    decision_confidence_avg: Optional[float] = None
    parameter_uncertainty_width: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    status: str = "applied"
    reverted_at: Optional[str] = None


class AuditLogResponse(BaseModel):
    entries: list[AuditLogEntryResponse]
    total: int
    offset: int
    limit: int


# ── AI Status ────────────────────────────────────────────────────────────────

class AIStatusResponse(BaseModel):
    autonomy_active: bool = False
    shadow_mode: bool = True
    emergency_stop: bool = False
    last_action_at: Optional[str] = None
    changes_today: int = 0
    next_optimization_at: Optional[str] = None
    daily_budget_remaining: int = 10
    last_optimization_at: Optional[str] = None
    optimizer_running: bool = False


# ── AI Decision Payload (what Claude returns) ────────────────────────────────

class UncertainValue(BaseModel):
    """A numeric value with confidence interval."""
    value: float
    lower: float
    upper: float


class AISignalWeights(BaseModel):
    """Signal weights per regime with uncertainty."""
    rsi: UncertainValue
    volume: UncertainValue
    trend: UncertainValue
    volatility: UncertainValue
    momentum: UncertainValue
    support_resistance: UncertainValue
    macd: UncertainValue
    bollinger: UncertainValue


class AIExitParams(BaseModel):
    atr_stop_mult: UncertainValue
    atr_trail_mult: UncertainValue


class AIRuleChange(BaseModel):
    rule_id: str
    action: Literal["disable", "enable", "boost", "reduce"]
    sizing_mult: Optional[float] = None
    reason: str


class AIRiskAdjustments(BaseModel):
    position_size_pct: Optional[UncertainValue] = None
    risk_per_trade_pct: Optional[UncertainValue] = None


class AIDecisionPayload(BaseModel):
    """Structured JSON that Claude returns from the optimizer."""
    signal_weights: Optional[dict[str, AISignalWeights]] = None  # keyed by regime
    exit_params: Optional[dict[str, AIExitParams]] = None        # keyed by symbol or "_default"
    min_score: Optional[UncertainValue] = None
    rule_changes: list[AIRuleChange] = Field(default_factory=list)
    risk_adjustments: Optional[AIRiskAdjustments] = None
    reasoning: str = ""
    confidence: float = 0.5


# ── Shadow Decision ──────────────────────────────────────────────────────────

class ShadowDecisionResponse(BaseModel):
    id: int
    timestamp: str
    param_type: str
    symbol: Optional[str] = None
    ai_suggested_value: str
    actual_value_used: str
    market_condition: Optional[str] = None
    hypothetical_outcome: Optional[str] = None
    delta_value: Optional[float] = None
    confidence: Optional[float] = None
    regime: Optional[str] = None


class GatingCondition(BaseModel):
    name: str
    met: bool
    actual: float
    required: float


class ParamTypeMetrics(BaseModel):
    count: int = 0
    hit_rate: Optional[float] = None
    effect_size_avg: Optional[float] = None
    avg_confidence: Optional[float] = None


class ShadowPerformanceResponse(BaseModel):
    total_decisions: int
    decisions_with_data: int = 0
    overall_hit_rate: Optional[float] = None
    overall_effect_size_avg: Optional[float] = None
    active_days: int = 0
    regimes_covered: dict[str, dict] = Field(default_factory=dict)
    by_param_type: dict[str, ParamTypeMetrics] = Field(default_factory=dict)
    gating_conditions: list[GatingCondition] = Field(default_factory=list)
    ready_for_live: bool = False
    ready_reasons: list[str] = Field(default_factory=list)


# ── Cost Tracking ────────────────────────────────────────────────────────────

class DailyCostEntry(BaseModel):
    date: str
    calls: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float


class CostReportResponse(BaseModel):
    days: int
    total_cost_usd: float
    total_calls: int
    daily: list[DailyCostEntry]


# ── Learning Metrics ─────────────────────────────────────────────────────────

class LearningMetricsResponse(BaseModel):
    window_days: int
    total_decisions: int
    scored_decisions: int = 0
    hit_rate: Optional[float] = None
    net_score: int = 0
    net_pnl_impact: Optional[float] = None
    data_quality: str = "insufficient"
    by_action_type: dict[str, dict] = Field(default_factory=dict)
    warning: Optional[str] = None


# ── Economic Report ──────────────────────────────────────────────────────────

class EconomicReportResponse(BaseModel):
    days: int
    ai_pnl_impact: float = 0
    total_cost: float = 0
    cost_per_decision: float = 0
    roi_estimate: Optional[float] = None
    cost_as_pct_pnl: Optional[float] = None
    decisions_per_day: float = 0
