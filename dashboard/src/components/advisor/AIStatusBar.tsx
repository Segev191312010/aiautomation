/**
 * AIStatusBar — Compact horizontal bar showing AI system status.
 * Shows: Autonomy toggle, Mode (Shadow/Live), Changes today,
 * Next optimization timestamp, Emergency stop indicator.
 * Data comes from props — no API calls.
 */
import React from 'react'
import clsx from 'clsx'
import type { AIStatus } from '@/types/advisor'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTimestamp(ts: string | null | undefined): string {
  if (!ts) return '--'
  try {
    return new Date(ts).toLocaleString('en-US', {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return ts
  }
}

// ── Status item ───────────────────────────────────────────────────────────────

function StatusItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[9px] font-sans uppercase tracking-widest text-[var(--text-muted)]">
        {label}
      </span>
      {children}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  status: AIStatus | null
}

export default function AIStatusBar({ status }: Props) {
  if (!status) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 rounded-xl border border-[var(--border)] bg-white">
        <div className="h-2 w-2 rounded-full bg-gray-300 animate-pulse" />
        <span className="text-xs font-sans text-[var(--text-muted)]">Connecting to AI status...</span>
      </div>
    )
  }

  return (
    <div className={clsx(
      'flex items-center gap-4 px-4 py-3 rounded-xl border flex-wrap',
      status.emergency_stop
        ? 'bg-red-50 border-red-300'
        : 'bg-white border-[var(--border)]',
    )}>
      {/* Emergency stop indicator */}
      {status.emergency_stop && (
        <div className="flex items-center gap-1.5 mr-2">
          <span className="relative flex h-2.5 w-2.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-red-500" />
          </span>
          <span className="text-xs font-sans font-bold text-red-700 uppercase tracking-wide">
            STOP
          </span>
        </div>
      )}

      {/* Autonomy */}
      <StatusItem label="Autonomy">
        <span className={clsx(
          'text-[10px] font-sans font-bold px-1.5 py-0.5 rounded',
          status.autonomy_active
            ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
            : 'bg-gray-100 text-gray-600 border border-gray-200',
        )}>
          {status.autonomy_active ? 'ON' : 'OFF'}
        </span>
      </StatusItem>

      <span className="w-px h-6 bg-[var(--border)] self-center" />

      {/* Mode */}
      <StatusItem label="Mode">
        <span className={clsx(
          'text-[10px] font-sans font-bold px-1.5 py-0.5 rounded',
          status.shadow_mode
            ? 'bg-amber-50 text-amber-700 border border-amber-200'
            : 'bg-blue-50 text-blue-700 border border-blue-200',
        )}>
          {status.shadow_mode ? 'Shadow' : 'Live'}
        </span>
      </StatusItem>

      <span className="w-px h-6 bg-[var(--border)] self-center" />

      {/* Changes today */}
      <StatusItem label="Changes Today">
        <span className="text-xs font-mono font-semibold text-[var(--text-primary)] tabular-nums">
          {status.changes_today}
          <span className={`font-normal ${status.daily_budget_remaining === 0 ? 'text-red-600' : 'text-[var(--text-muted)]'}`}>
            /{status.changes_today + status.daily_budget_remaining}
          </span>
        </span>
      </StatusItem>

      <span className="w-px h-6 bg-[var(--border)] self-center" />

      {/* Optimizer running indicator */}
      <StatusItem label="Optimizer">
        <div className="flex items-center gap-1">
          {status.optimizer_running ? (
            <>
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
              </span>
              <span className="text-[10px] font-sans text-emerald-600">Running</span>
            </>
          ) : (
            <>
              <span className="h-2 w-2 rounded-full bg-gray-300" />
              <span className="text-[10px] font-sans text-[var(--text-muted)]">Idle</span>
            </>
          )}
        </div>
      </StatusItem>

      <span className="w-px h-6 bg-[var(--border)] self-center" />

      {/* Next optimization */}
      <StatusItem label="Next Optimization">
        <span className="text-[10px] font-mono text-[var(--text-secondary)]">
          {fmtTimestamp(status.next_optimization_at)}
        </span>
      </StatusItem>
    </div>
  )
}
