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
  is_mock?: boolean
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
  is_mock?: boolean
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
  mock_mode: boolean
  bot_running: boolean
  last_run?: string
  next_run?: string
  bot_interval_seconds: number
}

export interface BotStatus {
  running: boolean
  last_run?: string
  next_run?: string
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

export type AppRoute = 'dashboard' | 'tradebot' | 'market' | 'simulation' | 'autopilot' | 'aisystem' | 'rules' | 'settings'

// ── Autopilot / AI automation ──────────────────────────────────────────────────

export type AutopilotMode = 'OFF' | 'PAPER' | 'LIVE'

export interface AutopilotStatus {
  mode: AutopilotMode
  autonomy_active: boolean
  shadow_mode: boolean
  emergency_stop: boolean
  daily_loss_locked: boolean
  daily_loss_limit_pct: number
  broker_connected: boolean
  open_positions_count: number
  active_rules_count: number
  direct_ai_open_trades_count: number
  last_action_at: string | null
  changes_today: number
  next_optimization_at: string | null
  last_optimization_at: string | null
  daily_budget_remaining: number
  optimizer_running: boolean
  bot_health: string | null
}

export interface AutopilotConfig {
  autopilot_mode: AutopilotMode
  emergency_stop: boolean
  daily_loss_locked: boolean
  daily_loss_limit_pct: number
}

export type RuleStatus = 'active' | 'paused' | 'retired' | 'draft' | 'shadow'

export interface AutopilotRule {
  id: string
  name: string
  symbol: string
  universe: string | null
  enabled: boolean
  conditions: Condition[]
  logic: 'AND' | 'OR'
  action: TradeAction
  cooldown_minutes: number
  last_triggered: string | null
  status: RuleStatus | string
  ai_generated: boolean
  ai_reason: string | null
  thesis: string | null
  hold_style: string | null
  version: number
  created_by: string
  supersedes_rule_id: string | null
  updated_at: string | null
}

export interface SourcePerformance {
  source: string
  trades?: number
  total_trades?: number
  hit_rate: number | null
  realized_pnl?: number
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

export interface AuditEntry {
  id: number
  timestamp: string
  action_type: string
  category: string
  description: string
  old_value: string | null
  new_value: string | null
  reason: string | null
  confidence: number | null
  status: string
  reverted_at: string | null
}

export interface AuditFeed {
  entries: AuditEntry[]
  total: number
  offset: number
  limit: number
}

export interface PromotionReadiness {
  rule_id: string
  status: string
  eligible: boolean
  reasons: string[]
  latest_validation: Record<string, unknown> | null
  data_quality_note: string | null
}
