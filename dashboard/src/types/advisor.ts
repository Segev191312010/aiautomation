// ── Autopilot Types ──────────────────────────────────────────────────────────
// Mirrors backend/api_contracts.py — single source of truth for autopilot API shapes.

// ── Score Bucket ────────────────────────────────────────────────────────────

export interface ScoreBucket {
  range: string
  count: number
  avg_pnl: number
  win_rate: number
}

// ── Rule Performance ────────────────────────────────────────────────────────

export type RuleVerdict = 'disable' | 'boost' | 'watch' | 'reduce' | 'hold'
export type RuleStatus = 'good' | 'ok' | 'bad'

export interface RulePerformance {
  rule_id: string
  rule_name: string
  total_trades: number
  win_rate: number
  profit_factor: number
  total_pnl: number
  avg_pnl: number
  avg_win: number
  avg_loss: number
  avg_hold_hours: number
  verdict: RuleVerdict
  status: RuleStatus
}

// ── Sector Performance ──────────────────────────────────────────────────────

export type SectorVerdict = 'avoid' | 'favor' | 'neutral'

export interface SectorPerformance {
  sector: string
  trade_count: number
  win_rate: number
  total_pnl: number
  verdict: SectorVerdict
}

// ── Time Pattern ────────────────────────────────────────────────────────────

export interface TimePattern {
  hour: number
  trade_count: number
  win_rate: number
  avg_pnl: number
  total_pnl: number
}

// ── Score Analysis ──────────────────────────────────────────────────────────

export interface ScoreAnalysis {
  available: boolean
  buckets: ScoreBucket[]
  optimal_min_score: number
  current_min_score: number
}

// ── Bracket Analysis ────────────────────────────────────────────────────────

export interface BracketAnalysis {
  total_closed: number
  sl_hits: number
  tp_hits: number
  other_exits: number
  sl_hit_pct: number
  tp_hit_pct: number
  brackets_too_tight: boolean
}

// ── Recommendation ──────────────────────────────────────────────────────────

export type RecommendationType = 'disable' | 'boost' | 'adjust' | 'warning'
export type RecommendationPriority = 'high' | 'medium' | 'low'
export type RecommendationCategory = 'rule' | 'sector' | 'score' | 'bracket'

export interface Recommendation {
  type: RecommendationType
  priority: RecommendationPriority
  message: string
  rule_id?: string
  category: RecommendationCategory
}

// ── Auto-tune ───────────────────────────────────────────────────────────────

export interface AutoTuneResult {
  applied: boolean
  changes: string[]
  warnings: string[]
  rules_to_disable: string[]
}

// ── PnL Summary ─────────────────────────────────────────────────────────────

export interface AdvisorPnLSummary {
  total_pnl: number
  win_rate: number
  profit_factor: number
  trade_count: number
  best_trade: number
  worst_trade: number
  avg_win: number
  avg_loss: number
  [key: string]: number  // allow extra fields
}

// ── Performance Metrics ─────────────────────────────────────────────────────

export interface PerformanceMetrics {
  total_return: number
  total_return_pct: number
  sharpe_ratio: number
  sortino_ratio: number
  win_rate: number
  profit_factor: number
  avg_hold_time: string
  total_trades: number
  best_trade: number
  worst_trade: number
  [key: string]: number | string  // allow extra fields
}

// ── Full Advisor Report ─────────────────────────────────────────────────────

export interface AdvisorReport {
  generated_at: string
  lookback_days: number
  pnl_summary: AdvisorPnLSummary
  performance: PerformanceMetrics
  rule_performance: RulePerformance[]
  sector_performance: SectorPerformance[]
  time_patterns: TimePattern[]
  score_analysis: ScoreAnalysis
  bracket_analysis: BracketAnalysis
  recommendations: Recommendation[]
  auto_tune_preview: AutoTuneResult
  report: string
  trade_count: number
  data_warning?: string | null
}

// ── Advisor Analysis (subset) ───────────────────────────────────────────────

