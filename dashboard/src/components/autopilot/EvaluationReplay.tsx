/**
 * Evaluation replay panel (F5-05 stub) — wired to existing API functions.
 * Shows evaluation runs with slice details.
 */
import { useState, useEffect } from 'react'
import { fetchEvaluationRuns, fetchEvaluationSlices } from '@/services/api'
import type { EvaluationRun, EvaluationSlice } from '@/types/advisor'
import { fmtTimestamp, fmtPct } from '@/utils/formatters'

export function EvaluationReplay() {
  const [runs, setRuns] = useState<EvaluationRun[]>([])
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [slices, setSlices] = useState<EvaluationSlice[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchEvaluationRuns(20, 0)
      .then(setRuns)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const toggleRun = async (evalId: string) => {
    if (expandedId === evalId) {
      setExpandedId(null)
      setSlices([])
      return
    }
    setExpandedId(evalId)
    try {
      const s = await fetchEvaluationSlices(evalId)
      setSlices(s)
    } catch {
      setSlices([])
    }
  }

  if (loading) {
    return <div className="animate-pulse space-y-2 p-4">{Array.from({ length: 3 }, (_, i) => <div key={i} className="h-8 rounded bg-zinc-800" />)}</div>
  }

  if (runs.length === 0) {
    return (
      <div className="text-center py-8 text-zinc-500 text-sm">
        No evaluation replays yet. Launch one from the autopilot config.
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
              <span className="text-xs font-sans text-zinc-200">{run.candidate_type}: {run.candidate_key}</span>
              <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                run.status === 'completed' ? 'bg-emerald-500/10 text-emerald-400' :
                run.status === 'running' ? 'bg-blue-500/10 text-blue-400' :
                'bg-zinc-800 text-zinc-400'
              }`}>{run.status}</span>
            </div>
            <svg viewBox="0 0 24 24" fill="none" className={`w-3.5 h-3.5 text-zinc-500 transition-transform ${expandedId === run.id ? 'rotate-180' : ''}`}>
              <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>

          {expandedId === run.id && slices.length > 0 && (
            <div className="border-t border-zinc-800">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-zinc-500 border-b border-zinc-800/50">
                    <th className="px-4 py-1.5 text-left font-normal">Slice</th>
                    <th className="px-4 py-1.5 text-right font-normal">Count</th>
                    <th className="px-4 py-1.5 text-right font-normal">Hit Rate</th>
                    <th className="px-4 py-1.5 text-right font-normal">Net PnL</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/30">
                  {slices.map((s, i) => (
                    <tr key={i} className="text-zinc-300">
                      <td className="px-4 py-1.5 font-mono">{s.slice_key}</td>
                      <td className="px-4 py-1.5 text-right">{s.count}</td>
                      <td className="px-4 py-1.5 text-right">{fmtPct(s.hit_rate)}</td>
                      <td className={`px-4 py-1.5 text-right font-mono ${(s.net_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        ${(s.net_pnl ?? 0).toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
