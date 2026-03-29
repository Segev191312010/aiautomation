// ── Market data ─────────────────────────────────────────────────────────────

export interface MarketQuote {
  symbol: string
  price: number
  change: number
  change_pct: number
  year_high?: number
  year_low?: number
  market_cap?: number
  avg_volume?: number
  volume?: number
  bid?: number
  ask?: number
  last_update: string
  live_source?: 'ibkr' | 'yahoo'
  market_state?: 'open' | 'extended' | 'closed' | 'unknown'
  stale_s?: number
}

export interface OHLCVBar {
  time: number   // Unix seconds
  open: number
  high: number
  low: number
  close: number
  volume: number
}

// ── Account ──────────────────────────────────────────────────────────────────

export interface AccountSummary {
  balance: number
  cash: number
  margin_used: number
  unrealized_pnl: number
  realized_pnl: number
  currency: string
}

export interface SimAccountState {
  cash: number
  initial_cash: number
  net_liquidation: number
  positions_value: number
  unrealized_pnl: number
  realized_pnl: number
  total_return_pct: number
  is_sim: true
}

export type AnyAccount = AccountSummary | SimAccountState

// ── Positions ────────────────────────────────────────────────────────────────

export interface Position {
  symbol: string
  asset_type: string
  qty: number
  avg_cost: number
  market_price: number
  market_value: number
  unrealized_pnl: number
  realized_pnl: number
}

export interface SimPosition {
  symbol: string
  qty: number
  avg_cost: number
  current_price: number
  market_value: number
  unrealized_pnl: number
  pnl_pct: number
}

// ── Orders ───────────────────────────────────────────────────────────────────

export type OrderAction = 'BUY' | 'SELL'
export type OrderType   = 'MKT' | 'LMT'
export type AssetType   = 'STK' | 'OPT' | 'FUT'

export interface OpenOrder {
  order_id: number
  symbol: string
  action: OrderAction
  qty: number
  order_type: OrderType
  limit_price?: number
  status: string
}

export interface SimOrderRecord {
  id: string
  symbol: string
  action: OrderAction
  qty: number
  price: number
  commission: number
  pnl?: number
  timestamp: string
}

// ── Rules ────────────────────────────────────────────────────────────────────

export type Indicator = 'RSI' | 'SMA' | 'EMA' | 'MACD' | 'BBANDS' | 'ATR' | 'STOCH' | 'PRICE'

export interface Condition {
  indicator: Indicator
  params: Record<string, number | string>
  operator: string
  value: number | string
}

export interface TradeAction {
  type: OrderAction
  asset_type: AssetType
  quantity: number
  order_type: OrderType
  limit_price?: number
}

export type RuleUniverse = 'sp500' | 'nasdaq100' | 'etfs' | 'all'
export type RuleStatus = 'draft' | 'paper' | 'active' | 'paused' | 'retired'
export type HoldStyle = 'intraday' | 'swing'

export interface Rule {
  id: string
  name: string
  symbol: string
  universe?: RuleUniverse | null
  enabled: boolean
  conditions: Condition[]
  logic: 'AND' | 'OR'
  action: TradeAction
  cooldown_minutes: number
  last_triggered?: string | null
  status?: RuleStatus
  ai_generated?: boolean
  ai_reason?: string | null
  thesis?: string | null
  hold_style?: HoldStyle | null
  version?: number
  created_by?: string
  supersedes_rule_id?: string | null
  updated_at?: string | null
}

export interface RuleCreate {
  name: string
  symbol: string
  universe?: RuleUniverse | null
  enabled?: boolean
  conditions: Condition[]
  logic?: 'AND' | 'OR'
  action: TradeAction
  cooldown_minutes?: number
  status?: RuleStatus
  ai_generated?: boolean
  ai_reason?: string | null
  thesis?: string | null
  hold_style?: HoldStyle | null
  version?: number
  created_by?: string
  supersedes_rule_id?: string | null
}

// ── Alerts ──────────────────────────────────────────────────────────────────

export type AlertType = 'one_shot' | 'recurring'

