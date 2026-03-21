/**
 * AIActivityFeed — Scrollable feed of recent AI-driven audit log actions.
 * Shows timestamp, description, confidence, status, and revert button.
 * Revert button only appears on entries with status === 'applied'.
 * Data comes from props — no API calls.
 */
import React from 'react'
import clsx from 'clsx'
import type { AuditLogEntry } from '@/types/advisor'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTimestamp(ts: string): string {
  try {
    return new Date(ts).toLocaleString('en-US', {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return ts
  }
}

// ── Status badge ──────────────────────────────────────────────────────────────

const STATUS_STYLES: Record<string, string> = {
  applied:  'bg-emerald-50 text-emerald-700 border border-emerald-200',
  pending:  'bg-amber-50 text-amber-700 border border-amber-200',
  blocked:  'bg-red-50 text-red-700 border border-red-200',
  reverted: 'bg-[var(--bg-hover)] text-[var(--text-secondary)] border border-[var(--border)]',
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={clsx(
      'text-[9px] font-sans font-semibold uppercase tracking-widest px-1.5 py-0.5 rounded',
      STATUS_STYLES[status] ?? STATUS_STYLES.blocked,
    )}>
      {status}
    </span>
  )
}

// ── Confidence badge ──────────────────────────────────────────────────────────

function ConfidenceBadge({ confidence }: { confidence: number }) {
  const pct  = Math.round(confidence * 100)
  const color =
    pct >= 80 ? 'text-emerald-600' :
    pct >= 60 ? 'text-amber-600'   :
                'text-red-600'
  return (
    <span className={clsx('text-[9px] font-mono tabular-nums font-semibold', color)}>
      {pct}% conf
    </span>
  )
}

// ── Single entry card ─────────────────────────────────────────────────────────

interface EntryCardProps {
  entry:    AuditLogEntry
  onRevert: (id: number) => void
}

function EntryCard({ entry, onRevert }: EntryCardProps) {
  return (
    <div className={clsx(
      'px-3 py-2.5 rounded-xl border transition-colors',
      entry.status === 'applied'  ? 'bg-emerald-50/50 border-emerald-100' :
      entry.status === 'pending'  ? 'bg-amber-50/50 border-amber-100'     :
      entry.status === 'blocked'  ? 'bg-red-50/50 border-red-100'         :
      'bg-white border-[var(--border)]',
    )}>
      {/* Top row */}
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <div className="flex items-center gap-2 flex-wrap min-w-0">
          <StatusBadge status={entry.status} />
          <span className="text-[9px] font-sans uppercase tracking-widest text-[var(--text-muted)]">
            {entry.action_type}
          </span>
          {entry.confidence != null && (
            <ConfidenceBadge confidence={entry.confidence} />
          )}
        </div>
        <span className="text-[9px] font-mono text-[var(--text-muted)] flex-shrink-0">
          {fmtTimestamp(entry.timestamp)}
        </span>
      </div>

      {/* Description */}
      <p className="text-xs font-sans text-[var(--text-primary)] leading-relaxed">
        {entry.description}
      </p>

      {/* Reason */}
      {entry.reason && (
        <p className="text-[10px] font-sans text-[var(--text-secondary)] mt-1 leading-snug">
          {entry.reason}
        </p>
      )}

      {/* Old → New value */}
      {(entry.old_value || entry.new_value) && (
        <div className="flex items-center gap-2 mt-1.5 text-[10px] font-mono">
          {entry.old_value && (
            <span className="text-red-600">{entry.old_value}</span>
          )}
          {entry.old_value && entry.new_value && (
            <span className="text-[var(--text-muted)]">→</span>
          )}
          {entry.new_value && (
            <span className="text-emerald-600">{entry.new_value}</span>
          )}
        </div>
      )}

      {/* Revert button */}
      {entry.status === 'applied' && !entry.reverted_at && (
        <div className="flex justify-end mt-2">
          <button
            onClick={() => onRevert(entry.id)}
            className="text-[10px] font-sans text-[var(--text-secondary)] border border-[var(--border)]
                       px-2 py-0.5 rounded-md hover:bg-[var(--bg-hover)] transition-colors"
          >
            Revert
          </button>
        </div>
      )}
      {entry.reverted_at && (
        <div className="flex justify-end mt-1">
          <span className="text-[9px] font-sans text-[var(--text-muted)]">
            Reverted {fmtTimestamp(entry.reverted_at)}
          </span>
        </div>
      )}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  entries:  AuditLogEntry[]
  onRevert: (id: number) => void
}

export default function AIActivityFeed({ entries, onRevert }: Props) {
  if (!entries || entries.length === 0) {
    return (
      <div className="flex items-center justify-center py-8 text-sm font-sans text-[var(--text-muted)]">
        No AI activity recorded yet.
      </div>
    )
  }

  return (
    <div className="space-y-2 max-h-96 overflow-y-auto pr-1">
      {entries.map((entry) => (
        <EntryCard key={entry.id} entry={entry} onRevert={onRevert} />
      ))}
    </div>
  )
}
