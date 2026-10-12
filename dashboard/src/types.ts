/** Wire types for the read-only `/api/live/*` routes. Decimals arrive as strings. */

export type DecimalValue = string | number | null | undefined

export type ScreenerRow = {
  symbol: string
  con_id?: number | null
  primary_exchange?: string | null
  source?: string | null
  last?: DecimalValue
  bid?: DecimalValue
  ask?: DecimalValue
  prior_close?: DecimalValue
  open?: DecimalValue
  day_high?: DecimalValue
  day_low?: DecimalValue
  change_pct?: DecimalValue
  gap_pct?: DecimalValue
  volume_shares?: DecimalValue
  rvol?: DecimalValue
  dollar_volume?: DecimalValue
  pct_from_high?: DecimalValue
  score?: DecimalValue
  hot?: boolean | null
  hot_rank?: number | null
}

export type ScreenerSnapshot = {
  schema?: string
  session_date?: string
  observed_at?: string
  sweep_seconds?: number | null
  universe_size?: number | null
  qualified?: number | null
  quoted?: number | null
  errors?: number | null
  rows: ScreenerRow[]
  empty?: boolean
  stale?: boolean
  snapshot_age_seconds?: number | null
}

export type MarketPhase = 'pre_open' | 'open' | 'after_close' | 'no_session' | 'unknown'

export type MarketStatus = {
  session_date: string
  phase: MarketPhase
  is_open: boolean
  opens_at: string | null
  closes_at: string | null
  entry_cutoff_at: string | null
  next_open_at: string | null
  half_day: boolean
}

export type OrderCounts = {
  sent: number
  open: number
  skipped: number
  needs_attention: number
  realized_pnl: string
}

export type LiveStatus = {
  server_time_utc: string
  server_time_new_york: string
  session_date: string
  market: MarketStatus
  snapshot_observed_at: string | null
  snapshot_age_seconds: number | null
  decisions_today: Record<string, number>
  decisions_total: number
  orders_today: OrderCounts
  bridge_state: 'ok' | 'missing' | 'unreadable'
}

export type TradeIntentView = {
  quantity: number
  entry_limit: string
  stop_price: string
  target_price: string
}

export type SignalAI = {
  decision: string
  confidence: string | null
  rationale: string
}

export type Signal = {
  id: string
  created_at: string
  run_id: string
  strategy: 'momo' | 'orb'
  symbol: string
  primary_exchange: string | null
  outcome: string
  ai: SignalAI | null
  risk_codes: string[]
  intent: TradeIntentView | null
  reason: string
}

/** The temporary PAPER bridge's bracket lifecycle; `unknown` is never treated as sent. */
export type BridgeOrderStatus =
  | 'pending'
  | 'sent'
  | 'open'
  | 'closed'
  | 'cancelled'
  | 'skipped'
  | 'needs_attention'
  | 'unknown'

export type BridgeOrder = {
  decision_id: string
  symbol: string | null
  primary_exchange: string | null
  strategy: 'momo' | 'orb' | null
  parent_order_id: number | null
  status: BridgeOrderStatus
  skip_reason: string | null
  decided_at: string | null
  sent_at: string | null
  run_id: string | null
  intent: TradeIntentView | null
  filled_quantity: number | null
  avg_entry_price: string | null
  exit_quantity: number | null
  avg_exit_price: string | null
  exit_reason: string | null
  realized_pnl: string | null
}

export type SelectedSymbol = {
  symbol: string
  exchange: string | null
}