export interface Alert {
  id: string
  user_id: string
  name: string
  symbol: string
  condition: Condition
  alert_type: AlertType
  cooldown_minutes: number
  enabled: boolean
  last_triggered?: string
  created_at: string
}

export interface AlertCreate {
  name: string
  symbol: string
  condition: Condition
  alert_type?: AlertType
  cooldown_minutes?: number
  enabled?: boolean
}

export interface AlertUpdate {
  name?: string
  symbol?: string
  condition?: Condition
  alert_type?: AlertType
  cooldown_minutes?: number
  enabled?: boolean
}

export interface AlertHistory {
  id: string
  alert_id: string
  alert_name: string
  symbol: string
  condition_summary: string
  price_at_trigger: number
  fired_at: string
}

export interface AlertFiredEvent {
  type: 'alert_fired'
  alert_id: string
  name: string
  symbol: string
  condition_summary: string
  price: number
  timestamp: string
}

export interface AlertTestResult {
  alert_id: string
  symbol: string
  price: number
  triggered: boolean
  condition_summary: string
}

// ── Alert notifications & stats ──────────────────────────────────────────────

export type AlertSoundId = 'ding' | 'chime' | 'alarm' | 'cash_register'

export interface NotificationPrefs {
  /** Play a sound when an alert fires. */
  sound_enabled:    boolean
  /** Which sound to play. */
  sound:            AlertSoundId
  /** Master volume 0–1. */
  volume:           number
  /** Silence all sounds regardless of other settings. */
  muted:            boolean
  /** Show a native browser (OS-level) push notification. */
  browser_push:     boolean
  /** Show an in-app toast when an alert fires. */
  in_app:           boolean
}

export const DEFAULT_NOTIFICATION_PREFS: NotificationPrefs = {
  sound_enabled: true,
  sound:         'chime',
  volume:        0.6,
  muted:         false,
  browser_push:  false,
  in_app:        true,
}

export interface AlertStats {
  total_today:      number
  total_week:       number
  total_month:      number
  top_symbols:      { symbol: string; count: number }[]
  /** Fired counts bucketed by day, most-recent last, length ≤ 30. */
  daily_counts:     { date: string; count: number }[]
}

// ── Trade log ────────────────────────────────────────────────────────────────

export type TradeStatus = 'PENDING' | 'FILLED' | 'CANCELLED' | 'ERROR'

export interface Trade {
  id: string
  rule_id: string
  rule_name: string
  symbol: string
  action: OrderAction
  asset_type: string
  quantity: number
  order_type: string
  limit_price?: number
  fill_price?: number
  status: TradeStatus
  order_id?: number
  timestamp: string
  source?: 'rule' | 'ai_direct' | 'manual'
  ai_reason?: string | null
  ai_confidence?: number | null
  stop_price?: number | null
  invalidation?: string | null
  metadata?: Record<string, unknown>
  // S9 canonical outcome fields
  mode?: 'LIVE' | 'PAPER' | 'SIM' | null
  decision_id?: string | null
  position_id?: string | null
  opened_at?: string | null
  closed_at?: string | null
  entry_price?: number | null
  exit_price?: number | null
  fees?: number
  realized_pnl?: number | null
  pnl_pct?: number | null
  close_reason?: string | null
  outcome_quality?: 'canonical' | 'legacy_enriched' | 'legacy_unverified' | null
}

// ── System status ─────────────────────────────────────────────────────────────

export interface SystemStatus {
  ibkr_connected: boolean
  is_paper: boolean
  sim_mode: boolean
  bot_running: boolean
  last_run?: string
  next_run?: string
  bot_interval_seconds: number
  autopilot_mode?: 'OFF' | 'PAPER' | 'LIVE'
  autopilot_emergency_stop?: boolean
  autopilot_daily_loss_locked?: boolean
  features?: {
    market_diagnostics: boolean
    autopilot_console?: boolean
  }
}

export interface BotStatus {
  running: boolean
  last_run?: string
  next_run?: string
}

// —— Market diagnostics ———————————————————————————————————————————————

export type DiagnosticState = 'GREEN' | 'YELLOW' | 'RED' | 'unknown'
export type DiagnosticFreshness = 'ok' | 'warn' | 'stale' | 'unknown'