export interface AdvisorAnalysis {
  rule_performance: RulePerformance[]
  sector_performance: SectorPerformance[]
  time_patterns: TimePattern[]
  score_analysis: ScoreAnalysis
  bracket_analysis: BracketAnalysis
}

// ── Guardrails ──────────────────────────────────────────────────────────────

export interface GuardrailConfig {
  autopilot_mode: 'OFF' | 'PAPER' | 'LIVE'
  shadow_mode: boolean
  ai_autonomy_enabled: boolean
  max_rules_disabled_per_day: number
  max_rules_enabled_per_day: number
  max_position_size_increase_pct: number
  max_weight_change_pct: number
  max_atr_mult_change: number
  min_score_floor: number
  min_score_ceiling: number
  max_changes_per_day: number
  min_hours_between_changes: number
  emergency_stop: boolean
  daily_loss_locked?: boolean
  daily_loss_limit_pct?: number
  // Shadow → Live gating
  shadow_to_live_min_decisions: number
  shadow_to_live_min_days: number
  shadow_to_live_hit_rate_threshold: number
  shadow_to_live_effect_size_threshold: number
  // Auto-tighten
  auto_tighten_enabled: boolean
  auto_tighten_bad_hit_rate_7d: number
  auto_tighten_min_decisions_7d: number
  auto_tighten_bad_hit_rate_30d: number
  auto_tighten_min_decisions_30d: number
  // Tightened state
  guardrails_currently_tightened: boolean
  tightened_at?: string | null
  tightened_reason?: string | null
}

// ── Audit Log ───────────────────────────────────────────────────────────────

export interface AuditLogEntry {
  id: number
  timestamp: string
  action_type: string
  category: string
  description: string
  old_value?: string | null
  new_value?: string | null
  reason?: string | null
  confidence?: number | null
  input_tokens?: number | null
  output_tokens?: number | null
  status: string
  reverted_at?: string | null
  decision_run_id?: string | null
  decision_item_id?: string | null
}

export interface AuditLogPage {
  entries: AuditLogEntry[]
  total: number
  offset: number
  limit: number
}

// ── AI Status ───────────────────────────────────────────────────────────────

export interface AIStatus {
  mode: 'OFF' | 'PAPER' | 'LIVE'
  autonomy_active: boolean
  shadow_mode: boolean
  emergency_stop: boolean
  daily_loss_locked: boolean
  daily_loss_limit_pct: number
  broker_connected: boolean
  open_positions_count: number
  active_rules_count: number
  direct_ai_open_trades_count: number
  last_action_at?: string | null
  changes_today: number
  next_optimization_at?: string | null
  daily_budget_remaining: number
  last_optimization_at?: string | null
  optimizer_running: boolean
  bot_health?: BotHealth | null
}

export interface BotHealth {
  is_running: boolean
  minutes_since_last_cycle?: number | null
  total_cycles_today: number
  error_count_24h: number
  ibkr_connected: boolean
  stale_warning: boolean
  last_error_message?: string | null
  last_signal_symbol?: string | null
  last_successful_ibkr_heartbeat_at?: string | null
  last_order_submit_at?: string | null
  last_fill_event_at?: string | null
  degraded_mode_count_24h: number
}

// ── AI Decision Payload ─────────────────────────────────────────────────────

export interface UncertainValue {
  value: number
  lower: number
  upper: number
}

export interface AIRuleChange {
  rule_id: string
  action: 'disable' | 'enable' | 'boost' | 'reduce'
  sizing_mult?: number
  reason: string
}

export interface AISignalWeights {
  rsi: UncertainValue
  volume: UncertainValue
  trend: UncertainValue
  volatility: UncertainValue
  momentum: UncertainValue
  support_resistance: UncertainValue
  macd: UncertainValue
  bollinger: UncertainValue
}

export interface AIExitParams {
  atr_stop_mult: UncertainValue
  atr_trail_mult: UncertainValue
}

export interface AIRiskAdjustments {
  position_size_pct?: UncertainValue
  risk_per_trade_pct?: UncertainValue
}

