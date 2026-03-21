/**
 * AutoTunePanel — Preview and apply AI-generated parameter tuning.
 * Shows proposed changes and warnings before applying.
 * Requires confirmation before applying changes.
 * Data comes from props — no API calls within this component.
 */
import React, { useState } from 'react'
import clsx from 'clsx'
import type { AutoTuneResult } from '@/types/advisor'

// ── Icons ─────────────────────────────────────────────────────────────────────

function IconCheck() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}
      strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  )
}

function IconTriangle() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
      <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  )
}

function Spinner() {
  return (
    <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  )
}

// ── Confirmation dialog ───────────────────────────────────────────────────────

interface ConfirmDialogProps {
  changeCount:  number
  onConfirm:    () => void
  onCancel:     () => void
}

function ConfirmDialog({ changeCount, onConfirm, onCancel }: ConfirmDialogProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm">
      <div className="bg-white border border-[var(--border)] rounded-2xl shadow-card-lg p-6 max-w-sm w-full mx-4">
        <h3 className="text-sm font-sans font-semibold text-[var(--text-primary)] mb-2">
          Apply AI Tune Changes?
        </h3>
        <p className="text-xs font-sans text-[var(--text-secondary)] mb-4 leading-relaxed">
          This will modify <strong>{changeCount} rule{changeCount !== 1 ? 's' : ''}</strong> based on AI recommendations.
          Live trading behavior will change immediately.
        </p>
        <div className="flex gap-2 justify-end">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-xs font-sans text-[var(--text-secondary)] border border-[var(--border)]
                       rounded-lg hover:bg-[var(--bg-hover)] transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={false}
            className="px-3 py-1.5 text-xs font-sans font-medium text-white bg-red-600
                       rounded-lg hover:bg-red-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Apply Changes
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  preview:   AutoTuneResult | null
  onPreview: () => void
  onApply:   () => void
  loading:   boolean
}

export default function AutoTunePanel({ preview, onPreview, onApply, loading }: Props) {
  const [confirming, setConfirming] = useState(false)

  function handleApplyClick() {
    setConfirming(true)
  }

  function handleConfirm() {
    if (loading) return  // prevent double-click
    setConfirming(false)
    onApply()
  }

  const totalChanges = preview
    ? preview.changes.length + preview.rules_to_disable.length
    : 0

  return (
    <>
      {confirming && (
        <ConfirmDialog
          changeCount={totalChanges}
          onConfirm={handleConfirm}
          onCancel={() => setConfirming(false)}
        />
      )}

      <div className="space-y-4">
        {/* Header row */}
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <p className="text-xs font-sans text-[var(--text-secondary)] leading-relaxed">
              AI analyzes your rule performance and suggests parameter adjustments.
              Preview first, then apply when satisfied.
            </p>
          </div>
          <button
            onClick={onPreview}
            disabled={loading}
            className={clsx(
              'flex items-center gap-2 px-3.5 py-2 text-xs font-sans font-medium rounded-lg transition-colors',
              'bg-[var(--accent)] text-white hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed',
            )}
          >
            {loading && <Spinner />}
            {loading ? 'Analyzing...' : 'Preview Changes'}
          </button>
        </div>

        {/* Preview results */}
        {preview && !loading && (
          <div className="space-y-3">
            {/* Changes list */}
            {(preview.changes.length > 0 || preview.rules_to_disable.length > 0) && (
              <div className="space-y-1.5">
                <h4 className="text-[10px] font-sans uppercase tracking-widest text-[var(--text-muted)]">
                  Proposed Changes
                </h4>
                {preview.changes.map((change, i) => (
                  <div key={i} className="flex items-start gap-2 px-3 py-2 rounded-lg bg-emerald-50 border border-emerald-100">
                    <span className="text-emerald-600 mt-0.5 flex-shrink-0"><IconCheck /></span>
                    <span className="text-xs font-sans text-[var(--text-primary)]">{change}</span>
                  </div>
                ))}
                {preview.rules_to_disable.map((ruleId, i) => (
                  <div key={`disable-${i}`} className="flex items-start gap-2 px-3 py-2 rounded-lg bg-red-50 border border-red-100">
                    <span className="text-red-600 mt-0.5 flex-shrink-0"><IconCheck /></span>
                    <span className="text-xs font-sans text-[var(--text-primary)]">
                      Disable rule: <span className="font-mono font-medium">{ruleId}</span>
                    </span>
                  </div>
                ))}
              </div>
            )}

            {/* Warnings */}
            {preview.warnings.length > 0 && (
              <div className="space-y-1.5">
                <h4 className="text-[10px] font-sans uppercase tracking-widest text-[var(--text-muted)]">
                  Warnings
                </h4>
                {preview.warnings.map((warning, i) => (
                  <div key={i} className="flex items-start gap-2 px-3 py-2 rounded-lg bg-amber-50 border border-amber-100">
                    <span className="text-amber-600 mt-0.5 flex-shrink-0"><IconTriangle /></span>
                    <span className="text-xs font-sans text-[var(--text-primary)]">{warning}</span>
                  </div>
                ))}
              </div>
            )}

            {/* No changes */}
            {totalChanges === 0 && preview.warnings.length === 0 && (
              <div className="flex items-center justify-center py-4 text-sm font-sans text-[var(--text-muted)]">
                No changes recommended — system is already well-optimized.
              </div>
            )}

            {/* Apply button */}
            {totalChanges > 0 && (
              <button
                onClick={handleApplyClick}
                className={clsx(
                  'w-full py-2.5 text-sm font-sans font-semibold rounded-xl transition-colors',
                  'bg-red-600 text-white hover:bg-red-700',
                  preview.applied && 'opacity-50 cursor-not-allowed',
                )}
                disabled={preview.applied}
              >
                {preview.applied ? 'Changes Applied' : `Apply ${totalChanges} Change${totalChanges !== 1 ? 's' : ''}`}
              </button>
            )}
          </div>
        )}
      </div>
    </>
  )
}