export interface DiagnosticWidgetInsight {
  score: number | null
  state: DiagnosticState
}

export interface DiagnosticOverview {
  as_of_ts: number | null
  composite_score: number | null
  state: DiagnosticState
  indicator_count: number
  stale_count: number
  warn_count: number
  trend: Array<{ time: number; value: number }>
  widgets: Record<string, DiagnosticWidgetInsight>
  last_run_ts?: number
}

export interface DiagnosticIndicator {
  code: string
  name: string
  source: string
  frequency: 'real_time' | 'daily' | 'weekly' | 'monthly' | string
  weight: number
  expected_lag_business_days: number
  stale_warn_s: number | null
  stale_critical_s: number | null
  time: number | null
  value: number | null
  score: number | null
  state: DiagnosticState | null
  reason_code: string | null
  freshness_status: DiagnosticFreshness
  age_s: number | null
  meta: Record<string, unknown>
}

export interface DiagnosticIndicatorHistoryPoint {
  time: number
  value: number | null
  score: number | null
  state: DiagnosticState | null
  reason_code: string | null
  freshness: DiagnosticFreshness
  age_s: number | null
}

export interface DiagnosticMarketMap {
  symbol: string
  pct_change: number
  rel_volume: number
  price: number
  as_of_ts: number
}

export interface DiagnosticSectorProjectionValue {
  sector: string
  score: number
  direction: 'BULLISH' | 'NEUTRAL' | 'BEARISH'
}

export interface DiagnosticSectorProjection {
  run_id: number
  run_ts: number
  lookback_days: number
  heuristic_version: string
  status: string
  values: DiagnosticSectorProjectionValue[]
}

export interface DiagnosticNewsArticle {
  source: string
  headline: string
  url: string
  published_at: number
  fetched_at: number
}

export interface DiagnosticRefreshRun {
  run_id: number
  status: string
  locked_by?: string
  locked_at?: number | null
  lock_expires_at?: number | null
  started_at?: number | null
  completed_at?: number | null
  error?: string | null
}

// ── Simulation / replay ───────────────────────────────────────────────────────

export interface PlaybackState {
  active: boolean
  symbol: string
  speed: number
  current_index: number
  total_bars: number
  start_ts?: number
  current_ts?: number
  end_ts?: number
  progress: number
}

// ── WebSocket events ──────────────────────────────────────────────────────────

export type WsEventType =
  | 'pong'
  | 'ibkr_state'
  | 'bot'
  | 'signal'
  | 'filled'
  | 'error'
  | 'bar'
  | 'quote'
  | 'replay_bar'
  | 'replay_done'
  | 'sim_order'
  | 'sim_reset'
  | 'alert_fired'
  | 'positions_update'
  | 'account_update'
  | 'order_filled'
  | 'order_modified'

export interface WsEvent {
  type: WsEventType
  [key: string]: unknown
}

// ── Activity Feed ──────────────────────────────────────────────────────────────

export interface ActivityEvent {
  id: string
  timestamp: string
  type: 'fill' | 'signal' | 'cancelled'
  symbol: string
  action: 'BUY' | 'SELL'
  qty: number
  price?: number
  ruleName: string
  slPrice?: number
  tpPrice?: number
  pctOfAccount?: number
  status: 'FILLED' | 'PENDING' | 'CANCELLED'
}

// ── UI / watchlist ────────────────────────────────────────────────────────────

export interface Watchlist {
  id: string
  name: string
  symbols: string[]
}

export type SortField = 'symbol' | 'price' | 'change_pct' | 'volume' | 'market_cap'
export type SortDir   = 'asc' | 'desc'

export interface WatchlistSort {
  field: SortField
  dir:   SortDir
}

export type AppRoute = 'dashboard' | 'tradebot' | 'market' | 'charts' | 'rotation' | 'screener' | 'simulation' | 'backtest' | 'rules' | 'alerts' | 'settings' | 'stock' | 'analytics' | 'advisor'

// ── Chart types ─────────────────────────────────────────────────────────────

