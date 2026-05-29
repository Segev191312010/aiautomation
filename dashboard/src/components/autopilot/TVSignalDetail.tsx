/**
 * TVSignalDetail — read-only inspector for a single inbound signal, including
 * the raw webhook/scanner payload as received.
 */
import type { ReactNode } from 'react'
import type { SignalRow, SignalStatus } from '@/types/signals'
import { fmtTimestamp } from '@/utils/formatters'

interface Props {
  signal: SignalRow | null
}

const STATUS_TONE: Record<SignalStatus, string> = {
  pending_review: 'bg-amber-500/10 text-amber-400',
  in_review: 'bg-indigo-500/10 text-indigo-400',
  applied: 'bg-emerald-500/10 text-emerald-400',
  declined_by_ai: 'bg-zinc-700/40 text-zinc-300',
  failed: 'bg-red-500/10 text-red-400',
  expired: 'bg-zinc-800 text-zinc-500',
  queued: 'bg-sky-500/10 text-sky-400',
  draining: 'bg-amber-500/10 text-amber-400',
}

function MetaRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <span className="text-[11px] uppercase tracking-[0.12em] text-[var(--text-muted)]">{label}</span>
      <span className="text-sm font-medium text-[var(--text-primary)]">{value}</span>
    </div>
  )
}

export default function TVSignalDetail({ signal }: Props) {
  if (!signal) {
    return (
      <div className="flex items-center justify-center rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] px-5 py-12 text-sm text-[var(--text-muted)]">
        Select a signal to inspect its payload.
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div className="text-lg font-semibold text-[var(--text-primary)]">{signal.symbol}</div>
        <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${STATUS_TONE[signal.status] ?? 'bg-zinc-800 text-zinc-400'}`}>
          {signal.status}
        </span>
      </div>

      <div className="rounded-[20px] border border-[var(--border)] bg-[var(--bg-hover)] px-4 py-2">
        <MetaRow label="Action" value={signal.action} />
        <MetaRow label="Source" value={signal.source} />
        <MetaRow label="Queued" value={fmtTimestamp(signal.queued_at)} />
        <MetaRow label="Signal ID" value={<span className="font-mono text-xs text-[var(--text-secondary)]">{signal.id}</span>} />
      </div>

      <div>
        <div className="mb-2 text-[11px] uppercase tracking-[0.12em] text-[var(--text-muted)]">Raw payload</div>
        <pre className="max-h-72 overflow-auto rounded-[20px] border border-[var(--border)] bg-[var(--bg-card)] p-4 text-xs font-mono leading-5 text-[var(--text-secondary)]">
          {JSON.stringify(signal.payload, null, 2)}
        </pre>
      </div>
    </div>
  )
}
