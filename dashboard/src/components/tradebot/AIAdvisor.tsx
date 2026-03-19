/**
 * Stage 3 — AI Advisor Dashboard Card.
 * Shows recommendations, rule performance, and daily report.
 */
import React, { useState, useEffect } from 'react'

interface Recommendation {
  type: 'disable' | 'boost' | 'adjust' | 'warning'
  priority: 'high' | 'medium' | 'low'
  message: string
  rule_id?: string
}

interface RulePerf {
  rule_id: string
  rule_name: string
  total_trades: number
  win_rate: number
  profit_factor: number
  total_pnl: number
  avg_hold_hours: number
  status: 'good' | 'ok' | 'bad'
}

export default function AIAdvisor() {
  const [recommendations, setRecommendations] = useState<Recommendation[]>([])
  const [rulePerf, setRulePerf] = useState<RulePerf[]>([])
  const [dailyReport, setDailyReport] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [reportLoading, setReportLoading] = useState(false)

  useEffect(() => {
    if (!expanded) return
    setLoading(true)
    Promise.all([
      fetch('/api/advisor/recommendations').then(r => r.json()).catch(() => ({ recommendations: [] })),
      fetch('/api/advisor/analysis').then(r => r.json()).catch(() => ({ rule_performance: [] })),
    ]).then(([recData, analysisData]) => {
      setRecommendations(recData.recommendations || [])
      setRulePerf(analysisData.rule_performance || [])
    }).finally(() => setLoading(false))
  }, [expanded])

  const generateReport = async () => {
    setReportLoading(true)
    try {
      const res = await fetch('/api/advisor/daily-report')
      const data = await res.json()
      setDailyReport(data.report || 'No report generated.')
    } catch { setDailyReport('Failed to generate report.') }
    setReportLoading(false)
  }

  const priorityColor = {
    high: 'text-red-400 bg-red-500/10 border-red-500/20',
    medium: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
    low: 'text-blue-400 bg-blue-500/10 border-blue-500/20',
  }

  const statusColor = {
    good: 'text-emerald-400',
    ok: 'text-zinc-400',
    bad: 'text-red-400',
  }

  return (
    <div className="bg-zinc-900/80 border border-zinc-800 rounded-2xl overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between p-4 hover:bg-zinc-800/30 transition-colors"
      >
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-purple-500 to-blue-500 flex items-center justify-center">
            <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
            </svg>
          </div>
          <div className="text-left">
            <div className="text-sm font-semibold text-zinc-100">AI Trading Advisor</div>
            <div className="text-[10px] text-zinc-500">Performance analysis, recommendations, daily insights</div>
          </div>
        </div>
        <span className="text-zinc-600 text-xs">{expanded ? '▾' : '▸'}</span>
      </button>

      {expanded && (
        <div className="border-t border-zinc-800 p-4 space-y-4">
          {loading ? (
            <div className="text-zinc-500 text-xs animate-pulse">Analyzing trading performance...</div>
          ) : (
            <>
              {/* Recommendations */}
              {recommendations.length > 0 && (
                <div>
                  <h4 className="text-[10px] font-mono uppercase tracking-widest text-zinc-500 mb-2">Recommendations</h4>
                  <div className="space-y-1.5">
                    {recommendations.slice(0, 5).map((rec, i) => (
                      <div key={i} className={`text-xs px-3 py-2 rounded-lg border ${priorityColor[rec.priority]}`}>
                        {rec.message}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Rule Performance Table */}
              {rulePerf.length > 0 && (
                <div>
                  <h4 className="text-[10px] font-mono uppercase tracking-widest text-zinc-500 mb-2">Rule Performance</h4>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-zinc-800">
                          <th className="py-1.5 px-2 text-left text-zinc-500 font-normal">Rule</th>
                          <th className="py-1.5 px-2 text-right text-zinc-500 font-normal">Trades</th>
                          <th className="py-1.5 px-2 text-right text-zinc-500 font-normal">Win%</th>
                          <th className="py-1.5 px-2 text-right text-zinc-500 font-normal">PF</th>
                          <th className="py-1.5 px-2 text-right text-zinc-500 font-normal">P&L</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rulePerf.map(r => (
                          <tr key={r.rule_id} className="border-b border-zinc-800/50">
                            <td className={`py-1.5 px-2 ${statusColor[r.status]}`}>{r.rule_name}</td>
                            <td className="py-1.5 px-2 text-right text-zinc-400">{r.total_trades}</td>
                            <td className={`py-1.5 px-2 text-right ${r.win_rate >= 50 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {r.win_rate.toFixed(0)}%
                            </td>
                            <td className="py-1.5 px-2 text-right text-zinc-300">{r.profit_factor.toFixed(1)}</td>
                            <td className={`py-1.5 px-2 text-right ${r.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                              ${r.total_pnl.toFixed(0)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Daily Report */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-[10px] font-mono uppercase tracking-widest text-zinc-500">Daily AI Report</h4>
                  <button
                    onClick={generateReport}
                    disabled={reportLoading}
                    className="text-[10px] px-2.5 py-1 bg-purple-500/15 text-purple-400 border border-purple-500/30 rounded-lg hover:bg-purple-500/25 disabled:opacity-40"
                  >
                    {reportLoading ? 'Generating...' : 'Generate Report'}
                  </button>
                </div>
                {dailyReport && (
                  <div className="bg-zinc-950/50 border border-zinc-800 rounded-lg p-3 text-xs text-zinc-300 whitespace-pre-wrap leading-relaxed">
                    {dailyReport}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