export type ChartType = 'candlestick' | 'ohlc' | 'line' | 'area' | 'baseline' | 'heikin-ashi'

export interface TradeMarker {
  time:    number          // Unix seconds
  action:  'BUY' | 'SELL'
  price:   number
  label?:  string          // e.g., "RSI < 30"
}

// ── Drawing types ───────────────────────────────────────────────────────

export type {
  DrawingType,
  DrawingPoint,
  DrawingOptions,
  Drawing,
  DrawingToolState,
  HitTestResult,
} from './drawing'

// ── AI Advisor types ────────────────────────────────────────────────────

export type {
  AdvisorReport,
  AdvisorAnalysis,
  AutoTuneResult,
  AdvisorPnLSummary,
  PerformanceMetrics,
  GuardrailConfig,
  AuditLogEntry,
  AuditLogPage,
  AIStatus,
  Recommendation,
  RecommendationPriority,
  RecommendationType,
  RecommendationCategory,
  RulePerformance,
  RuleVerdict,
  SectorPerformance,
  TimePattern,
  ScoreAnalysis,
  ScoreBucket,
  BracketAnalysis,
  UncertainValue,
  AIRuleChange,
  AISignalWeights,
  AIExitParams,
  AIRiskAdjustments,
  AIDecisionPayload,
  ShadowDecision,
  ShadowPerformance,
  ShadowFilters,
  ShadowDecisionsPage,
  GatingCondition,
  ParamTypeMetrics,
  LearningMetrics,
  DataQuality,
  EconomicReport,
  DailyCost,
  CostReport,
} from './advisor'

// ── Screener ─────────────────────────────────────────────────────────────

export type ScreenerIndicator = 'RSI' | 'SMA' | 'EMA' | 'MACD' | 'BBANDS' | 'ATR' | 'STOCH' | 'PRICE' | 'VOLUME' | 'CHANGE_PCT'
export type ScreenerOperator = 'GT' | 'LT' | 'GTE' | 'LTE' | 'CROSSES_ABOVE' | 'CROSSES_BELOW'

export interface FilterValue {
  type: 'number' | 'indicator'
  number?: number
  indicator?: ScreenerIndicator
  // Keep in sync with backend: FilterValue.params = dict[str, Any]
  params?: Record<string, number | string>
  multiplier?: number
}

export interface ScanFilter {
  id?: string
  indicator: ScreenerIndicator
  // Keep in sync with backend: ScanFilter.params = dict[str, Any]
  params: Record<string, number | string>
  operator: ScreenerOperator
  value: FilterValue
}

export interface ScanRequest {
  universe: string
  symbols?: string[]
  filters: ScanFilter[]
  interval: string
  period: string
  limit: number
}

export interface ScanResultRow {
  symbol: string
  price: number
  change_pct: number
  volume: number
  indicators: Record<string, number>
  screener_score: number
  setup: string
  relative_volume: number
  momentum_20d: number
  trend_strength: number
  notes: string[]
}

export interface ScanResponse {
  results: ScanResultRow[]
  skipped_symbols: string[]
}

export interface EnrichResult {
  symbol: string
  name: string
  sector?: string
  market_cap?: number
}

export interface ScreenerPreset {
  id: string
  name: string
  filters: ScanFilter[]
  built_in: boolean
  created_at: string
}

export interface UniverseInfo {
  id: string
  name: string
  count: number
}

// ── Backtesting ─────────────────────────────────────────────────────────

export interface BacktestRequest {
  symbol: string
  period: string
  interval: string
  entry_conditions: Condition[]
  exit_conditions: Condition[]
  condition_logic: 'AND' | 'OR'
  initial_capital: number
  position_size_pct: number
  stop_loss_pct: number
  take_profit_pct: number
}

export interface BacktestTrade {
  entry_date: string
  exit_date: string
  entry_price: number
  exit_price: number
  qty: number
  pnl: number
  pnl_pct: number
  duration_bars: number
  duration_days: number
  exit_reason: string
}

