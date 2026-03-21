/**
 * RulePerformanceTable — Sortable table of per-rule trading performance.
 * Columns: Rule Name, Trades, Win%, PF, P&L, Avg Hold, Verdict.
 * Clicking a row triggers onRuleClick with the rule_id.
 * Data comes from props — no API calls.
 */
import React, { useState, useMemo } from 'react'
import clsx from 'clsx'
import type { RulePerformance, RuleVerdict } from '@/types/advisor'

// ── Verdict badge ─────────────────────────────────────────────────────────────

const VERDICT_STYLES: Record<RuleVerdict, string> = {
  disable: 'bg-red-50 text-red-700 border border-red-200',
  boost:   'bg-emerald-50 text-emerald-700 border border-emerald-200',
  hold:    'bg-[var(--bg-hover)] text-[var(--text-secondary)] border border-[var(--border)]',
  reduce:  'bg-amber-50 text-amber-700 border border-amber-200',
  watch:   'bg-blue-50 text-blue-700 border border-blue-200',
}

function VerdictBadge({ verdict }: { verdict: RuleVerdict }) {
  return (
    <span className={clsx(
      'text-[9px] font-sans font-semibold uppercase tracking-widest px-1.5 py-0.5 rounded',
      VERDICT_STYLES[verdict],
    )}>
      {verdict}
    </span>
  )
}

// ── Sort controls ─────────────────────────────────────────────────────────────

type SortKey = 'rule_name' | 'total_trades' | 'win_rate' | 'profit_factor' | 'total_pnl' | 'avg_hold_hours'
type SortDir = 'asc' | 'desc'

interface SortState {
  key: SortKey
  dir: SortDir
}

interface ColDef {
  key:   SortKey
  label: string
  align: 'left' | 'right'
}

const COLUMNS: ColDef[] = [
  { key: 'rule_name',    label: 'Rule Name',  align: 'left'  },
  { key: 'total_trades', label: 'Trades',     align: 'right' },
  { key: 'win_rate',     label: 'Win%',       align: 'right' },
  { key: 'profit_factor',label: 'PF',         align: 'right' },
  { key: 'total_pnl',   label: 'P&L',        align: 'right' },
  { key: 'avg_hold_hours',label: 'Avg Hold',  align: 'right' },
]

function SortIcon({ active, dir }: { active: boolean; dir: SortDir }) {
  return (
    <span className={clsx('ml-1 text-[10px]', active ? 'text-[var(--accent)]' : 'text-[var(--text-muted)]')}>
      {active ? (dir === 'asc' ? '▲' : '▼') : '⇅'}
    </span>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  rules:        RulePerformance[]
  onRuleClick?: (ruleId: string) => void
}

export default function RulePerformanceTable({ rules, onRuleClick }: Props) {
  const [sort, setSort] = useState<SortState>({ key: 'total_pnl', dir: 'desc' })

  const sorted = useMemo(() => {
    if (!rules || rules.length === 0) return []
    return [...rules].sort((a, b) => {
      const av = a[sort.key]
      const bv = b[sort.key]
      if (typeof av === 'string' && typeof bv === 'string') {
        return sort.dir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av)
      }
      const an = Number(av) || 0
      const bn = Number(bv) || 0
      return sort.dir === 'asc' ? an - bn : bn - an
    })
  }, [rules, sort])

  function handleSort(key: SortKey) {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: 'desc' },
    )
  }

  if (!rules || rules.length === 0) {
    return (
      <div className="flex items-center justify-center py-8 text-sm font-sans text-[var(--text-muted)]">
        No rule performance data available.
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[680px]">
        <thead>
          <tr className="border-b border-[var(--border)]">
            {COLUMNS.map((col) => (
              <th
                key={col.key}
                className={clsx(
                  'py-2.5 px-3 text-[9px] font-sans uppercase tracking-widest',
                  'text-[var(--text-muted)] font-medium cursor-pointer select-none',
                  'hover:text-[var(--text-secondary)] transition-colors',
                  col.align === 'right' ? 'text-right' : 'text-left',
                )}
                onClick={() => handleSort(col.key)}
              >
                {col.label}
                <SortIcon active={sort.key === col.key} dir={sort.dir} />
              </th>
            ))}
            <th className="py-2.5 px-3 text-[9px] font-sans uppercase tracking-widest text-[var(--text-muted)] font-medium text-right">
              Verdict
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((rule) => (
            <tr
              key={rule.rule_id}
              onClick={() => onRuleClick?.(rule.rule_id)}
              className={clsx(
                'border-b border-[var(--border)] transition-colors',
                onRuleClick ? 'cursor-pointer hover:bg-[var(--bg-hover)]' : 'cursor-default',
              )}
            >
              {/* Rule Name */}
              <td className="py-2.5 px-3 text-left">
                <span className="text-xs font-sans text-[var(--text-primary)] font-medium">
                  {rule.rule_name}
                </span>
              </td>

              {/* Trades */}
              <td className="py-2.5 px-3 text-right text-xs font-mono tabular-nums text-[var(--text-secondary)]">
                {rule.total_trades}
              </td>

              {/* Win% */}
              <td className={clsx(
                'py-2.5 px-3 text-right text-xs font-mono tabular-nums font-medium',
                Number(rule.win_rate) >= 50 ? 'text-emerald-600' : 'text-red-600',
              )}>
                {(Number(rule.win_rate) || 0).toFixed(1)}%
              </td>

              {/* PF */}
              <td className={clsx(
                'py-2.5 px-3 text-right text-xs font-mono tabular-nums',
                Number(rule.profit_factor) >= 1 ? 'text-emerald-600' : 'text-red-600',
              )}>
                {Number.isFinite(Number(rule.profit_factor)) ? (Number(rule.profit_factor) || 0).toFixed(2) : '\u221e'}
              </td>

              {/* P&L */}
              <td className={clsx(
                'py-2.5 px-3 text-right text-xs font-mono tabular-nums font-medium',
                Number(rule.total_pnl) >= 0 ? 'text-emerald-600' : 'text-red-600',
              )}>
                {Number(rule.total_pnl) >= 0 ? '+' : ''}${(Number(rule.total_pnl) || 0).toFixed(0)}
              </td>

              {/* Avg Hold */}
              <td className="py-2.5 px-3 text-right text-xs font-mono tabular-nums text-[var(--text-secondary)]">
                {(Number(rule.avg_hold_hours) || 0).toFixed(1)}h
              </td>

              {/* Verdict */}
              <td className="py-2.5 px-3 text-right">
                <VerdictBadge verdict={rule.verdict} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
