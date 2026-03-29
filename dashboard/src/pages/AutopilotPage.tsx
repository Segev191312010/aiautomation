import { useEffect, useMemo, useState } from 'react'
import TradeBotTabs from '@/components/tradebot/TradeBotTabs'
import AIActivityFeed from '@/components/autopilot/AIActivityFeed'
import AIStatusBar from '@/components/autopilot/AIStatusBar'
import AIPerformanceCard from '@/components/autopilot/AIPerformanceCard'
import CostReportPanel from '@/components/autopilot/CostReportPanel'
import AutopilotRuleLab from '@/components/rules/AutopilotRuleLab'
import { useAutopilotStore } from '@/store'
import type {
  AutopilotIntervention,
  DecisionRun,
  EvaluationRun,
  EvaluationSlice,
  RulePerformanceRow,
  SourcePerformance,
} from '@/types/advisor'
import type { Rule } from '@/types'
import {
  acknowledgeAutopilotIntervention,
  fetchAutopilotInterventions,
  fetchAutopilotPerformance,
  fetchAutopilotRulePerformance,
  fetchAutopilotRules,
  fetchAutopilotSourcePerformance,
  fetchDecisionRuns,
  fetchEvaluationRuns,
  fetchEvaluationSlices,
  postEmergencyStop,
  resetDailyLossLock,
  resetEmergencyStop,
  resolveAutopilotIntervention,
  setAutopilotMode,
} from '@/services/api'

type ConsoleTab = 'feed' | 'performance' | 'rule-lab' | 'evaluation'

function fmtUsd(value: number | null | undefined) {
  if (value == null) return '--'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(value)
}

function modeButtonClass(active: boolean) {
  return active
    ? 'bg-indigo-600 text-white shadow-sm'
    : 'bg-white text-[var(--text-secondary)] border border-[var(--border)] hover:bg-[var(--bg-hover)]'
}