export interface AIDecisionPayload {
  signal_weights?: Record<string, AISignalWeights>
  exit_params?: Record<string, AIExitParams>
  min_score?: UncertainValue
  rule_changes: AIRuleChange[]
  rule_actions?: AIRuleAction[]
  direct_trades?: AIDirectTrade[]
  risk_adjustments?: AIRiskAdjustments
  reasoning: string
  confidence: number
}

export type AutopilotMode = 'OFF' | 'PAPER' | 'LIVE'

export interface AutopilotConfig {
  autopilot_mode: AutopilotMode
  emergency_stop: boolean
  daily_loss_locked: boolean
  daily_loss_limit_pct: number
}

export interface AIRuleAction {
  action: 'create' | 'update' | 'enable' | 'disable' | 'pause' | 'retire' | 'delete'
  rule_id?: string
  rule_payload?: Record<string, unknown>
  reason: string
  confidence: number
}

export interface AIDirectTrade {
  symbol: string
  action: 'BUY' | 'SELL'
  order_type: 'MKT' | 'LMT'
  limit_price?: number | null
  stop_price: number
  invalidation: string
  reason: string
  confidence: number
}

export interface RuleVersionRecord {
  version: number
  rule_id: string
  name: string
  conditions: import('@/types').Condition[]
  logic: 'AND' | 'OR'
  action: import('@/types').TradeAction
  cooldown_minutes: number
  created_at: string
  note?: string
  author?: string
  status?: import('@/types').RuleStatus
}

export interface RuleValidationRecord {
  version: number
  validation_mode: string
  trades_count: number
  hit_rate?: number | null
  net_pnl?: number | null
  expectancy?: number | null
  max_drawdown?: number | null
  overlap_score?: number | null
  passed: boolean
  notes?: string | null
  created_at: string
  // S9: evidence quality
  evaluated_closed_count?: number | null
  excluded_legacy_count?: number | null
  validation_window?: string | null
  symbols_evaluated?: string[] | null
  data_quality?: 'canonical' | 'legacy_fallback' | 'mixed' | null
}

export interface RulePromotionReadiness {
  rule_id: string
  status: import('@/types').RuleStatus
  eligible: boolean
  reasons: string[]
  latest_validation?: RuleValidationRecord | null
  data_quality_note?: string | null
}

export interface SourcePerformance {
  source: 'rule' | 'ai_direct' | 'manual' | 'combined'
  trades_count: number
  hit_rate: number | null
  realized_pnl: number
  unrealized_pnl: number
  total_cost: number
  roi: number | null
}

export interface AutopilotPerformance {
  window_days: number
  total_trades: number
  hit_rate: number | null
  realized_pnl: number
  unrealized_pnl: number
  total_cost: number
  roi: number | null
  by_source: SourcePerformance[]
}

export interface RulePerformanceRow {
  rule_id: string
  rule_name: string
  trades_count: number
  hit_rate: number | null
  net_pnl: number
  source: 'rule' | 'ai_direct' | 'manual'
}

export interface AutopilotIntervention {
  id: number
  opened_at: string
  severity: string
  category: string
  symbol?: string | null
  source: string
  summary: string
  required_action: string
  acknowledged_at?: string | null
  resolved_at?: string | null
  resolved_by?: string | null
}

// ── Shadow Mode ─────────────────────────────────────────────────────────────

export interface ShadowDecision {
  id: number
  timestamp: string
  param_type: string
  symbol?: string | null
  ai_suggested_value: string
  actual_value_used: string
  market_condition?: string | null
  hypothetical_outcome?: string | null
  delta_value?: number | null
  confidence?: number | null
  regime?: string | null
}

export interface GatingCondition {
  name: string
  met: boolean
  actual: number
  required: number
}

export interface ParamTypeMetrics {
  count: number
  hit_rate: number | null
  effect_size_avg: number | null
  avg_confidence: number | null
}

