import { useCallback, useEffect, useMemo, useState } from 'react'
import TradeBotTabs from '@/components/tradebot/TradeBotTabs'
import AIActivityFeed from '@/components/autopilot/AIActivityFeed'
import AIStatusBar from '@/components/autopilot/AIStatusBar'
import AIPerformanceCard from '@/components/autopilot/AIPerformanceCard'
import CostReportPanel from '@/components/autopilot/CostReportPanel'
import CircuitBreakerPanel from '@/components/autopilot/CircuitBreakerPanel'
import { SectionHeader } from '@/components/common/SectionHeader'
import PageErrorBanner from '@/components/common/PageErrorBanner'
import ConfirmModal from '@/components/common/ConfirmModal'
import ErrorBoundary from '@/components/ui/ErrorBoundary'
import { IconArrows, IconBarChart, IconDollar, IconGrid, IconHistory, IconLightning, IconShield, IconTrendUp } from '@/components/icons'
import AutopilotRuleLab from '@/components/rules/AutopilotRuleLab'
import { DecisionDrilldown } from '@/components/autopilot/DecisionDrilldown'
import { EvaluationReplay } from '@/components/autopilot/EvaluationReplay'
import { useAutopilotStore } from '@/store'
import type { Rule } from '@/types'
import type { AutopilotIntervention, RulePerformanceRow, SourcePerformance } from '@/types/advisor'
import {
  acknowledgeAutopilotIntervention,
  fetchAutopilotInterventions,
  fetchAutopilotPerformance,
  fetchAutopilotRulePerformance,
  fetchAutopilotRules,
  fetchAutopilotSourcePerformance,
  postEmergencyStop,
  resetDailyLossLock,
  resetEmergencyStop,
  resolveAutopilotIntervention,
  setAutopilotMode,
} from '@/services/api'

type ConsoleTab = 'feed' | 'performance' | 'rule-lab' | 'evaluation'

function fmtUsd(value: number | null | undefined) {
  if (value == null) return '--'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(value)
}

