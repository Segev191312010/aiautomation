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

/** Present when retention reduced an old sweep to its hot rows. */
export type ScreenerCompaction = {
  rows_kept?: number
  rows_dropped?: number
  compacted_at?: string
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
  compacted?: ScreenerCompaction | null
}

export type MarketRegime = 'risk_on' | 'neutral' | 'risk_off'

export type MarketIndexChange = {
  symbol: string
  change_pct: string | null
}

export type MarketSectorChange = {
  sector: string
  change_pct: string
}

/** Today's newest market context (`/api/live/market`); percentages are decimal strings. */
export type MarketContextView =
  | { available: false }
  | {
      available: true
      snapshot_id: string
      session_date: string
      observed_at: string
      age_seconds: number | null
      regime: MarketRegime
      summary: string
      indexes: MarketIndexChange[]
      breadth_up_pct: string | null
      advancers: number
      decliners: number
      median_change_pct: string | null
      sector_leaders: MarketSectorChange[]
      sector_laggards: MarketSectorChange[]
    }

/** One row of the owner's manual-order ledger; the status vocabulary passes through. */
export type ManualOrder = {
  id: string
  /** The id the owner types in Telegram, e.g. `/cancel a3f9c2`. */
  short_id: string
  created_at: string
  updated_at: string | null
  source: string | null
  raw_text: string | null
  action: string | null
  symbol: string | null
  quantity: string | null
  order_type: string | null
  limit_price: string | null
  stop_price: string | null
  status: string
  reject_code: string | null
  error: string | null
  filled_qty: string | null
  avg_fill_price: string | null
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

/**
 * `/api/live/manager/*`: the AI day manager, read-only. Every route answers
 * `{ available: false }` while migration 16 is pending. Amounts are decimal strings.
 */
export type ManagerUnavailable = {
  available: false
  reason: string
  detail: string
}

export type ManagerSwitch = {
  state: 'on' | 'off'
  since: string | null
  source: 'cli' | 'telegram' | null
  reason: string | null
}

export type ManagerLatch = {
  family: string
  mode: string
  managed: boolean
  latched_at: string
}

export type ManagerMigrations = {
  applied_version: number
  code_version: number
  current: boolean
}

export type ManagerPlanCounts = {
  attempts: number
  valid: number
  in_force: string | null
}

export type ManagerSpend = {
  spend_date: string
  purpose: string
  calls: number
  spent_usd: string
  reserved_usd: string
  committed_usd: string
  budget_usd: string
  remaining_usd: string
  used_pct: number
  exhausted: boolean
}

export type ManagerDayUniverse = {
  qualified: number
  seen: number
}

export type ManagerCompetingLogin = {
  active: boolean
  last_alert_at: string | null
}

export type ManagerStatus = {
  available: true
  server_time_utc: string
  session_date: string
  switch: ManagerSwitch
  latch: ManagerLatch | null
  migrations: ManagerMigrations
  plans: ManagerPlanCounts
  spend: ManagerSpend
  day_universe: ManagerDayUniverse
  competing_login: ManagerCompetingLogin
}

export type ManagerStatusView = ManagerStatus | ManagerUnavailable

export type ManagerStrategyStep = {
  strategy: string
  step: string
  on: boolean
  reason: string
}

export type ManagerFocusEntry = {
  con_id: number
  symbol: string | null
  strategies: string[]
  note: string
}

export type ManagerDropped = {
  kind: string
  token: string
  detail: string
  widening: boolean
}

/** One plan attempt in the history; `in_force` marks only the plan that governs now. */
export type ManagerPlanSummary = {
  plan_id: string
  slot: string
  attempt: number
  created_at: string
  valid_until: string | null
  outcome: string
  valid: boolean
  in_force: boolean
  regime: string | null
  focus_count: number | null
  dropped_count: number
  latency_ms: number | null
  cost_usd: string
  input_tokens: number | null
  output_tokens: number | null
  model: string
  manager_version: string
}

export type ManagerPlan = ManagerPlanSummary & {
  strategies: ManagerStrategyStep[]
  focus: ManagerFocusEntry[]
  dropped: ManagerDropped[]
  gates: Record<string, boolean>
}

export type ManagerCurrentPlan = {
  available: true
  session_date: string
  in_force: boolean
  plan: ManagerPlan | null
  last_attempt: ManagerPlanSummary | null
  neutral_reason: string | null
}

export type ManagerCurrentPlanView = ManagerCurrentPlan | ManagerUnavailable

export type ManagerPlanHistory = {
  available: true
  session_date: string
  plans: ManagerPlanSummary[]
}

export type ManagerPlanHistoryView = ManagerPlanHistory | ManagerUnavailable

export type ManagerVerdict = {
  id: number
  created_at: string
  mode: 'managed' | 'neutral'
  step: string
  skipped: boolean
  note: string | null
  plan_id: string | null
  symbol: string | null
  strategy: string | null
  detected_at: string | null
  entry_price: string | null
  stop_price: string | null
  target_price: string | null
}

export type ManagerVerdicts = {
  available: true
  session_date: string
  verdicts: ManagerVerdict[]
}

export type ManagerVerdictsView = ManagerVerdicts | ManagerUnavailable

export type ManagerSpendView = (ManagerSpend & { available: true }) | ManagerUnavailable
