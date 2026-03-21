/**
 * DailyReportCard — Shows AI-generated daily trading report.
 * Displays a "Generate AI Report" button and renders the report
 * in a styled prose card when available.
 * Data comes from props — no API calls.
 */
import React from 'react'
import clsx from 'clsx'

// ── Spinner ───────────────────────────────────────────────────────────────────

function Spinner() {
  return (
    <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  report:      string
  onGenerate:  () => void
  loading:     boolean
}

export default function DailyReportCard({ report, onGenerate, loading }: Props) {
  return (
    <div className="space-y-3">
      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <p className="text-xs font-sans text-[var(--text-secondary)]">
          AI-generated analysis of your recent trading activity.
        </p>
        <button
          onClick={onGenerate}
          disabled={loading}
          className={clsx(
            'flex items-center gap-2 px-3.5 py-2 text-xs font-sans font-medium rounded-lg transition-colors',
            'border border-[var(--border)] bg-white text-[var(--text-primary)]',
            'hover:bg-[var(--bg-hover)] disabled:opacity-50 disabled:cursor-not-allowed',
          )}
        >
          {loading ? (
            <>
              <Spinner />
              <span>Generating...</span>
            </>
          ) : (
            <>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
                strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
                <path d="M12 2a5 5 0 0 1 5 5c0 2.76-2.24 5-5 5S7 9.76 7 7a5 5 0 0 1 5-5z" />
                <path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
              </svg>
              <span>Generate AI Report</span>
            </>
          )}
        </button>
      </div>

      {/* Loading skeleton */}
      {loading && (
        <div className="bg-[var(--bg-hover)] border border-[var(--border)] rounded-xl p-4 space-y-2 animate-pulse">
          {[80, 60, 90, 50, 70].map((w, i) => (
            <div key={i} className="h-3 rounded bg-[var(--border)]" style={{ width: `${w}%` }} />
          ))}
        </div>
      )}

      {/* Report content */}
      {report && !loading && (
        <div className="bg-white border border-[var(--border)] rounded-xl p-4">
          <div className="text-[10px] font-sans uppercase tracking-widest text-[var(--text-muted)] mb-3">
            AI Analysis Report
          </div>
          <div className="text-xs font-sans text-[var(--text-primary)] leading-relaxed whitespace-pre-wrap">
            {report}
          </div>
        </div>
      )}

      {/* Empty state */}
      {!report && !loading && (
        <div className="flex items-center justify-center py-8 text-sm font-sans text-[var(--text-muted)]
                        border border-dashed border-[var(--border)] rounded-xl">
          Click "Generate AI Report" to get an analysis of your recent trading.
        </div>
      )}
    </div>
  )
}
