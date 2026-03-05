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

export interface Rule {
  id: string
  name: string
  symbol: string
  enabled: boolean
  conditions: Condition[]
  logic: 'AND' | 'OR'
  action: TradeAction
  cooldown_minutes: number
  last_triggered?: string
}

export interface RuleCreate {
  name: string
  symbol: string
  enabled?: boolean
  conditions: Condition[]
  logic?: 'AND' | 'OR'
  action: TradeAction
  cooldown_minutes?: number
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
}

// ── System status ─────────────────────────────────────────────────────────────

export interface SystemStatus {
  ibkr_connected: boolean
  ibkr_host: string
  ibkr_port: number
  is_paper: boolean
  sim_mode: boolean
  bot_running: boolean
  last_run?: string
  next_run?: string
  bot_interval_seconds: number
  features?: {
    market_diagnostics: boolean
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

export interface WsEvent {
  type: WsEventType
  [key: string]: unknown
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

export type AppRoute = 'dashboard' | 'tradebot' | 'market' | 'screener' | 'simulation' | 'backtest' | 'rules' | 'alerts' | 'settings' | 'stock'

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
  top_holders: { name: string; shares: number; pct: number }[] | null
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
  upgrades_downgrades: { date: string; firm: string; to_grade: string; from_grade: string; action: string }[] | null
  recommendation_trend: { period: string; strong_buy: number; buy: number; hold: number; sell: number; strong_sell: number }[] | null
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
