import { vi } from 'vitest'

import type { BridgeOrder, LiveStatus, ScreenerSnapshot, Signal } from '../types'

export const TOKEN_KEY = 'autotrader.live.token'

export const STATUS_FIXTURE: LiveStatus = {
  server_time_utc: '2026-10-12T14:05:00.000Z',
  server_time_new_york: '2026-10-12T10:05:00-04:00',
  session_date: '2026-10-12',
  market: {
    session_date: '2026-10-12',
    phase: 'open',
    is_open: true,
    opens_at: '2026-10-12T09:30:00-04:00',
    closes_at: '2026-10-12T16:00:00-04:00',
    entry_cutoff_at: '2026-10-12T12:00:00-04:00',
    next_open_at: null,
    half_day: false,
  },
  snapshot_observed_at: '2026-10-12T14:04:52.000Z',
  snapshot_age_seconds: 8,
  decisions_today: { ai_abstained: 1, risk_blocked: 1, rule_rejected: 1, shadow_accepted: 1 },
  decisions_total: 4,
  orders_today: { sent: 1, open: 1, skipped: 0, needs_attention: 0, realized_pnl: '0.00' },
  bridge_state: 'ok',
}

export const SCREENER_FIXTURE: ScreenerSnapshot = {
  schema: 'screener.v1',
  session_date: '2026-10-12',
  observed_at: '2026-10-12T14:04:52.000Z',
  sweep_seconds: 12.3,
  universe_size: 503,
  qualified: 410,
  quoted: 400,
  errors: 10,
  empty: false,
  stale: false,
  snapshot_age_seconds: 8,
  rows: [
    {
      symbol: 'NVDA',
      con_id: 4815747,
      primary_exchange: 'NASDAQ',
      last: '123.45',
      change_pct: '2.88',
      gap_pct: '0.83',
      rvol: '2.40',
      volume_shares: '12345600',
      pct_from_high: '-0.44',
      score: '0.8712',
      hot: true,
      hot_rank: 1,
    },
    {
      symbol: 'AMD',
      con_id: 4391,
      primary_exchange: 'NASDAQ',
      last: '150.00',
      change_pct: '5.10',
      gap_pct: '3.00',
      rvol: '3.90',
      volume_shares: '45000000',
      pct_from_high: '-0.10',
      score: '0.7900',
      hot: true,
      hot_rank: 2,
    },
    {
      symbol: 'BRK B',
      con_id: 72063691,
      primary_exchange: 'NYSE',
      last: '470.10',
      change_pct: '-1.20',
      gap_pct: '-0.10',
      rvol: '1.10',
      volume_shares: '2100000',
      pct_from_high: '-1.50',
      score: '0.5100',
      hot: false,
      hot_rank: null,
    },
  ],
}

const INTENT = { quantity: 5266, entry_limit: '19.11', stop_price: '18.94', target_price: '19.45' }

export const SIGNALS_FIXTURE: Signal[] = [
  {
    id: 'decision-risk',
    created_at: '2026-10-12T14:01:30.000Z',
    run_id: 'momo-9c33dc48-2026-10-12',
    strategy: 'momo',
    symbol: 'AMD',
    primary_exchange: 'NASDAQ',
    outcome: 'risk_blocked',
    ai: { decision: 'PROPOSE', confidence: '0.70', rationale: 'Valid setup.' },
    risk_codes: ['max_open_positions'],
    intent: null,
    reason: 'Risk blocked: max_open_positions',
  },
  {
    id: 'decision-abstain',
    created_at: '2026-10-12T14:01:00.000Z',
    run_id: 'momo-9c33dc48-2026-10-12',
    strategy: 'momo',
    symbol: 'NVDA',
    primary_exchange: 'NASDAQ',
    outcome: 'ai_abstained',
    ai: { decision: 'ABSTAIN', confidence: '0.40', rationale: 'Extended into resistance.' },
    risk_codes: [],
    intent: null,
    reason: 'Claude abstained: Extended into resistance.',
  },
  {
    id: 'decision-rule',
    created_at: '2026-10-12T14:00:30.000Z',
    run_id: 'shadow-9c33dc48-2026-10-12',
    strategy: 'orb',
    symbol: 'AKA',
    primary_exchange: 'NYSE',
    outcome: 'rule_rejected',
    ai: null,
    risk_codes: [],
    intent: null,
    reason: 'Rules: no qualifying setup (price/liquidity/no breakout)',
  },
  {
    id: 'decision-accept',
    created_at: '2026-10-12T14:00:20.000Z',
    run_id: 'shadow-9c33dc48-2026-10-12',
    strategy: 'orb',
    symbol: 'MMED',
    primary_exchange: 'NASDAQ',
    outcome: 'shadow_accepted',
    ai: { decision: 'PROPOSE', confidence: '0.66', rationale: 'Clean opening-range breakout.' },
    risk_codes: [],
    intent: INTENT,
    reason: 'Would buy: 5266 @ 19.11',
  },
]

export const ORDERS_FIXTURE: BridgeOrder[] = [
  {
    decision_id: 'decision-accept',
    symbol: 'MMED',
    primary_exchange: 'NASDAQ',
    strategy: 'orb',
    parent_order_id: 101,
    status: 'open',
    skip_reason: null,
    decided_at: '2026-10-12T14:00:20.000Z',
    sent_at: '2026-10-12T14:00:21.123Z',
    run_id: 'shadow-9c33dc48-2026-10-12',
    intent: INTENT,
    filled_quantity: 5266,
    avg_entry_price: '19.1100',
    exit_quantity: 0,
    avg_exit_price: null,
    exit_reason: null,
    realized_pnl: '0',
  },
]

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type Handler = (path: string, init?: RequestInit) => Response

/** Route table for the four live endpoints; a fresh Response is built per call. */
export function liveApi(overrides: Record<string, () => Response> = {}): Handler {
  const routes: Record<string, () => Response> = {
    '/api/live/status': () => jsonResponse(STATUS_FIXTURE),
    '/api/live/screener': () => jsonResponse(SCREENER_FIXTURE),
    '/api/live/signals': () => jsonResponse(SIGNALS_FIXTURE),
    '/api/live/orders': () => jsonResponse(ORDERS_FIXTURE),
    ...overrides,
  }
  return (path) => {
    const route = routes[path.split('?')[0]]
    return route ? route() : jsonResponse({ detail: 'Not Found' }, 404)
  }
}

export function installFetch(handler: Handler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    return handler(url, init)
  })
  vi.stubGlobal('fetch', mock)
  return mock
}
