import React from 'react'
import ErrorBoundary from '@/components/ui/ErrorBoundary'
import { useSwingDashboard } from '@/hooks/useSwingDashboard'
import BreadthMetricsTable from '@/components/swing/BreadthMetricsTable'
import GuruScreenerPanel from '@/components/swing/GuruScreenerPanel'
import ATRMatrix from '@/components/swing/ATRMatrix'
import Club97Table from '@/components/swing/Club97Table'
import StockbeeScans from '@/components/swing/StockbeeScans'
import LeadingIndustries from '@/components/swing/LeadingIndustries'
import StageAnalysis from '@/components/swing/StageAnalysis'
import TrendGrades from '@/components/swing/TrendGrades'

const SKELETON_WIDTHS = ['72%', '88%', '65%', '80%']

function SectionSkeleton() {
  return (
    <div className="card animate-pulse">
      <div className="h-3 w-32 bg-[var(--bg-secondary)] rounded mb-4" />
      <div className="space-y-2">
        {SKELETON_WIDTHS.map((w, i) => (
          <div key={i} className="h-5 bg-[var(--bg-secondary)] rounded" style={{ width: w }} />
        ))}
      </div>
    </div>
  )
}

export default function SwingDashboardPage() {
  const {
    breadth,
    guruResults,
    atrMatrix,
    club97,
    stockbeeResults,
    industries,
    stages,
    grades,
    loading,
    error,
    lastUpdate,
    activeGuruTab,
    activeStockbeeTab,
    setGuruTab,
    setStockbeeTab,
    refresh,
  } = useSwingDashboard()

  return (
    <div className="shell-stack max-w-[1720px] mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          {lastUpdate && (
            <p className="text-xs text-[var(--text-muted)] font-mono">
              Last updated: {lastUpdate.toLocaleTimeString()}
            </p>
          )}
        </div>
        <button
          onClick={refresh}
          disabled={loading}
          className="px-3 py-1.5 rounded-lg text-xs font-medium bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-50 transition-colors"
        >
          {loading ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="card border border-[var(--danger)]/20 bg-[var(--danger)]/5 text-sm text-[var(--danger)]">
          {error}
        </div>
      )}

      {/* Sections */}
      {loading && !breadth ? (
        <div className="shell-stack">
          {[1, 2, 3, 4].map((i) => <SectionSkeleton key={i} />)}
        </div>
      ) : (
        <>
          {/* 1. Key Metrics */}
          <ErrorBoundary>
            <BreadthMetricsTable data={breadth} />
          </ErrorBoundary>

          {/* 2. Guru-Inspired Screeners */}
          <ErrorBoundary>
            <GuruScreenerPanel
              results={guruResults}
              activeTab={activeGuruTab}
              onTabChange={setGuruTab}
            />
          </ErrorBoundary>

          {/* 3. ATR Matrix */}
          <ErrorBoundary>
            <ATRMatrix data={atrMatrix} />
          </ErrorBoundary>

          {/* 4. 97 Club */}
          <ErrorBoundary>
            <Club97Table data={club97} />
          </ErrorBoundary>

          {/* 5. Stockbee Scans */}
          <ErrorBoundary>
            <StockbeeScans
              results={stockbeeResults}
              activeTab={activeStockbeeTab}
              onTabChange={setStockbeeTab}
            />
          </ErrorBoundary>

          {/* 6. Leading Industries */}
          <ErrorBoundary>
            <LeadingIndustries data={industries} />
          </ErrorBoundary>

          {/* Two-column layout for Stage Analysis + Trend Grades */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* 7. Stage Analysis */}
            <ErrorBoundary>
              <StageAnalysis data={stages} />
            </ErrorBoundary>

            {/* 8. Trend Grades */}
            <ErrorBoundary>
              <TrendGrades data={grades} />
            </ErrorBoundary>
          </div>
        </>
      )}
    </div>
  )
}
