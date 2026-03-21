/**
 * AI Advisor Summary Card — slim version for TradeBotPage.
 * Links to the full /advisor page for details.
 */
import { useEffect } from 'react'
import { useAdvisorStore, useUIStore } from '@/store'
import type { Recommendation } from '@/types/advisor'

const priorityColor: Record<string, string> = {
  high: 'text-red-700 bg-red-50 border-red-200',
  medium: 'text-amber-700 bg-amber-50 border-amber-200',
  low: 'text-indigo-700 bg-indigo-50 border-indigo-200',
}

export default function AIAdvisor() {
  const recommendations = useAdvisorStore((s) => s.recommendations)
  const aiStatus = useAdvisorStore((s) => s.aiStatus)
  const loading = useAdvisorStore((s) => s.loading)
  const fetchReport = useAdvisorStore((s) => s.fetchReport)
  const fetchAIStatus = useAdvisorStore((s) => s.fetchAIStatus)
  const setRoute = useUIStore((s) => s.setRoute)

  useEffect(() => {
    if (recommendations.length === 0 && !loading) {
      fetchReport()
      fetchAIStatus()
    }
  }, [recommendations.length, loading, fetchReport, fetchAIStatus])

  const highCount = recommendations.filter((r: Recommendation) => r.priority === 'high').length
  const topRecs = recommendations.slice(0, 3)

  return (
    <div className="bg-white border border-[var(--border)] rounded-xl overflow-hidden">
      <button
        onClick={() => setRoute('advisor')}
        className="w-full flex items-center justify-between p-4 hover:bg-[var(--bg-hover)] transition-colors"
      >
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-500 flex items-center justify-center">
            <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
            </svg>
          </div>
          <div className="text-left">
            <div className="text-sm font-semibold text-[var(--text-primary)]">AI Trading Advisor</div>
            <div className="text-[10px] text-[var(--text-muted)]">
              {loading ? 'Analyzing...' : `${recommendations.length} recommendations`}
              {highCount > 0 && ` (${highCount} high priority)`}
              {aiStatus?.shadow_mode && ' — Shadow mode'}
            </div>
          </div>
        </div>
        <span className="text-[var(--text-muted)] text-xs">View details →</span>
      </button>

      {topRecs.length > 0 && (
        <div className="border-t border-[var(--border)] px-4 py-3 space-y-1.5">
          {topRecs.map((rec, i) => (
            <div key={i} className={`text-xs px-3 py-2 rounded-lg border ${priorityColor[rec.priority]}`}>
              {rec.message}
            </div>
          ))}
          {recommendations.length > 3 && (
            <button
              onClick={() => setRoute('advisor')}
              className="text-[10px] text-indigo-600 hover:text-indigo-700 font-medium"
            >
              +{recommendations.length - 3} more recommendations →
            </button>
          )}
        </div>
      )}
    </div>
  )
}