export interface BacktestMetrics {
  total_return_pct: number
  cagr: number
  sharpe_ratio: number
  sortino_ratio: number
  calmar_ratio: number
  max_drawdown_pct: number
  win_rate: number
  profit_factor: number
  num_trades: number
  avg_win: number
  avg_loss: number
  longest_win_streak: number
  longest_lose_streak: number
  avg_trade_duration_days: number
}

export interface BacktestResult {
  id: string
  symbol: string
  period: string
  interval: string
  initial_capital: number
  final_equity: number
  equity_curve: { time: number; equity: number; drawdown_pct: number }[]
  buy_hold_curve: { time: number; equity: number }[]
  trades: BacktestTrade[]
  metrics: BacktestMetrics
  warmup_period: number
  total_bars: number
  entry_conditions: Condition[]
  exit_conditions: Condition[]
  condition_logic: string
  position_size_pct: number
  stop_loss_pct: number
  take_profit_pct: number
  created_at?: string
}

export interface BacktestHistoryItem {
  id: string
  name: string
  symbol: string
  created_at: string
  total_return_pct: number
  num_trades: number
  sharpe_ratio: number
}

// ── Auth / Settings ──────────────────────────────────────────────────────

export interface User {
  id: string
  email: string
  settings: UserSettings
}

export interface UserSettings {
  theme: string
  default_symbol: string
  default_bar_size: string
  bot_interval: number
  watchlist: string[]
  [key: string]: unknown
}

// ── Stock Profile ─────────────────────────────────────────────────────────

export interface StockOverview {
  symbol: string
  name: string
  exchange: string | null
  sector: string | null
  industry: string | null
  description: string | null
  employees: number | null
  website: string | null
  price: number | null
  change: number | null
  change_pct: number | null
  fetched_at: number
}

export interface StockKeyStats {
  market_cap: number | null
  fifty_two_week_high: number | null
  fifty_two_week_low: number | null
  trailing_pe: number | null
  forward_pe: number | null
  trailing_eps: number | null
  forward_eps: number | null
  volume: number | null
  avg_volume: number | null
  dividend_yield: number | null
  beta: number | null
  fifty_day_ma: number | null
  two_hundred_day_ma: number | null
  fetched_at: number
}

export interface StockFinancials {
  total_revenue: number | null
  revenue_growth: number | null
  net_income: number | null
  operating_margins: number | null
  gross_margins: number | null
  profit_margins: number | null
  debt_to_equity: number | null
  current_ratio: number | null
  quarterly_revenue: { period: string; value: number }[] | null
  quarterly_net_income: { period: string; value: number }[] | null
  fetched_at: number
}

export interface StockAnalyst {
  recommendation_mean: number | null
  recommendation_key: string | null
  recommendation_period: string | null
  strong_buy: number | null
  buy: number | null
  hold: number | null
  sell: number | null
  strong_sell: number | null
  current_price: number | null
  target_mean_price: number | null
  target_high_price: number | null
  target_low_price: number | null
  target_median_price: number | null
  num_analyst_opinions: number | null
  fetched_at: number
}

export interface StockOwnership {
  held_pct_institutions: number | null
  held_pct_insiders: number | null
  top_holders: { name: string; shares: number; pct: number; value?: number; date_reported?: string }[] | null
  mutual_fund_holders: { name: string; shares: number; pct: number; value?: number; date_reported?: string }[] | null
  total_institutional_holders: number | null
  fetched_at: number
}

export interface StockEvents {
  next_earnings_date: string | null
  ex_dividend_date: string | null
  fetched_at: number
}

export interface StockNarrative {
  strengths: string[]
  risks: string[]
  outlook: string
  fetched_at: number
}

export interface FinancialTable {
  periods: string[]
  items: { label: string; values: (number | null)[] }[]
}

export interface StockFinancialStatements {
  income_statement: { annual: FinancialTable; quarterly: FinancialTable }
  balance_sheet: { annual: FinancialTable; quarterly: FinancialTable }
  cash_flow: { annual: FinancialTable; quarterly: FinancialTable }
  fetched_at: number
}