function formatTimestamp(value: string) {
  return new Date(value).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function modeButtonClass(active: boolean) {
  return active
    ? 'rounded-2xl bg-[var(--accent)] px-4 py-3 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-[var(--accent-hover)]'
    : 'rounded-2xl border border-[var(--border)] bg-[var(--bg-hover)] px-4 py-3 text-xs font-semibold text-[var(--text-secondary)] transition-colors hover:border-[var(--accent)] hover:text-[var(--text-primary)]'
}

function SignalCard({ label, value, tone = 'default' }: { label: string; value: string; tone?: 'default' | 'success' | 'warning' }) {
  const toneClass =
    tone === 'success'
      ? 'border-[rgba(31,157,104,0.18)] bg-[rgba(31,157,104,0.1)] text-[var(--success)]'
      : tone === 'warning'
        ? 'border-[rgba(245,158,11,0.2)] bg-[rgba(245,158,11,0.1)] text-[var(--accent)]'
        : 'border-[var(--border)] bg-[var(--bg-hover)] text-[var(--text-primary)]'

  return (
    <div className={`rounded-[24px] border p-4 ${toneClass}`}>
      <div className="shell-kicker">{label}</div>
      <div className="mt-3 text-2xl font-semibold leading-none">{value}</div>
    </div>
  )
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
  // Decision/evaluation rendering delegated to DecisionDrilldown + EvaluationReplay components
  const [pageError, setPageError] = useState<string | null>(null)
  const [dailyLossLimitInput, setDailyLossLimitInput] = useState('2.0')
  const [liveModeConfirmOpen, setLiveModeConfirmOpen] = useState(false)
  const [killResetConfirmOpen, setKillResetConfirmOpen] = useState(false)

  const loadRules = useCallback(async () => {
    setRulesLoading(true)
    try { setRules(await fetchAutopilotRules()) }
    catch (err) { throw new Error(err instanceof Error ? err.message : 'Failed to load rules') }
    finally { setRulesLoading(false) }
  }, [])

  const loadPerformanceData = useCallback(async () => {
    try {
      const [perf, sources, rulesTable] = await Promise.all([
        fetchAutopilotPerformance(30),
        fetchAutopilotSourcePerformance(30),
        fetchAutopilotRulePerformance(30),
      ])
      setSourcePerformance(sources.length ? sources : perf.by_source)
      setRulePerformance(rulesTable)
    } catch (err) {
      throw new Error(err instanceof Error ? err.message : 'Failed to load performance breakdown')
    }
  }, [])

  const loadInterventions = useCallback(async () => {
    try { setInterventions(await fetchAutopilotInterventions(false)) }
    catch (err) { throw new Error(err instanceof Error ? err.message : 'Failed to load interventions') }
  }, [])

  const refreshOperatorConsole = useCallback(async () => {
    const results = await Promise.allSettled([
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
    const rejected = results.find((result): result is PromiseRejectedResult => result.status === 'rejected')
    setPageError(rejected ? (rejected.reason instanceof Error ? rejected.reason.message : 'Some autopilot sections failed to refresh.') : null)
  }, [fetchGuardrails, fetchAuditLog, fetchAIStatus, fetchLearningMetrics, fetchCostReport, fetchEconomicReport, loadRules, loadPerformanceData, loadInterventions])

  useEffect(() => {
    void refreshOperatorConsole()
    const timer = window.setInterval(() => { void Promise.allSettled([fetchAuditLog(), fetchAIStatus(), loadRules(), loadInterventions()]) }, 30000)
    return () => window.clearInterval(timer)
  }, [refreshOperatorConsole, fetchAuditLog, fetchAIStatus, loadRules, loadInterventions])

  useEffect(() => {
    if (guardrails?.daily_loss_limit_pct != null) setDailyLossLimitInput(String(guardrails.daily_loss_limit_pct))
  }, [guardrails?.daily_loss_limit_pct])

  const openInterventions = useMemo(() => interventions.filter((item) => !item.resolved_at), [interventions])
  const topSource = useMemo(() => [...sourcePerformance].sort((a, b) => (b.realized_pnl + b.unrealized_pnl) - (a.realized_pnl + a.unrealized_pnl))[0] ?? null, [sourcePerformance])
  const tabs = useMemo(() => [
    { id: 'feed', label: 'Feed', count: auditLog.length },
    { id: 'performance', label: 'Performance', count: sourcePerformance.reduce((sum, item) => sum + item.trades_count, 0) },
    { id: 'rule-lab', label: 'Rule Lab', count: rules.length },
    { id: 'evaluation', label: 'Evaluation' },
  ], [auditLog.length, sourcePerformance, rules.length])

  async function handleKillToggle() {
    // Tripping the kill switch is always safe — allow it without a typed
    // confirmation. Resetting it re-arms the runtime and opens the order
    // path again, so require a typed phrase.
    if (aiStatus?.emergency_stop) {
      setKillResetConfirmOpen(true)
      return
    }
    try {
      await postEmergencyStop()
      await Promise.all([fetchAIStatus(), fetchGuardrails(), fetchAuditLog()])
    } catch (err) {
      setPageError(err instanceof Error ? err.message : 'Failed to change kill switch state')
    }
  }

  async function handleKillResetConfirm() {
    setKillResetConfirmOpen(false)
    try {
      await resetEmergencyStop()
      await Promise.all([fetchAIStatus(), fetchGuardrails(), fetchAuditLog()])
    } catch (err) {
      setPageError(err instanceof Error ? err.message : 'Failed to reset kill switch')
    }
  }

  async function handleDailyLossSave() {
    const value = Number(dailyLossLimitInput)
    if (!Number.isFinite(value) || value <= 0) return setPageError('Daily loss limit must be a positive percent')
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

  async function applyModeChange(mode: 'OFF' | 'PAPER' | 'LIVE') {
    try {
      await setAutopilotMode(mode, 'Mode changed from operator console')
      await Promise.all([fetchAIStatus(), fetchGuardrails(), fetchAuditLog()])
    } catch (err) {
      setPageError(err instanceof Error ? err.message : 'Failed to update autopilot mode')
    }
  }

  async function handleModeChange(mode: 'OFF' | 'PAPER' | 'LIVE') {
    // Flipping to LIVE grants AI real-money authority. Gate behind a typed
    // confirmation so a stray click from the operator console cannot flip it.
    if (mode === 'LIVE' && aiStatus?.mode !== 'LIVE') {
      setLiveModeConfirmOpen(true)
      return
    }
    await applyModeChange(mode)
  }

  async function handleLiveModeConfirm() {
    setLiveModeConfirmOpen(false)
    await applyModeChange('LIVE')
  }

  return (
    <div className="flex flex-col gap-6 pb-4">
      <ErrorBoundary>
      <section className="grid gap-6 xl:grid-cols-[minmax(0,1.25fr)_minmax(320px,0.75fr)]">
        <div className="shell-panel relative overflow-hidden p-6 sm:p-7">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(245,158,11,0.16),transparent_34%)]" />
          <div className="relative">
            <div className="shell-kicker">Operator console</div>
            <div className="mt-3 flex flex-wrap items-center gap-2.5">
              <h1 className="display-font text-[2.7rem] leading-none text-[var(--text-primary)] sm:text-[3.2rem]">AI Autopilot</h1>
              <span className="shell-chip text-[11px] font-semibold">Mode {aiStatus?.mode ?? 'OFF'}</span>
              <span className="shell-chip text-[11px] font-semibold">{aiStatus?.emergency_stop ? 'Emergency stop active' : 'Runtime armed'}</span>
              <span className="shell-chip text-[11px] font-semibold">{openInterventions.length} open intervention{openInterventions.length === 1 ? '' : 's'}</span>
            </div>
            <p className="mt-4 max-w-3xl text-sm leading-7 text-[var(--text-secondary)]">
              Keep AI authority, intervention handling, cost visibility, and evaluation evidence in one operator desk.
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              <span className="shell-chip text-[11px] font-medium">Learning window {learningWindow}d</span>
              <span className="shell-chip text-[11px] font-medium">{auditLog.length} recent audit event{auditLog.length === 1 ? '' : 's'}</span>
              {topSource && <span className="shell-chip text-[11px] font-medium">Best source {topSource.source}</span>}
            </div>
            <div className="mt-6 flex flex-wrap gap-2">
              {(['OFF', 'PAPER', 'LIVE'] as const).map((mode) => (
                <button key={mode} type="button" onClick={() => void handleModeChange(mode)} className={modeButtonClass(aiStatus?.mode === mode)}>
                  {mode}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-3 xl:grid-cols-1">
          <SignalCard label="Autonomy" value={aiStatus?.mode ?? 'OFF'} tone={aiStatus?.mode === 'LIVE' ? 'success' : aiStatus?.mode === 'PAPER' ? 'warning' : 'default'} />
          <SignalCard label="Interventions" value={String(openInterventions.length)} tone={openInterventions.length === 0 ? 'success' : 'warning'} />
          <SignalCard label="Learning Window" value={`${learningWindow}d`} />
        </div>
      </section>

      </ErrorBoundary>

      <PageErrorBanner show={Boolean(pageError || error)} message={pageError || error || undefined} />

      <ErrorBoundary>
      <section className="grid gap-6 xl:grid-cols-[minmax(0,1.1fr)_minmax(360px,0.9fr)]">
        <div className="space-y-6">
          <div className="shell-panel p-5 sm:p-6">
            <SectionHeader icon={<IconShield className="h-3.5 w-3.5 text-rose-500" />} eyebrow="Runtime" title="Autopilot Control" badge={aiStatus ? <span className="shell-chip px-3 py-1 text-[10px] font-mono">{aiStatus.broker_connected ? 'Broker connected' : 'Broker offline'}</span> : null} />
            <div className="mt-4"><AIStatusBar status={aiStatus} onKillToggle={() => void handleKillToggle()} onDailyLossReset={() => void handleDailyLossReset()} /></div>
          </div>
          <div className="shell-panel p-5 sm:p-6">
            <SectionHeader icon={<IconDollar className="h-3.5 w-3.5 text-emerald-500" />} eyebrow="Guardrail" title="Daily Loss Control" badge={guardrails?.daily_loss_locked ? <span className="shell-chip px-3 py-1 text-[10px] font-mono border-[rgba(245,158,11,0.28)] bg-[rgba(245,158,11,0.12)] text-[var(--accent)]">Locked</span> : null} />
            <div className="mt-4 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
              <div className="max-w-2xl text-sm leading-6 text-[var(--text-secondary)]">Operator-owned daily loss guardrail for new AI entries.</div>
              <div className="flex flex-wrap items-center gap-2">
                <input value={dailyLossLimitInput} onChange={(event) => setDailyLossLimitInput(event.target.value)} className="w-28 rounded-2xl border border-[var(--border)] bg-[var(--bg-input)] px-4 py-3 text-sm font-medium text-[var(--text-primary)] outline-none transition-colors focus:border-[var(--accent)]" inputMode="decimal" />
                <span className="text-sm text-[var(--text-muted)]">% of net liq</span>
                <button type="button" onClick={() => void handleDailyLossSave()} className="rounded-2xl bg-[var(--accent)] px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-[var(--accent-hover)]">Save limit</button>
              </div>
            </div>
          </div>
        </div>
        <div className="shell-panel p-5 sm:p-6">
          <SectionHeader icon={<IconLightning className="h-3.5 w-3.5 text-[var(--accent)]" />} eyebrow="Protection" title="Circuit Breaker" badge={<span className="shell-chip px-3 py-1 text-[10px] font-mono">30s refresh</span>} />
          <div className="mt-4"><CircuitBreakerPanel /></div>
        </div>
      </section>

      </ErrorBoundary>

      <section className="animate-fade-in-up" style={{ animationDelay: '40ms' }}>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="shell-kicker">Workspace</div>
            <p className="mt-2 max-w-xl text-sm leading-6 text-[var(--text-secondary)]">Move between audit feed, learning performance, rule inventory, and evaluation evidence without leaving the operator console.</p>
          </div>
          <TradeBotTabs activeTab={activeTab} onTabChange={(tab) => setActiveTab(tab as ConsoleTab)} tabs={tabs} />
        </div>
      </section>

      {activeTab === 'feed' && (<ErrorBoundary>
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)]">
          <section className="shell-panel animate-fade-in-up p-5 sm:p-6" style={{ animationDelay: '60ms' }}>
            <SectionHeader icon={<IconHistory className="h-3.5 w-3.5 text-indigo-500" />} eyebrow="Audit" title="Live Activity Feed" badge={<span className="shell-chip px-3 py-1 text-[10px] font-mono">Realtime operator trail</span>} />
            <div className="mt-4"><AIActivityFeed entries={auditLog} onRevert={(id) => void revertAction(id)} /></div>
          </section>
          <section className="shell-panel animate-fade-in-up p-5 sm:p-6" style={{ animationDelay: '90ms' }}>
            <SectionHeader icon={<IconShield className="h-3.5 w-3.5 text-rose-500" />} eyebrow="Operations" title="Intervention Queue" badge={<span className="shell-chip px-3 py-1 text-[10px] font-mono">{openInterventions.length} open</span>} />
            <div className="mt-4 space-y-3">
              {openInterventions.length ? openInterventions.map((item) => (
                <div key={item.id} className="rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] p-4">
                  <div className="flex items-center justify-between gap-3"><div className="text-sm font-semibold text-[var(--text-primary)]">{item.summary}</div><span className="shell-chip px-3 py-1 text-[10px] font-mono">{item.severity}</span></div>
                  <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">{item.required_action}</p>
                  <div className="mt-3 flex flex-wrap gap-3 text-[11px] font-mono text-[var(--text-muted)]"><span>{item.category}</span><span>{item.source}</span><span>{item.symbol ?? 'system'}</span><span>{formatTimestamp(item.opened_at)}</span></div>
                  <div className="mt-4 flex justify-end gap-2">
                    {!item.acknowledged_at && <button type="button" onClick={() => void acknowledgeAutopilotIntervention(item.id).then(loadInterventions).catch((err) => setPageError(err instanceof Error ? err.message : 'Failed to acknowledge intervention'))} className="rounded-2xl border border-[var(--border)] px-4 py-2 text-xs font-semibold text-[var(--text-secondary)] transition-colors hover:border-[var(--accent)] hover:text-[var(--text-primary)]">Acknowledge</button>}
                    <button type="button" onClick={() => void resolveAutopilotIntervention(item.id).then(loadInterventions).catch((err) => setPageError(err instanceof Error ? err.message : 'Failed to resolve intervention'))} className="rounded-2xl bg-[var(--accent)] px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-[var(--accent-hover)]">Resolve</button>
                  </div>
                </div>
              )) : <div className="flex items-center justify-center rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] px-5 py-12 text-sm text-[var(--text-muted)]">No open intervention items.</div>}
            </div>
          </section>
        </div>
      </ErrorBoundary>)}

      {activeTab === 'performance' && (<ErrorBoundary>
        <div className="space-y-6">
          <div className="grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(320px,0.85fr)]">
            <section className="shell-panel animate-fade-in-up p-5 sm:p-6" style={{ animationDelay: '60ms' }}>
              <SectionHeader icon={<IconTrendUp className="h-3.5 w-3.5 text-emerald-500" />} eyebrow="Learning" title="AI Performance" badge={<span className="shell-chip px-3 py-1 text-[10px] font-mono">{learningWindow}d window</span>} />
              <div className="mt-4"><AIPerformanceCard metrics={learningMetrics} economicReport={economicReport} activeWindow={learningWindow} onWindowChange={setLearningWindow} /></div>
            </section>
            <section className="shell-panel animate-fade-in-up p-5 sm:p-6" style={{ animationDelay: '90ms' }}>
              <SectionHeader icon={<IconDollar className="h-3.5 w-3.5 text-indigo-500" />} eyebrow="Costs" title="AI Cost Report" />
              <div className="mt-4"><CostReportPanel report={costReport} /></div>
            </section>
          </div>
          <section className="shell-panel animate-fade-in-up p-5 sm:p-6" style={{ animationDelay: '120ms' }}>
            <SectionHeader icon={<IconBarChart className="h-3.5 w-3.5 text-[var(--accent)]" />} eyebrow="Sources" title="Performance by Source" badge={topSource ? <span className="shell-chip px-3 py-1 text-[10px] font-mono">Best {topSource.source}</span> : null} />
            <div className="mt-4 grid gap-4 lg:grid-cols-3">{sourcePerformance.map((item) => <div key={item.source} className="rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] p-4"><div className="shell-kicker">{item.source}</div><div className="mt-3 text-2xl font-semibold text-[var(--text-primary)]">{fmtUsd(item.realized_pnl + item.unrealized_pnl)}</div><div className="mt-1 text-sm text-[var(--text-secondary)]">{item.trades_count} trades and {item.hit_rate != null ? `${(item.hit_rate * 100).toFixed(1)}% hit rate` : 'no closed hit rate yet'}</div><div className="mt-2 text-[11px] font-mono text-[var(--text-muted)]">Cost {fmtUsd(item.total_cost)} and ROI {item.roi != null ? item.roi.toFixed(2) : '--'}</div></div>)}</div>
          </section>
          <section className="shell-panel animate-fade-in-up p-5 sm:p-6" style={{ animationDelay: '160ms' }}>
            <SectionHeader icon={<IconGrid className="h-3.5 w-3.5 text-slate-500" />} eyebrow="Contribution" title="Rule Contribution" badge={<span className="shell-chip px-3 py-1 text-[10px] font-mono">{rulePerformance.length} rows</span>} />
            <div className="mt-4">{rulePerformance.length ? <div className="overflow-x-auto"><table className="w-full min-w-[640px]"><thead><tr className="border-b border-[var(--border)] text-left text-[11px] uppercase tracking-[0.12em] text-[var(--text-muted)]"><th className="py-3 pr-3 font-medium">Rule</th><th className="py-3 px-3 font-medium">Source</th><th className="py-3 px-3 font-medium">Trades</th><th className="py-3 px-3 font-medium">Hit Rate</th><th className="py-3 pl-3 text-right font-medium">Net P&L</th></tr></thead><tbody>{rulePerformance.map((row) => <tr key={`${row.rule_id}:${row.source}`} className="border-b border-[var(--border)]/60 last:border-b-0"><td className="py-3 pr-3"><div className="font-medium text-[var(--text-primary)]">{row.rule_name}</div><div className="text-xs text-[var(--text-muted)]">{row.rule_id}</div></td><td className="py-3 px-3 text-sm text-[var(--text-secondary)]">{row.source}</td><td className="py-3 px-3 text-sm text-[var(--text-secondary)]">{row.trades_count}</td><td className="py-3 px-3 text-sm text-[var(--text-secondary)]">{row.hit_rate != null ? `${(row.hit_rate * 100).toFixed(1)}%` : '--'}</td><td className="py-3 pl-3 text-right text-sm font-medium text-[var(--text-primary)]">{fmtUsd(row.net_pnl)}</td></tr>)}</tbody></table></div> : <div className="flex items-center justify-center rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] px-5 py-12 text-sm text-[var(--text-muted)]">No rule-level performance history yet.</div>}</div>
          </section>
        </div>
      </ErrorBoundary>)}

      {activeTab === 'rule-lab' && (<ErrorBoundary>{rulesLoading && !rules.length ? <div className="shell-panel rounded-[28px] px-5 py-8 text-sm text-[var(--text-muted)]">Loading AI rule inventory...</div> : <section className="shell-panel animate-fade-in-up p-5 sm:p-6" style={{ animationDelay: '60ms' }}><SectionHeader icon={<IconArrows className="h-3.5 w-3.5 text-indigo-500" />} eyebrow="Rule Lab" title="Autopilot Rule Inventory" badge={<span className="shell-chip px-3 py-1 text-[10px] font-mono">{rules.length} rules</span>} /><div className="mt-4"><AutopilotRuleLab rules={rules} onRefresh={loadRules} /></div></section>}</ErrorBoundary>)}

      {activeTab === 'evaluation' && (<ErrorBoundary>
        <div className="space-y-6">
          <section className="shell-panel animate-fade-in-up p-5 sm:p-6" style={{ animationDelay: '60ms' }}>
            <SectionHeader icon={<IconHistory className="h-3.5 w-3.5 text-indigo-500" />} eyebrow="Decision Ledger" title="Decision Runs" />
            <div className="mt-4">
              <DecisionDrilldown />
            </div>
          </section>
          <section className="shell-panel animate-fade-in-up p-5 sm:p-6" style={{ animationDelay: '90ms' }}>
            <SectionHeader icon={<IconGrid className="h-3.5 w-3.5 text-slate-500" />} eyebrow="Evaluation" title="Evaluation Runs" />
            <div className="mt-4">
              <EvaluationReplay />
            </div>
          </section>
        </div>
      </ErrorBoundary>)}

      <ConfirmModal
        open={liveModeConfirmOpen}
        title="Enable LIVE autopilot"
        description={
          <>
            <p>This grants AI authority to place real-money orders on your brokerage account.</p>
            <p>The backend matrix check must also succeed or the change will be rejected.</p>
          </>
        }
        confirmPhrase="GO LIVE"
        confirmLabel="Enable LIVE"
        destructive
        onConfirm={() => { void handleLiveModeConfirm() }}
        onCancel={() => setLiveModeConfirmOpen(false)}
      />

      <ConfirmModal
        open={killResetConfirmOpen}
        title="Reset emergency stop"
        description={
          <>
            <p>Resetting the kill switch re-arms the runtime and allows AI entries to resume.</p>
            <p>Confirm only after you have reviewed what triggered the stop.</p>
          </>
        }
        confirmPhrase="RESET KILL"
        confirmLabel="Reset kill switch"
        destructive
        onConfirm={() => { void handleKillResetConfirm() }}
        onCancel={() => setKillResetConfirmOpen(false)}
      />
    </div>
  )
}
