/**
 * AutopilotPage — Operator console for the AI autopilot trading system.
 * Replaces AIAdvisorPage with a leaner, tab-driven layout.
 *
 * Three tabs:
 *   Feed        — live activity feed + intervention queue
 *   Performance — AI learning metrics, source/rule P&L, cost report
 *   Rule Lab    — AI-managed rule inventory with version history
 *
 * The AutopilotStatusStrip (mode badge + kill switch) is always visible
 * regardless of the active tab.
 */
import React, { useEffect, useMemo, useState } from 'react'
import TradeBotTabs from '@/components/tradebot/TradeBotTabs'
import AutopilotStatusStrip from '@/components/autopilot/AutopilotStatusStrip'
import FeedTab from '@/components/autopilot/FeedTab'
import PerformanceTab from '@/components/autopilot/PerformanceTab'
import RuleLabTab from '@/components/autopilot/RuleLabTab'
import { useAdvisorStore } from '@/store'
import { setAutopilotMode } from '@/services/api'

type ConsoleTab = 'feed' | 'performance' | 'rules'

// ── Mode selector ─────────────────────────────────────────────────────────────

function ModeSelector({
  currentMode,
  onChange,
}: {
  currentMode: 'OFF' | 'PAPER' | 'LIVE' | undefined
  onChange: (mode: 'OFF' | 'PAPER' | 'LIVE') => void
}) {
  return (
    <div className="flex items-center gap-1 p-1 bg-[var(--bg-hover)] rounded-xl">
      {(['OFF', 'PAPER', 'LIVE'] as const).map((mode) => (
        <button
          key={mode}
          type="button"
          onClick={() => onChange(mode)}
          className={[
            'px-3 py-1.5 text-xs font-sans font-semibold rounded-lg transition-colors',
            currentMode === mode
              ? mode === 'LIVE'
                ? 'bg-emerald-600 text-white shadow-sm'
                : mode === 'PAPER'
                  ? 'bg-amber-500 text-white shadow-sm'
                  : 'bg-zinc-500 text-white shadow-sm'
              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-white/60',
          ].join(' ')}
        >
          {mode}
        </button>
      ))}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AutopilotPage() {
  const {
    aiStatus,
    auditLog,
    error,
    fetchGuardrails,
    fetchAuditLog,
    fetchAIStatus,
    fetchLearningMetrics,
    fetchCostReport,
    fetchEconomicReport,
  } = useAdvisorStore()

  const [tab, setTab] = useState<ConsoleTab>('feed')
  const [pageError, setPageError] = useState<string | null>(null)

  // Bootstrap all data on mount; poll status + feed every 30 s
  useEffect(() => {
    void Promise.all([
      fetchGuardrails(),
      fetchAuditLog(),
      fetchAIStatus(),
      fetchLearningMetrics(),
      fetchCostReport(),
      fetchEconomicReport(),
    ])

    const timer = window.setInterval(() => {
      void Promise.all([fetchAuditLog(), fetchAIStatus()])
    }, 30_000)

    return () => window.clearInterval(timer)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleModeChange(mode: 'OFF' | 'PAPER' | 'LIVE') {
    try {
      await setAutopilotMode(mode, 'Mode changed from operator console')
      await Promise.all([fetchAIStatus(), fetchGuardrails(), fetchAuditLog()])
    } catch (err) {
      setPageError(err instanceof Error ? err.message : 'Failed to update autopilot mode')
    }
  }

  const tabs = useMemo(() => [
    { id: 'feed',        label: 'Live Feed',    count: auditLog.length || undefined },
    { id: 'performance', label: 'Performance' },
    { id: 'rules',       label: 'Rule Lab' },
  ], [auditLog.length])

  const combinedError = pageError ?? error

  return (
    <div className="space-y-5 pb-8">

      {/* Page header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold text-[var(--text-primary)]">AI Autopilot</h1>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            Autonomous rule management, direct trade execution, live traceability, and emergency controls.
          </p>
        </div>
        <ModeSelector
          currentMode={aiStatus?.mode}
          onChange={(mode) => void handleModeChange(mode)}
        />
      </div>

      {/* Error banner */}
      {combinedError && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm font-sans text-red-700">
          {combinedError}
        </div>
      )}

      {/* Status strip — always visible */}
      <AutopilotStatusStrip />

      {/* Tab selector */}
      <TradeBotTabs
        tabs={tabs}
        activeTab={tab}
        onTabChange={(t) => setTab(t as ConsoleTab)}
      />

      {/* Tab content */}
      {tab === 'feed'        && <FeedTab />}
      {tab === 'performance' && <PerformanceTab />}
      {tab === 'rules'       && <RuleLabTab />}

    </div>
  )
}
