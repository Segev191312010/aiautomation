/**
 * ShadowDecisionsTable — Paginated, filterable log of AI shadow decisions.
 * Shows what the AI would have done vs what actually ran, with outcomes.
 * Supports filter by param type, regime, and minimum confidence.
 * Data comes from props — no API calls.
 */
import React from 'react'
import clsx from 'clsx'
import type { ShadowDecision, ShadowFilters } from '@/types/advisor'

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtTimestamp(ts: string): string {
  try {
    return new Date(ts).toLocaleString('en-US', {
      month:   'short',
      day:     'numeric',
      hour:    '2-digit',
      minute:  '2-digit',
      hour12:  false,
    })
  } catch {
    return ts
  }
}

/**
 * Parse ai_suggested_value and actual_value_used (JSON strings or plain numbers)
 * and return a formatted "AI → Actual (delta)" string.
 */
function fmtAiVsActual(aiRaw: string | null | undefined, actualRaw: string | null | undefined): string {
  if (!aiRaw && !actualRaw) return '\u2014'
  if (!aiRaw) return `? \u2192 ${actualRaw}`
  if (!actualRaw) return `${aiRaw} \u2192 ?`
  function extract(raw: string): number | null {
    try {
      const parsed = JSON.parse(raw)
      if (typeof parsed === 'number') return parsed
      if (parsed !== null && typeof parsed === 'object' && 'value' in parsed) {
        return Number(parsed.value)
      }
    } catch { /* fall through */ }
    const n = Number(raw)
    return Number.isFinite(n) ? n : null
  }

  const ai     = extract(aiRaw)
  const actual = extract(actualRaw)

  if (ai === null && actual === null) return `${aiRaw} → ${actualRaw}`
  if (ai === null)   return `? → ${Number(actual).toFixed(2)}`
  if (actual === null) return `${Number(ai).toFixed(2)} → ?`

  const delta   = actual - ai
  const sign    = delta >= 0 ? '+' : ''
  const aiStr     = Number.isInteger(ai)     ? String(ai)     : Number(ai).toFixed(2)
  const actualStr = Number.isInteger(actual) ? String(actual) : Number(actual).toFixed(2)
  const deltaStr  = `${sign}${Number.isInteger(delta) ? delta : Number(delta).toFixed(2)}`

  return `${aiStr} → ${actualStr} (${deltaStr})`
}

function confidenceColor(conf: number | null | undefined): string {
  if (conf === null || conf === undefined) return 'text-[var(--text-muted)]'
  if (Number(conf) >= 0.7) return 'text-emerald-600'
  if (Number(conf) >= 0.5) return 'text-amber-600'
  return 'text-red-600'
}

// ── Type pill ─────────────────────────────────────────────────────────────────

const PARAM_TYPE_STYLES: Record<string, string> = {
  min_score:        'bg-indigo-50 text-indigo-700 border border-indigo-200',
  risk_multiplier:  'bg-amber-50 text-amber-700 border border-amber-200',
  rule_change:      'bg-purple-50 text-purple-700 border border-purple-200',
}

function TypePill({ type }: { type: string }) {
  return (
    <span className={clsx(
      'text-[9px] font-sans font-semibold uppercase tracking-widest px-1.5 py-0.5 rounded whitespace-nowrap',
      PARAM_TYPE_STYLES[type] ?? 'bg-[var(--bg-hover)] text-[var(--text-secondary)] border border-[var(--border)]',
    )}>
      {type}
    </span>
  )
}

// ── Filter row ─────────────────────────────────────────────────────────────────

const PARAM_TYPE_OPTIONS = [
  { value: '',                label: 'All Types' },
  { value: 'min_score',       label: 'min_score' },
  { value: 'risk_multiplier', label: 'risk_multiplier' },
  { value: 'rule_change',     label: 'rule_change' },
]

const REGIME_OPTIONS = [
  { value: '',         label: 'All Regimes' },
  { value: 'BULL',     label: 'BULL' },
  { value: 'BEAR',     label: 'BEAR' },
  { value: 'VOLATILE', label: 'VOLATILE' },
  { value: 'RANGE',    label: 'RANGE' },
]

interface FilterRowProps {
  filters:         ShadowFilters
  onFiltersChange: (f: Partial<ShadowFilters>) => void
}

