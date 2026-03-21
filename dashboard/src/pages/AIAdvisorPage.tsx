import { useEffect, useCallback } from 'react'
import { useAdvisorStore } from '@/store'
import AdvisorKPIRow from '@/components/advisor/AdvisorKPIRow'
import RecommendationsPanel from '@/components/advisor/RecommendationsPanel'
import RulePerformanceTable from '@/components/advisor/RulePerformanceTable'
import SectorChart from '@/components/advisor/SectorChart'
import TimePatternHeatmap from '@/components/advisor/TimePatternHeatmap'
import ScoreBucketChart from '@/components/advisor/ScoreBucketChart'
import BracketChart from '@/components/advisor/BracketChart'
import AutoTunePanel from '@/components/advisor/AutoTunePanel'
import DailyReportCard from '@/components/advisor/DailyReportCard'
import GuardrailsPanel from '@/components/advisor/GuardrailsPanel'
import AIActivityFeed from '@/components/advisor/AIActivityFeed'
import AIStatusBar from '@/components/advisor/AIStatusBar'
import ShadowPerformancePanel from '@/components/advisor/ShadowPerformancePanel'
import ShadowDecisionsTable from '@/components/advisor/ShadowDecisionsTable'
import AIPerformanceCard from '@/components/advisor/AIPerformanceCard'
import CostReportPanel from '@/components/advisor/CostReportPanel'

const LOOKBACK_OPTIONS = [30, 60, 90, 180, 365] as const

