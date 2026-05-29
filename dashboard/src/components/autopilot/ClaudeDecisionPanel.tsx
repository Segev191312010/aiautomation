/**
 * ClaudeDecisionPanel — surfaces the Claude worker's decision for a signal:
 * outcome, cost, prompt version, and full reasoning. Fetches lazily when a
 * signal id is provided.
 */
import { useEffect, useState } from 'react'
import { fetchDecisionForSignal } from '@/services/api/signals'
import type { ClaudeDecision, ClaudeDecisionOutcome } from '@/types/signals'

interface Props {
  signalId: string | null
}

const OUTCOME_TONE: Record<ClaudeDecisionOutcome, string> = {
  applied: 'bg-emerald-500/10 text-emerald-400',
  declined: 'bg-zinc-700/40 text-zinc-300',
  failed: 'bg-red-500/10 text-red-400',
  pending: 'bg-amber-500/10 text-amber-400',
}

function fmtCost(value: number) {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 4 }).format(value)
}

export default function ClaudeDecisionPanel({ signalId }: Props) {
  const [decision, setDecision] = useState<ClaudeDecision | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!signalId) {
      setDecision(null)
      setError(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchDecisionForSignal(signalId)
      .then((data) => { if (!cancelled) setDecision(data) })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load decision') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [signalId])

  if (!signalId) {
    return (
      <div className="flex items-center justify-center rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] px-5 py-12 text-sm text-[var(--text-muted)]">
        Select a signal to view the Claude decision.
      </div>
    )
  }

  if (loading) {
    return (
      <div className="animate-pulse space-y-2 p-4">
        {Array.from({ length: 3 }, (_, i) => <div key={i} className="h-6 rounded bg-zinc-800" />)}
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] px-5 py-12 text-sm text-[var(--text-muted)]">
        {error}
      </div>
    )
  }

  if (!decision) {
    return (
      <div className="flex items-center justify-center rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] px-5 py-12 text-sm text-[var(--text-muted)]">
        No Claude decision recorded for this signal yet.
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className={`text-[10px] font-mono px-2 py-1 rounded ${OUTCOME_TONE[decision.outcome] ?? 'bg-zinc-800 text-zinc-400'}`}>
          {decision.outcome}
        </span>
        <span className="text-xs font-mono text-[var(--text-muted)]">Cost {fmtCost(decision.cost_usd)}</span>
        <span className="text-xs font-mono text-[var(--text-muted)]">Prompt {decision.prompt_version}</span>
      </div>

      <div>
        <div className="mb-2 text-[11px] uppercase tracking-[0.12em] text-[var(--text-muted)]">Reasoning</div>
        <div className="whitespace-pre-wrap rounded-[20px] border border-[var(--border)] bg-[var(--bg-card)] p-4 text-sm leading-6 text-[var(--text-secondary)]">
          {decision.reasoning}
        </div>
      </div>
    </div>
  )
}