function FilterRow({ filters, onFiltersChange }: FilterRowProps) {
  const selectClass = clsx(
    'text-xs font-sans px-2.5 py-1.5 rounded-lg border border-[var(--border)]',
    'bg-white text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)]',
    'cursor-pointer transition-colors hover:bg-[var(--bg-hover)]',
  )

  return (
    <div className="flex flex-wrap items-center gap-3">
      {/* Param type */}
      <select
        value={filters.paramType ?? ''}
        onChange={(e) => onFiltersChange({ paramType: e.target.value || undefined, page: 1 })}
        className={selectClass}
      >
        {PARAM_TYPE_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>

      {/* Regime */}
      <select
        value={filters.regime ?? ''}
        onChange={(e) => onFiltersChange({ regime: e.target.value || undefined, page: 1 })}
        className={selectClass}
      >
        {REGIME_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>

      {/* Confidence slider */}
      <div className="flex items-center gap-2">
        <label className="text-[10px] font-sans text-[var(--text-muted)] uppercase tracking-wider whitespace-nowrap">
          Min confidence
        </label>
        <input
          type="range"
          min={0}
          max={1}
          step={0.1}
          value={filters.minConfidence ?? 0}
          onChange={(e) => onFiltersChange({ minConfidence: Number(e.target.value), page: 1 })}
          className="w-24 accent-[var(--accent)] cursor-pointer"
        />
        <span className="text-xs font-mono tabular-nums text-[var(--text-secondary)] w-8">
          {((filters.minConfidence ?? 0) * 100).toFixed(0)}%
        </span>
      </div>
    </div>
  )
}

// ── Table ─────────────────────────────────────────────────────────────────────

interface TableProps {
  decisions: ShadowDecision[]
}

function DecisionsTable({ decisions }: TableProps) {
  if (decisions.length === 0) {
    return (
      <div className="flex items-center justify-center py-10 text-sm font-sans text-[var(--text-muted)]">
        No shadow decisions recorded yet.
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[680px]">
        <thead>
          <tr className="border-b border-[var(--border)]">
            {(['Timestamp', 'Type', 'Symbol', 'AI vs Actual', 'Confidence', 'Regime'] as const).map((col) => (
              <th
                key={col}
                className={clsx(
                  'py-2.5 px-3 text-[9px] font-sans uppercase tracking-widest text-[var(--text-muted)] font-medium',
                  col === 'Timestamp' || col === 'Type' || col === 'Symbol' ? 'text-left' : 'text-right',
                )}
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {decisions.map((d) => (
            <tr key={d.id} className="border-b border-[var(--border)] hover:bg-[var(--bg-hover)] transition-colors">
              {/* Timestamp */}
              <td className="py-2.5 px-3 text-left">
                <span className="text-xs font-mono tabular-nums text-[var(--text-muted)] whitespace-nowrap">
                  {fmtTimestamp(d.timestamp)}
                </span>
              </td>

              {/* Type */}
              <td className="py-2.5 px-3 text-left">
                <TypePill type={d.param_type} />
              </td>

              {/* Symbol */}
              <td className="py-2.5 px-3 text-left">
                <span className="text-xs font-mono font-medium text-[var(--text-primary)]">
                  {d.symbol ?? '—'}
                </span>
              </td>

              {/* AI vs Actual */}
              <td className="py-2.5 px-3 text-right">
                <span className="text-xs font-mono tabular-nums text-[var(--text-secondary)] whitespace-nowrap">
                  {fmtAiVsActual(d.ai_suggested_value, d.actual_value_used)}
                </span>
              </td>

              {/* Confidence */}
              <td className={clsx(
                'py-2.5 px-3 text-right text-xs font-mono tabular-nums font-medium',
                confidenceColor(d.confidence),
              )}>
                {d.confidence !== null && d.confidence !== undefined
                  ? `${(Number(d.confidence) * 100).toFixed(0)}%`
                  : '—'
                }
              </td>

              {/* Regime */}
              <td className="py-2.5 px-3 text-right">
                <span className="text-xs font-mono text-[var(--text-muted)]">
                  {d.regime ?? '—'}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Pagination ─────────────────────────────────────────────────────────────────

interface PaginationProps {
  page:     number
  pageSize: number
  total:    number
  onChange: (page: number) => void
}

function Pagination({ page, pageSize, total, onChange }: PaginationProps) {
  const start   = Math.min((page - 1) * pageSize + 1, total)
  const end     = Math.min(page * pageSize, total)
  const maxPage = Math.max(1, Math.ceil(total / pageSize))

  const btnClass = (disabled: boolean) => clsx(
    'px-3 py-1.5 text-xs font-sans rounded-lg border border-[var(--border)] transition-colors',
    disabled
      ? 'opacity-40 cursor-not-allowed text-[var(--text-muted)]'
      : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] cursor-pointer',
  )

  return (
    <div className="flex items-center justify-between gap-3 flex-wrap">
      <span className="text-[11px] font-sans text-[var(--text-muted)]">
        {total === 0 ? 'No results' : `Showing ${start}–${end} of ${total}`}
      </span>
      <div className="flex items-center gap-2">
        <button
          onClick={() => onChange(page - 1)}
          disabled={page <= 1}
          className={btnClass(page <= 1)}
        >
          Prev
        </button>
        <span className="text-xs font-mono text-[var(--text-muted)] tabular-nums">
          {page} / {maxPage}
        </span>
        <button
          onClick={() => onChange(page + 1)}
          disabled={page >= maxPage}
          className={btnClass(page >= maxPage)}
        >
          Next
        </button>
      </div>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

interface Props {
  decisions:       ShadowDecision[]
  total:           number
  filters:         ShadowFilters
  onFiltersChange: (filters: Partial<ShadowFilters>) => void
}

export default function ShadowDecisionsTable({ decisions, total, filters, onFiltersChange }: Props) {
  return (
    <div className="space-y-4">
      {/* Filters */}
      <FilterRow filters={filters} onFiltersChange={onFiltersChange} />

      {/* Table */}
      <DecisionsTable decisions={decisions} />

      {/* Pagination */}
      {total > 0 && (
        <Pagination
          page={filters.page}
          pageSize={filters.pageSize}
          total={total}
          onChange={(p) => onFiltersChange({ page: p })}
        />
      )}
    </div>
  )
}