export default function AIAdvisorPage() {
  const {
    report, recommendations, analysis, dailyReport,
    autoTunePreview, guardrails, auditLog, aiStatus,
    loading, reportLoading, tuneLoading, lookbackDays, error,
    fetchReport, generateDailyReport,
    previewAutoTune, applyAutoTune, setLookbackDays,
    fetchGuardrails, updateGuardrails, emergencyStop,
    fetchAuditLog, revertAction, fetchAIStatus,
    shadowDecisions, shadowTotal, shadowPerformance, shadowFilters,
    fetchShadowDecisions, fetchShadowPerformance, setShadowFilters, toggleShadowMode,
    learningMetrics, costReport, economicReport, learningWindow,
    fetchLearningMetrics, fetchCostReport, fetchEconomicReport, setLearningWindow,
  } = useAdvisorStore()

  useEffect(() => {
    fetchReport()
    fetchGuardrails()
    fetchAuditLog()
    fetchAIStatus()
    fetchLearningMetrics()
    fetchCostReport()
    fetchEconomicReport()
  }, [fetchReport, fetchGuardrails, fetchAuditLog, fetchAIStatus, fetchLearningMetrics, fetchCostReport, fetchEconomicReport])

  // B20 FIX: Only fetch shadow data when shadow mode is active
  useEffect(() => {
    if (aiStatus?.shadow_mode) {
      fetchShadowPerformance()
      fetchShadowDecisions()
    }
  }, [aiStatus?.shadow_mode, fetchShadowPerformance, fetchShadowDecisions])

  const handleLookbackChange = useCallback((days: number) => {
    setLookbackDays(days)
    // Zustand set() is synchronous, so fetchReport reads the updated lookbackDays immediately
    useAdvisorStore.getState().fetchReport(true)
  }, [setLookbackDays])

  return (
    <div className="space-y-6 pb-8">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-xl font-semibold text-[var(--text-primary)]">AI Trading Advisor</h1>
          <p className="text-sm text-[var(--text-muted)] mt-0.5">
            Performance analysis, recommendations, and autonomous optimization
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* Lookback selector */}
          <div className="flex items-center gap-1 bg-white border border-[var(--border)] rounded-lg p-0.5">
            {LOOKBACK_OPTIONS.map((d) => (
              <button
                key={d}
                onClick={() => handleLookbackChange(d)}
                className={`px-2.5 py-1 text-xs font-mono rounded-md transition-colors ${
                  lookbackDays === d
                    ? 'bg-indigo-600 text-white'
                    : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]'
                }`}
              >
                {d}d
              </button>
            ))}
          </div>
          {/* Refresh */}
          <button
            onClick={() => fetchReport(true)}
            disabled={loading}
            className="px-3 py-1.5 text-xs font-medium bg-white border border-[var(--border)] rounded-lg hover:bg-[var(--bg-hover)] disabled:opacity-50 transition-colors"
          >
            {loading ? 'Loading...' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* AI Status Bar */}
      <AIStatusBar status={aiStatus} />

      {/* KPI Row */}
      <AdvisorKPIRow
        pnlSummary={report?.pnl_summary ?? null}
        performance={report?.performance ?? null}
        tradeCount={report?.trade_count ?? 0}
      />

      {/* AI Performance (Learning + Economics) */}
      <div className="bg-white border border-[var(--border)] rounded-xl p-5">
        <AIPerformanceCard
          metrics={learningMetrics}
          economicReport={economicReport}
          activeWindow={learningWindow}
          onWindowChange={setLearningWindow}
          guardrailsTightened={guardrails?.guardrails_currently_tightened}
          tightenedReason={guardrails?.tightened_reason}
        />
      </div>

      {/* Recommendations */}
      <RecommendationsPanel recommendations={recommendations} />

      {/* Rule Performance Table */}
      {analysis?.rule_performance && (
        <div className="bg-white border border-[var(--border)] rounded-xl p-5">
          <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-4">Rule Performance</h2>
          <RulePerformanceTable rules={analysis.rule_performance} />
        </div>
      )}

      {/* Analytics Grid: 2x2 */}
      {analysis && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          {analysis.sector_performance && (
            <div className="bg-white border border-[var(--border)] rounded-xl p-5">
              <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-4">Sector Performance</h2>
              <SectorChart sectors={analysis.sector_performance} />
            </div>
          )}
          {analysis.time_patterns && (
            <div className="bg-white border border-[var(--border)] rounded-xl p-5">
              <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-4">Trading Hours</h2>
              <TimePatternHeatmap patterns={analysis.time_patterns} />
            </div>
          )}
          {analysis.score_analysis && (
            <div className="bg-white border border-[var(--border)] rounded-xl p-5">
              <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-4">Signal Score Effectiveness</h2>
              <ScoreBucketChart analysis={analysis.score_analysis} />
            </div>
          )}
          {analysis.bracket_analysis && (
            <div className="bg-white border border-[var(--border)] rounded-xl p-5">
              <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-4">Stop/Target Analysis</h2>
              <BracketChart analysis={analysis.bracket_analysis} />
            </div>
          )}
        </div>
      )}

      {/* Auto-tune + Daily Report side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="bg-white border border-[var(--border)] rounded-xl p-5">
          <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-4">Auto-Tune</h2>
          <AutoTunePanel
            preview={autoTunePreview}
            onPreview={previewAutoTune}
            onApply={applyAutoTune}
            loading={tuneLoading}
          />
        </div>
        <div className="bg-white border border-[var(--border)] rounded-xl p-5">
          <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-4">Daily AI Report</h2>
          <DailyReportCard
            report={dailyReport}
            onGenerate={generateDailyReport}
            loading={reportLoading}
          />
        </div>
      </div>

      {/* Shadow Mode Validation */}
      {aiStatus?.shadow_mode && (
        <div className="space-y-5">
          <div className="bg-white border border-[var(--border)] rounded-xl p-5">
            <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-4">Shadow Mode Validation</h2>
            <ShadowPerformancePanel
              performance={shadowPerformance}
              onGoLive={() => toggleShadowMode(false)}
            />
          </div>
          <div className="bg-white border border-[var(--border)] rounded-xl p-5">
            <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-4">Shadow Decisions Log</h2>
            <ShadowDecisionsTable
              decisions={shadowDecisions}
              total={shadowTotal}
              filters={shadowFilters}
              onFiltersChange={setShadowFilters}
            />
          </div>
        </div>
      )}

      {/* Guardrails + Audit Log side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="bg-white border border-[var(--border)] rounded-xl p-5">
          <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-4">AI Guardrails</h2>
          <GuardrailsPanel
            config={guardrails}
            onUpdate={updateGuardrails}
            onEmergencyStop={emergencyStop}
          />
        </div>
        <div className="bg-white border border-[var(--border)] rounded-xl p-5">
          <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-4">AI Activity Log</h2>
          <AIActivityFeed entries={auditLog} onRevert={revertAction} />
        </div>
      </div>

      {/* Cost Report */}
      <div className="bg-white border border-[var(--border)] rounded-xl p-5">
        <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-4">API Cost Report</h2>
        <CostReportPanel report={costReport} />
      </div>

      {/* Data warning */}
      {report?.data_warning && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg px-4 py-2 text-sm text-amber-700">
          {report.data_warning}
        </div>
      )}
    </div>
  )
}
