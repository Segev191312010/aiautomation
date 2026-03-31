/**
 * Decision drilldown panel (F5-05 stub) — wired to existing API functions.
 * Shows decision run items in an expandable table.
 */
import { useState, useEffect } from 'react'
import { fetchDecisionRuns, fetchDecisionRunItems } from '@/services/api'
import type { DecisionRun, DecisionItem } from '@/types/advisor'
import { fmtTimestamp } from '@/utils/formatters'

export function DecisionDrilldown() {
  const [runs, setRuns] = useState<DecisionRun[]>([])
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null)
  const [items, setItems] = useState<DecisionItem[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchDecisionRuns(20, 0)
      .then(setRuns)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const toggleRun = async (runId: string) => {
    if (expandedRunId === runId) {
      setExpandedRunId(null)
      setItems([])
      return
    }
    setExpandedRunId(runId)
    try {
      const runItems = await fetchDecisionRunItems(runId)
      setItems(runItems)
    } catch {
      setItems([])
    }
  }

  if (loading) {
    return <div className="animate-pulse space-y-2 p-4">{Array.from({ length: 3 }, (_, i) => <div key={i} className="h-8 rounded bg-zinc-800" />)}</div>
  }

  if (runs.length === 0) {
    return (
      <div className="text-center py-8 text-zinc-500 text-sm">
        No decision runs recorded yet.
      </div>
    )
  }

  return (
    <div className="space-y-1">
      {runs.map((run) => (
        <div key={run.id} className="border border-zinc-800 rounded-xl overflow-hidden">
          <button
            type="button"
            onClick={() => toggleRun(run.id)}
            className="w-full flex items-center justify-between px-4 py-2.5 text-left hover:bg-zinc-800/40 transition-colors"
          >
            <div className="flex items-center gap-3">
              <span className="text-xs font-mono text-zinc-400">{fmtTimestamp(run.created_at)}</span>
              <span className="text-xs font-sans text-zinc-200">{run.source}</span>
              <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                run.status === 'completed' ? 'bg-emerald-500/10 text-emerald-400' :
                run.status === 'error' ? 'bg-red-500/10 text-red-400' :
                'bg-zinc-800 text-zinc-400'
              }`}>{run.status}</span>
            </div>
            <svg viewBox="0 0 24 24" fill="none" className={`w-3.5 h-3.5 text-zinc-500 transition-transform ${expandedRunId === run.id ? 'rotate-180' : ''}`}>
              <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>

          {expandedRunId === run.id && items.length > 0 && (
            <div className="border-t border-zinc-800 divide-y divide-zinc-800/50">
              {items.map((item) => (
                <div key={item.id} className="px-4 py-2 text-xs font-sans">
                  <div className="flex items-center gap-2">
                    <span className="text-zinc-200 font-medium">{item.symbol ?? item.target_key ?? '—'}</span>
                    <span className="font-mono text-zinc-400">{item.item_type}</span>
                    {item.action_name && (
                      <span className={`font-mono ${item.action_name === 'BUY' ? 'text-emerald-400' : 'text-red-400'}`}>{item.action_name}</span>
                    )}
                    <span className={`text-[10px] px-1 rounded ${
                      item.gate_status === 'passed' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-amber-500/10 text-amber-400'
                    }`}>{item.gate_status}</span>
                    {item.gate_reason && <span className="text-zinc-500 ml-auto">{item.gate_reason}</span>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