export interface StockAnalystDetail {
  upgrades_downgrades: {
    date: string
    firm: string
    to_grade: string
    from_grade: string
    action: string
    price_target_action?: string | null
    price_target?: number | null
    prior_price_target?: number | null
  }[] | null
  recommendation_trend: { period: string; strong_buy: number; buy: number; hold: number; sell: number; strong_sell: number }[] | null
  latest_recommendation: { period: string; strong_buy: number; buy: number; hold: number; sell: number; strong_sell: number } | null
  fetched_at: number
}

export interface ScorecardMetric {
  name: string
  value: number | null
  score: number | null
  grade: string
}

export interface ScorecardCategory {
  name: string
  score: number | null
  grade: string
  metrics: ScorecardMetric[]
}

export interface StockRatingScorecard {
  overall_score: number | null
  overall_grade: string
  categories: ScorecardCategory[]
  fetched_at: number
}

export interface CeoCompensation {
  salary?: number
  bonus?: number
  stock_awards?: number
  other_compensation?: number
  total_compensation?: number
  exercised_value?: number
}

export interface StockCompanyInfo {
  ceo: string | null
  ceo_title: string | null
  ceo_compensation: CeoCompensation | null
  hq_location: string | null
  phone: string | null
  ipo_date: string | null
  currency: string | null
  market_cap: number | null
  enterprise_value: number | null
  shares_outstanding: number | null
  float_shares: number | null
  sector: string | null
  industry: string | null
  employees: number | null
  officers: { name: string; title: string; age: number | null; total_pay?: number }[] | null
  fetched_at: number
}

export interface StockSplits {
  splits: { date: string; type: string; ratio: string }[]
  fetched_at: number
}

export interface StockEarningsDetail {
  next_date: string | null
  day_of_week: string | null
  eps_estimate: number | null
  revenue_estimate: number | null
  fetched_at: number
}

// ── Portfolio Analytics & Risk ───────────────────────────────────────────────

export interface PortfolioEquityPoint {
  time: number   // Unix seconds
  value: number
}

export interface PortfolioAnalytics {
  total_value: number
  day_pnl: number
  day_pnl_pct: number
  total_pnl: number
  total_pnl_pct: number
  win_rate: number
  sharpe_ratio: number
  max_drawdown_pct: number
  equity_curve: PortfolioEquityPoint[]
  benchmark_curve: PortfolioEquityPoint[]   // SPY
}

export interface DailyPnL {
  date: string   // ISO date
  pnl: number
  trades: number
}

export interface ExposureBreakdown {
  positions: {
    symbol: string
    sector: string
    value: number
    weight_pct: number
    pnl: number
    pnl_pct: number
  }[]
  sector_weights: Record<string, number>
}

export interface RiskLimitItem {
  label: string
  used: number
  limit: number
  unit: '$' | '%' | 'count'
}

export interface RiskLimits {
  limits: RiskLimitItem[]
  max_position_size_pct: number
  daily_loss_limit: number
  drawdown_limit_pct: number
  max_open_positions: number
}

export interface TradeHistoryRow {
  id: string
  symbol: string
  action: 'BUY' | 'SELL'
  quantity: number
  fill_price: number
  pnl?: number
  timestamp: string
  holding_days?: number
}

export interface CorrelationMatrix {
  symbols: string[]
  matrix: number[][]   // [i][j] = correlation between symbols[i] and symbols[j]
}

// ── Sector Rotation ──────────────────────────────────────────────────────────

export interface SectorRotation {
  symbol: string
  name: string
  quadrant: 'LEADING' | 'WEAKENING' | 'LAGGING' | 'IMPROVING'
  rs_ratio: number
  rs_momentum: number
  rs_sma: number
  perf_1w: number
  perf_1m: number
  perf_3m: number
  perf_6m: number
  perf_1y: number
  price: number
  volume: number
}

export interface SectorLeader {
  symbol: string
  name: string
  perf: number
  price: number
  volume: number
  rs_vs_sector: number
}

export interface SectorLeadersResponse {
  sector: string
  sector_name: string
  leaders: SectorLeader[]
}

export interface SectorHeatmapRow {
  symbol: string
  name: string
  '1w': number
  '1m': number
  '3m': number
  '6m': number
  '1y': number
  ytd: number
}