export default function AutopilotPage() {
  const {
    guardrails,
    auditLog,
    aiStatus,
    error,
    learningMetrics,
    costReport,
    economicReport,
    learningWindow,
    fetchGuardrails,
    updateGuardrails,
    fetchAuditLog,
    fetchAIStatus,
    fetchLearningMetrics,
    fetchCostReport,
    fetchEconomicReport,
    revertAction,
    setLearningWindow,
  } = useAutopilotStore()

  const [activeTab, setActiveTab] = useState<ConsoleTab>('feed')
  const [rules, setRules] = useState<Rule[]>([])
  const [rulesLoading, setRulesLoading] = useState(false)
  const [sourcePerformance, setSourcePerformance] = useState<SourcePerformance[]>([])
  const [rulePerformance, setRulePerformance] = useState<RulePerformanceRow[]>([])
  const [interventions, setInterventions] = useState<AutopilotIntervention[]>([])
  const [pageError, setPageError] = useState<string | null>(null)
  const [dailyLossLimitInput, setDailyLossLimitInput] = useState('2.0')

  async function loadRules() {
    setRulesLoading(true)
    try {
      setRules(await fetchAutopilotRules())
    } catch (err) {
      setPageError(err instanceof Error ? err.message : 'Failed to load rules')
    } finally {
      setRulesLoading(false)
    }
  }

  async function loadPerformanceData() {
    try {
      const [perf, sources, rulesTable] = await Promise.all([
        fetchAutopilotPerformance(30),
        fetchAutopilotSourcePerformance(30),
        fetchAutopilotRulePerformance(30),
      ])
      setSourcePerformance(sources.length ? sources : perf.by_source)
      setRulePerformance(rulesTable)
    } catch (err) {
      setPageError(err instanceof Error ? err.message : 'Failed to load performance breakdown')
    }
  }

  async function loadInterventions() {
    try {
      setInterventions(await fetchAutopilotInterventions(false))
    } catch (err) {
      setPageError(err instanceof Error ? err.message : 'Failed to load interventions')
    }
  }

  async function refreshOperatorConsole() {
    await Promise.all([
      fetchGuardrails(),
      fetchAuditLog(),
      fetchAIStatus(),
      fetchLearningMetrics(),
      fetchCostReport(),
      fetchEconomicReport(),
      loadRules(),
      loadPerformanceData(),
      loadInterventions(),
    ])
  }

  useEffect(() => {
    void refreshOperatorConsole()
    const timer = window.setInterval(() => {
      void Promise.all([fetchAuditLog(), fetchAIStatus(), loadRules(), loadInterventions()])
    }, 30000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if (guardrails?.daily_loss_limit_pct != null) {
      setDailyLossLimitInput(String(guardrails.daily_loss_limit_pct))
    }
  }, [guardrails?.daily_loss_limit_pct])

  // S10: evaluation state
  const [decisionRuns, setDecisionRuns] = useState<DecisionRun[]>([])
  const [evaluationRuns, setEvaluationRuns] = useState<EvaluationRun[]>([])
  const [selectedEvalSlices, setSelectedEvalSlices] = useState<EvaluationSlice[]>([])

  const tabs = useMemo(() => [
    { id: 'feed', label: 'Feed', count: auditLog.length },
    { id: 'performance', label: 'Performance', count: sourcePerformance.reduce((sum, item) => sum + item.trades_count, 0) },
    { id: 'rule-lab', label: 'Rule Lab', count: rules.length },
    { id: 'evaluation', label: 'Evaluation', count: evaluationRuns.length },
  ], [auditLog.length, sourcePerformance, rules.length, evaluationRuns.length])

  async function handleKillToggle() {
    try {
      if (aiStatus?.emergency_stop) {
        await resetEmergencyStop()
      } else {
        await postEmergencyStop()
      }
      await Promise.all([fetchAIStatus(), fetchGuardrails(), fetchAuditLog()])
    } catch (err) {
      setPageError(err instanceof Error ? err.message : 'Failed to change kill switch state')
    }
  }

  async function handleDailyLossSave() {
    const value = Number(dailyLossLimitInput)
    if (!Number.isFinite(value) || value <= 0) {
      setPageError('Daily loss limit must be a positive percent')
      return
    }
    try {
      await updateGuardrails({ daily_loss_limit_pct: value })
      await fetchGuardrails()
    } catch (err) {
      setPageError(err instanceof Error ? err.message : 'Failed to update daily loss limit')
    }
  }

  async function handleDailyLossReset() {
    try {
      await resetDailyLossLock()
      await Promise.all([fetchGuardrails(), fetchAIStatus()])
    } catch (err) {
      setPageError(err instanceof Error ? err.message : 'Failed to reset daily loss lock')
    }
  }

  async function handleModeChange(mode: 'OFF' | 'PAPER' | 'LIVE') {
    try {
      await setAutopilotMode(mode, 'Mode changed from operator console')
      await Promise.all([fetchAIStatus(), fetchGuardrails(), fetchAuditLog()])
    } catch (err) {
      setPageError(err instanceof Error ? err.message : 'Failed to update autopilot mode')
    }
  }

  async function handleAcknowledgeIntervention(id: number) {
    try {
      await acknowledgeAutopilotIntervention(id)
      await loadInterventions()
    } catch (err) {
      setPageError(err instanceof Error ? err.message : 'Failed to acknowledge intervention')
    }
  }

  async function handleResolveIntervention(id: number) {
    try {
      await resolveAutopilotIntervention(id)
      await loadInterventions()
    } catch (err) {
      setPageError(err instanceof Error ? err.message : 'Failed to resolve intervention')
    }
  }

  return (
    <div className="space-y-6 pb-8">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold text-[var(--text-primary)]">AI Autopilot</h1>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            Autonomous rule management, direct trade execution, live traceability, and emergency operator controls.
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {(['OFF', 'PAPER', 'LIVE'] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => void handleModeChange(mode)}
              className={`rounded-lg px-3 py-2 text-xs font-semibold transition-colors ${modeButtonClass(aiStatus?.mode === mode)}`}
            >
              {mode}
            </button>
          ))}
        </div>
      </div>

      {(pageError || error) && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {pageError || error}
        </div>
      )}

      <AIStatusBar
        status={aiStatus}
        onKillToggle={() => void handleKillToggle()}
        onDailyLossReset={() => void handleDailyLossReset()}
      />

      <div className="rounded-2xl border border-[var(--border)] bg-white p-5">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div>
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">Daily Loss Control</h2>
            <p className="text-xs text-[var(--text-muted)]">
              The only editable runtime safety control in the operator console. New AI entries stop when breached.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <input
              value={dailyLossLimitInput}
              onChange={(event) => setDailyLossLimitInput(event.target.value)}
              className="w-24 rounded-lg border border-[var(--border)] px-3 py-2 text-sm"
              inputMode="decimal"
            />
            <span className="text-sm text-[var(--text-muted)]">% of net liq</span>
            <button
              type="button"
              onClick={() => void handleDailyLossSave()}
              className="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-700"
            >
              Save Limit
            </button>
          </div>
        </div>
      </div>

      <TradeBotTabs activeTab={activeTab} onTabChange={(tab) => setActiveTab(tab as ConsoleTab)} tabs={tabs} />

      {activeTab === 'feed' && (
        <div className="grid grid-cols-1 xl:grid-cols-[1.2fr,0.8fr] gap-5">
          <div className="rounded-2xl border border-[var(--border)] bg-white p-5">
            <div className="flex items-center justify-between gap-2 mb-4">
              <div>
                <h2 className="text-sm font-semibold text-[var(--text-primary)]">Live Activity Feed</h2>
                <p className="text-xs text-[var(--text-muted)]">
                  Every meaningful autopilot action, rejection, revert, and manual override.
                </p>
              </div>
            </div>
            <AIActivityFeed entries={auditLog} onRevert={(id) => void revertAction(id)} />
          </div>

          <div className="rounded-2xl border border-[var(--border)] bg-white p-5">
            <div className="flex items-center justify-between gap-2 mb-4">
              <div>
                <h2 className="text-sm font-semibold text-[var(--text-primary)]">Intervention Queue</h2>
                <p className="text-xs text-[var(--text-muted)]">
                  Operational issues that need human acknowledgement, mode changes, or reconciliation.
                </p>
              </div>
              <div className="text-xs text-[var(--text-muted)]">{interventions.length} open</div>
            </div>

            {interventions.length ? (
              <div className="space-y-3">
                {interventions.map((item) => (
                  <div key={item.id} className="rounded-xl border border-[var(--border)] px-4 py-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="font-medium text-[var(--text-primary)]">{item.summary}</div>
                      <span className="text-xs font-semibold uppercase tracking-[0.12em] text-red-700">
                        {item.severity}
                      </span>
                    </div>
                    <div className="mt-1 text-sm text-[var(--text-secondary)]">{item.required_action}</div>
                    <div className="mt-3 flex items-center justify-end gap-2">
                      {!item.acknowledged_at && (
                        <button
                          type="button"
                          onClick={() => void handleAcknowledgeIntervention(item.id)}
                          className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]"
                        >
                          Acknowledge
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => void handleResolveIntervention(item.id)}
                        className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700"
                      >
                        Resolve
                      </button>
                    </div>
                    <div className="mt-2 text-xs text-[var(--text-muted)]">
                      {item.category} - {item.source} - {item.symbol ?? 'system'} - {new Date(item.opened_at).toLocaleString()}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-sm text-[var(--text-muted)]">No open intervention items.</div>
            )}
          </div>
        </div>
      )}

      {activeTab === 'performance' && (
        <div className="space-y-5">
          <div className="rounded-2xl border border-[var(--border)] bg-white p-5">
            <AIPerformanceCard
              metrics={learningMetrics}
              economicReport={economicReport}
              activeWindow={learningWindow}
              onWindowChange={setLearningWindow}
            />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {sourcePerformance.map((item) => (
              <div key={item.source} className="rounded-2xl border border-[var(--border)] bg-white p-4">
                <div className="text-[10px] uppercase tracking-[0.14em] text-[var(--text-muted)]">{item.source}</div>
                <div className="mt-2 text-xl font-semibold text-[var(--text-primary)]">{fmtUsd(item.realized_pnl)}</div>
                <div className="mt-1 text-sm text-[var(--text-secondary)]">
                  {item.trades_count} trades - {item.hit_rate != null ? `${(item.hit_rate * 100).toFixed(1)}% hit rate` : 'No closed P&L yet'}
                </div>
                <div className="mt-2 text-xs text-[var(--text-muted)]">
                  Cost {fmtUsd(item.total_cost)} - ROI {item.roi != null ? item.roi.toFixed(2) : '--'}
                </div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-[1.15fr,0.85fr] gap-5">
            <div className="rounded-2xl border border-[var(--border)] bg-white p-5">
              <div className="flex items-center justify-between gap-2 mb-4">
                <div>
                  <h2 className="text-sm font-semibold text-[var(--text-primary)]">Rule Contribution</h2>
                  <p className="text-xs text-[var(--text-muted)]">
                    Separate contribution tracking for rule-driven and direct AI trading.
                  </p>
                </div>
              </div>

              {rulePerformance.length ? (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[640px]">
                    <thead>
                      <tr className="border-b border-[var(--border)] text-left text-[11px] uppercase tracking-[0.12em] text-[var(--text-muted)]">
                        <th className="py-3 pr-3 font-medium">Rule</th>
                        <th className="py-3 px-3 font-medium">Source</th>
                        <th className="py-3 px-3 font-medium">Trades</th>
                        <th className="py-3 px-3 font-medium">Hit Rate</th>
                        <th className="py-3 pl-3 text-right font-medium">Net P&L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rulePerformance.map((row) => (
                        <tr key={`${row.rule_id}:${row.source}`} className="border-b border-[var(--border)]/60 last:border-b-0">
                          <td className="py-3 pr-3">
                            <div className="font-medium text-[var(--text-primary)]">{row.rule_name}</div>
                            <div className="text-xs text-[var(--text-muted)]">{row.rule_id}</div>
                          </td>
                          <td className="py-3 px-3 text-sm text-[var(--text-secondary)]">{row.source}</td>
                          <td className="py-3 px-3 text-sm text-[var(--text-secondary)]">{row.trades_count}</td>
                          <td className="py-3 px-3 text-sm text-[var(--text-secondary)]">
                            {row.hit_rate != null ? `${(row.hit_rate * 100).toFixed(1)}%` : '--'}
                          </td>
                          <td className="py-3 pl-3 text-right text-sm font-medium text-[var(--text-primary)]">{fmtUsd(row.net_pnl)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="text-sm text-[var(--text-muted)]">No rule-level performance history yet.</div>
              )}
            </div>

            <div className="rounded-2xl border border-[var(--border)] bg-white p-5">
              <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-4">AI Cost Report</h2>
              <CostReportPanel report={costReport} />
            </div>
          </div>
        </div>
      )}

      {activeTab === 'rule-lab' && (
        rulesLoading && !rules.length ? (
          <div className="rounded-2xl border border-[var(--border)] bg-white px-5 py-8 text-sm text-[var(--text-muted)]">
            Loading AI rule inventory...
          </div>
        ) : (
          <AutopilotRuleLab rules={rules} onRefresh={loadRules} />
        )
      )}

      {activeTab === 'evaluation' && (
        <div className="space-y-5">
          {/* Decision Runs */}
          <div className="rounded-2xl border border-[var(--border)] bg-white p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-[var(--text-primary)]">Decision Runs</h2>
              <button
                type="button"
                onClick={() => { void fetchDecisionRuns(20).then(setDecisionRuns) }}
                className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs hover:bg-slate-50"
              >
                Refresh
              </button>
            </div>
            {decisionRuns.length ? (
              <div className="space-y-2">
                {decisionRuns.map(run => (
                  <div key={run.id} className="flex items-center justify-between gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-sm">
                    <div className="flex items-center gap-2">
                      <span className={`inline-flex rounded-full border px-1.5 py-0.5 text-[10px] font-semibold ${
                        run.status === 'completed' ? 'border-emerald-200 text-emerald-700' : 'border-amber-200 text-amber-700'
                      }`}>{run.status}</span>
                      <span className="text-[var(--text-primary)]">{run.source}</span>
                      <span className="text-[var(--text-muted)]">{run.mode}</span>
                    </div>
                    <div className="flex items-center gap-3 text-xs text-[var(--text-muted)]">
                      {run.model && <span>{run.model}</span>}
                      {run.aggregate_confidence != null && <span>conf: {(run.aggregate_confidence * 100).toFixed(0)}%</span>}
                      <span>{Object.values(run.item_counts).reduce((a, b) => a + b, 0)} items</span>
                      <span>{new Date(run.created_at).toLocaleString()}</span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-sm text-[var(--text-muted)]">No decision runs yet. Click Refresh to load.</div>
            )}
          </div>

          {/* Evaluation Runs */}
          <div className="rounded-2xl border border-[var(--border)] bg-white p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-[var(--text-primary)]">Evaluation Runs</h2>
              <button
                type="button"
                onClick={() => { void fetchEvaluationRuns(20).then(setEvaluationRuns) }}
                className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs hover:bg-slate-50"
              >
                Refresh
              </button>
            </div>
            {evaluationRuns.length ? (
              <div className="space-y-2">
                {evaluationRuns.map(evalRun => (
                  <div key={evalRun.id} className="rounded-lg border border-[var(--border)] px-3 py-2">
                    <div className="flex items-center justify-between gap-2 text-sm">
                      <div className="flex items-center gap-2">
                        <span className={`inline-flex rounded-full border px-1.5 py-0.5 text-[10px] font-semibold ${
                          evalRun.status === 'completed' ? 'border-emerald-200 text-emerald-700' : 'border-amber-200 text-amber-700'
                        }`}>{evalRun.status}</span>
                        <span className="text-[var(--text-primary)]">{evalRun.evaluation_mode}</span>
                        <span className="text-[var(--text-muted)]">{evalRun.candidate_key}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => {
                            void fetchEvaluationSlices(evalRun.id).then(setSelectedEvalSlices)
                          }}
                          className="rounded border border-[var(--border)] px-2 py-1 text-xs hover:bg-slate-50"
                        >
                          Slices
                        </button>
                        <span className="text-xs text-[var(--text-muted)]">{new Date(evalRun.created_at).toLocaleString()}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-sm text-[var(--text-muted)]">No evaluation runs yet. Click Refresh to load.</div>
            )}
          </div>

          {/* Evaluation Slices Detail */}
          {selectedEvalSlices.length > 0 && (
            <div className="rounded-2xl border border-[var(--border)] bg-white p-5">
              <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-4">Evaluation Slices</h2>
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b border-[var(--border)] text-left text-[10px] uppercase tracking-[0.12em] text-[var(--text-muted)]">
                      <th className="pb-2 pr-3">Type</th>
                      <th className="pb-2 pr-3">Key</th>
                      <th className="pb-2 pr-3">Count</th>
                      <th className="pb-2 pr-3">Scored</th>
                      <th className="pb-2 pr-3">Hit Rate</th>
                      <th className="pb-2 pr-3">Net P&L</th>
                      <th className="pb-2 pr-3">Coverage</th>
                      <th className="pb-2 pr-3">Calibration</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selectedEvalSlices.map((slice, idx) => (
                      <tr key={idx} className="border-b border-[var(--border)]">
                        <td className="py-2 pr-3 font-medium text-[var(--text-primary)]">{slice.slice_type}</td>
                        <td className="py-2 pr-3 text-[var(--text-secondary)]">{slice.slice_key}</td>
                        <td className="py-2 pr-3">{slice.count}</td>
                        <td className="py-2 pr-3">{slice.scored_count}</td>
                        <td className="py-2 pr-3">{slice.hit_rate != null ? `${(slice.hit_rate * 100).toFixed(1)}%` : '--'}</td>
                        <td className="py-2 pr-3">{slice.net_pnl != null ? fmtUsd(slice.net_pnl) : '--'}</td>
                        <td className="py-2 pr-3">{slice.coverage != null ? `${(slice.coverage * 100).toFixed(0)}%` : '--'}</td>
                        <td className="py-2 pr-3">{slice.calibration_error != null ? slice.calibration_error.toFixed(3) : '--'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