export interface ShadowPerformance {
  total_decisions: number
  decisions_with_data: number
  overall_hit_rate: number | null
  overall_effect_size_avg: number | null
  active_days: number
  regimes_covered: Record<string, { decisions: number; hit_rate: number }>
  by_param_type: Record<string, ParamTypeMetrics>
  gating_conditions: GatingCondition[]
  ready_for_live: boolean
  ready_reasons: string[]
}

// ── S10: Decision Ledger Types ──────────────────────────────────────────────

export interface DecisionRun {
  id: string
  source: string
  mode: 'OFF' | 'PAPER' | 'LIVE'
  provider?: string | null
  model?: string | null
  prompt_version?: string | null
  aggregate_confidence?: number | null
  abstained: boolean
  input_tokens?: number | null
  output_tokens?: number | null
  status: string
  error?: string | null
  created_at: string
  completed_at?: string | null
  item_counts: Record<string, number>
}

export interface DecisionItem {
  id: string
  run_id: string
  item_index: number
  item_type: string
  action_name?: string | null
  target_key?: string | null
  symbol?: string | null
  gate_status: string
  gate_reason?: string | null
  confidence?: number | null
  regime?: string | null
  created_rule_id?: string | null
  created_trade_id?: string | null
  realized_trade_id?: string | null
  realized_pnl?: number | null
  realized_at?: string | null
  score_status: string
  score_source?: string | null
  created_at: string
  updated_at: string
}

export interface EvaluationRun {
  id: string
  candidate_type: string
  candidate_key: string
  baseline_key?: string | null
  evaluation_mode: string
  window_start?: string | null
  window_end?: string | null
  status: string
  summary: Record<string, unknown>
  created_at: string
  completed_at?: string | null
}

export interface EvaluationSlice {
  slice_type: string
  slice_key: string
  count: number
  scored_count: number
  hit_rate?: number | null
  net_pnl?: number | null
  expectancy?: number | null
  max_drawdown?: number | null
  coverage?: number | null
  abstain_rate?: number | null
  avg_confidence?: number | null
  calibration_error?: number | null
}

export interface EvaluationCompare {
  baseline?: EvaluationRun | null
  candidate?: EvaluationRun | null
  baseline_slices: EvaluationSlice[]
  candidate_slices: EvaluationSlice[]
}

export interface ReplayRequest {
  candidate_type: 'prompt_version' | 'model_version' | 'rule_snapshot' | 'decision_run'
  candidate_key: string
  baseline_key?: string | null
  evaluation_mode?: 'stored_context_existing' | 'stored_context_generate' | 'rule_backtest'
  window_days?: number
  limit_runs?: number
  min_confidence?: number | null
  symbols?: string[]
  action_types?: string[]
}

export interface ShadowFilters {
  paramType?: string
  symbol?: string
  regime?: string
  minConfidence?: number
  page: number
  pageSize: number
}

export interface ShadowDecisionsPage {
  entries: ShadowDecision[]
  total: number
  offset: number
  limit: number
}

// ── Learning Metrics ────────────────────────────────────────────────────────

export type DataQuality = 'insufficient' | 'low' | 'moderate' | 'good'

export interface LearningMetrics {
  window_days: number
  total_decisions: number
  scored_decisions: number
  hit_rate: number | null
  net_score: number
  net_pnl_impact: number | null
  data_quality: DataQuality
  by_action_type: Record<string, { count: number; hit_rate: number; net_pnl: number }>
  warning?: string | null
}

// ── Economic Report ─────────────────────────────────────────────────────────

export interface EconomicReport {
  days: number
  ai_pnl_impact: number
  total_cost: number
  cost_per_decision: number
  roi_estimate: number | null
  cost_as_pct_pnl: number | null
  decisions_per_day: number
}

// ── Cost Tracking ───────────────────────────────────────────────────────────

export interface DailyCost {
  date: string
  calls: number
  input_tokens: number
  output_tokens: number
  estimated_cost_usd: number
}

export interface CostReport {
  days: number
  total_cost_usd: number
  total_calls: number
  daily: DailyCost[]
}