// ── Rule Builder (Stage 6) ───────────────────────────────────────────────

export type TemplateCategory =
  | 'all'
  | 'trend_following'
  | 'mean_reversion'
  | 'momentum'
  | 'breakout'
  | 'composite'

export interface RuleTemplate {
  id: string
  name: string
  description: string
  category: TemplateCategory
  indicators_used: string[]
  entry_conditions: Condition[]
  exit_conditions: Condition[]
  logic: 'AND' | 'OR'
  action_type: 'BUY' | 'SELL'
  cooldown_minutes: number
  built_in: boolean
  tags: string[]
}

export interface RuleVersion {
  version: number
  rule_id: string
  name: string
  conditions: Condition[]
  logic: 'AND' | 'OR'
  action: TradeAction
  cooldown_minutes: number
  created_at: string
  note?: string
  author?: string
  status?: RuleStatus
}

export interface ValidationError {
  field: string
  message: string
  severity: 'error' | 'warning'
}

export interface ValidationResult {
  valid: boolean
  errors: ValidationError[]
  warnings: ValidationError[]
  summary?: string
}

export interface RulePerformanceTrade {
  id: string
  symbol: string
  entry_date: string
  exit_date: string
  entry_price: number
  exit_price: number
  pnl: number
  pnl_pct: number
  duration_days: number
}

export interface RulePerformanceStats {
  rule_id: string
  win_rate: number
  total_trades: number
  avg_return_pct: number
  profit_factor: number
  max_drawdown_pct: number
  equity_curve: { time: number; value: number }[]
  recent_trades: RulePerformanceTrade[]
  last_computed: string
}

export interface StockProfileBundle {
  overview: StockOverview | null
  key_stats: StockKeyStats | null
  financials: StockFinancials | null
  analyst: StockAnalyst | null
  ownership: StockOwnership | null
  events: StockEvents | null
  narrative: StockNarrative | null
  financial_statements: StockFinancialStatements | null
  analyst_detail: StockAnalystDetail | null
  rating_scorecard: StockRatingScorecard | null
  company_info: StockCompanyInfo | null
  stock_splits: StockSplits | null
  earnings_detail: StockEarningsDetail | null
}

// ── Stage 7 — extended risk & analytics types ────────────────────────────────

export interface PnLSummary {
  realized_pnl: number
  realized_pnl_pct: number
  unrealized_pnl: number
  today_pnl: number
  today_pnl_pct: number
  win_rate: number
  profit_factor: number
  best_trade_pnl: number
  best_trade_symbol: string
  worst_trade_pnl: number
  worst_trade_symbol: string
  total_trades: number
}

export interface MatchedTrade {
  id: string
  symbol: string
  entry_date: string
  exit_date: string
  entry_price: number
  exit_price: number
  qty: number
  pnl: number
  pnl_pct: number
  hold_days: number
}

export interface SectorExposureRow {
  sector: string
  weight_pct: number
  value: number
  position_count: number
  pnl: number
}

export interface PortfolioRisk {
  current_drawdown_pct: number
  max_drawdown_pct: number
  max_drawdown_date: string | null
  sharpe_ratio: number | null
  sortino_ratio: number | null
  var_95: number | null
}

export type RiskCheckStatus = 'OK' | 'WARN' | 'BREACH'

export interface RiskCheckResult {
  name: string
  current: number
  limit: number
  unit: '$' | '%' | 'count'
  status: RiskCheckStatus
  description: string
}

export type RiskEventType = 'WARN' | 'BLOCK' | 'BREACH'

export interface RiskEvent {
  id: string
  timestamp: string
  type: RiskEventType
  symbol: string | null
  description: string
  resolved: boolean
}

export interface PositionSizeResult {
  shares: number
  dollar_amount: number
  portfolio_pct: number
  risk_amount: number
}

export type PositionSizeMethod = 'fixed_fractional' | 'kelly' | 'equal_weight'

export interface RiskSettings {
  max_position_size_pct: number
  daily_loss_limit: number
  drawdown_limit_pct: number
  max_open_positions: number
  max_sector_pct: number
  max_corr_threshold: number
}
