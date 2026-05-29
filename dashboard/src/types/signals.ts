/**
 * Signals tab types — TradingView webhook + scanner signal rows and Claude
 * worker decisions surfaced in the Autopilot operator console.
 */

export type SignalSource = 'tv_webhook' | 'scanner'

export type SignalStatus =
  | 'pending_review'
  | 'in_review'
  | 'applied'
  | 'declined_by_ai'
  | 'failed'
  | 'expired'
  | 'queued'
  | 'draining'

export interface SignalRow {
  id: string
  symbol: string
  action: string
  source: SignalSource
  status: SignalStatus
  queued_at: string
  payload: Record<string, unknown>
}

export type ClaudeDecisionOutcome = 'applied' | 'declined' | 'failed' | 'pending'

export interface ClaudeDecision {
  signal_id: string
  reasoning: string
  outcome: ClaudeDecisionOutcome
  cost_usd: number
  prompt_version: string
}
