import { useState, useMemo } from 'react'
import clsx from 'clsx'
import type { StockFinancialStatements, FinancialTable } from '@/types'
import FreshnessTag from './FreshnessTag'

// ── Tab definitions ──────────────────────────────────────────────────────────

const TABS = [
  { key: 'income_statement', label: 'Income Statement' },
  { key: 'balance_sheet',    label: 'Balance Sheet' },
  { key: 'cash_flow',        label: 'Cash Flow' },
] as const

type TabKey = typeof TABS[number]['key']
type PeriodMode = 'quarterly' | 'annual'

// ── Number formatting ────────────────────────────────────────────────────────

function fmtCompact(v: number | null, isPercent = false): string {
  if (v == null) return '—'
  if (isPercent) return `${v.toFixed(1)}%`
  const abs = Math.abs(v)
  const sign = v < 0 ? '-' : ''
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(2)}T`
  if (abs >= 1e9)  return `${sign}$${(abs / 1e9).toFixed(2)}B`
  if (abs >= 1e6)  return `${sign}$${(abs / 1e6).toFixed(2)}M`
  if (abs >= 1e3)  return `${sign}$${(abs / 1e3).toFixed(0)}K`
  return `${sign}$${abs.toFixed(2)}`
}

// ── Period header formatting ─────────────────────────────────────────────────

function formatPeriod(dateStr: string, mode: PeriodMode): string {
  try {
    const d = new Date(dateStr)
    if (isNaN(d.getTime())) return dateStr.slice(0, 7)
    const year = d.getFullYear()
    if (mode === 'annual') return String(year)
    // Determine fiscal quarter from month
    const month = d.getMonth() + 1 // 1–12
    const q = month <= 3 ? 1 : month <= 6 ? 2 : month <= 9 ? 3 : 4
    return `Q${q}-${year}`
  } catch {
    return dateStr.slice(0, 7)
  }
}

// ── Fuzzy label search ───────────────────────────────────────────────────────

function findRow(
  items: FinancialTable['items'],
  ...keywords: string[]
): { label: string; values: (number | null)[] } | null {
  for (const kw of keywords) {
    const lower = kw.toLowerCase()
    const found = items.find((item) =>
      item.label.toLowerCase().includes(lower)
    )
    if (found) return found
  }
  return null
}

// ── Curated row definitions per tab ─────────────────────────────────────────

interface CuratedRow {
  label: string
  keywords: string[]   // first match wins
  isPercent?: boolean
  computed?: true      // row is derived, not searched
}

const CURATED: Record<TabKey, CuratedRow[]> = {
  income_statement: [
    { label: 'Revenue',           keywords: ['total revenue', 'revenue'] },
    { label: 'Operating Expense', keywords: ['operating expense', 'total operating expense'] },
    { label: 'Net Income',        keywords: ['net income', 'net earnings'] },
    { label: 'Net Profit Margin', keywords: [],   isPercent: true, computed: true },
    { label: 'EPS',               keywords: ['basic eps', 'diluted eps', 'eps'] },
    { label: 'EBITDA',            keywords: ['ebitda'] },
  ],
  balance_sheet: [
    { label: 'Cash & Short-term', keywords: ['cash and cash equivalents', 'cash equivalents', 'cash short term', 'cash'] },
    { label: 'Total Assets',      keywords: ['total assets'] },
    { label: 'Total Liabilities', keywords: ['total liabilities net', 'total liabilities'] },
    { label: 'Total Equity',      keywords: ['stockholders equity', 'common stock equity', 'total equity'] },
  ],
  cash_flow: [
    { label: 'Net Income',           keywords: ['net income', 'net earnings'] },
    { label: 'Cash From Operations', keywords: ['operating cash flow', 'cash from operating'] },
    { label: 'Cash From Investing',  keywords: ['investing cash flow', 'cash from investing'] },
    { label: 'Cash From Financing',  keywords: ['financing cash flow', 'cash from financing'] },
    { label: 'Free Cash Flow',       keywords: ['free cash flow'] },
  ],
}

// ── Resolve curated rows from raw FinancialTable ─────────────────────────────

interface ResolvedRow {
  label: string
  values: (number | null)[]
  isPercent: boolean
}

function resolveCuratedRows(
  tableData: FinancialTable,
  tabKey: TabKey,
  maxPeriods: number,
): { periods: string[]; rows: ResolvedRow[] } {
  const periods = tableData.periods.slice(0, maxPeriods)
  const items   = tableData.items

  const rows: ResolvedRow[] = []

  // Pre-find revenue and net income for margin calculation
  const revenueRow  = findRow(items, 'total revenue', 'revenue')
  const netIncomeRow = findRow(items, 'net income', 'net earnings')

  for (const def of CURATED[tabKey]) {
    let values: (number | null)[]

    if (def.computed && def.label === 'Net Profit Margin') {
      // Derive: net income / revenue * 100
      values = periods.map((_, i) => {
        const rev = revenueRow?.values[i] ?? null
        const ni  = netIncomeRow?.values[i] ?? null
        if (rev == null || ni == null || rev === 0) return null
        return (ni / rev) * 100
      })
    } else {
      const found = findRow(items, ...def.keywords)
      values = found
        ? periods.map((_, i) => found.values[i] ?? null)
        : periods.map(() => null)
    }

    rows.push({
      label:     def.label,
      values,
      isPercent: !!def.isPercent,
    })
  }

  return { periods, rows }
}

// ── Direction arrow per period index (vs previous period) ────────────────────

function getDir(
  values: (number | null)[],
  idx: number,
): 'up' | 'down' | 'flat' | null {
  if (idx >= values.length - 1) return null // no previous period
  const curr = values[idx]
  const prev = values[idx + 1]
  if (curr == null || prev == null) return null
  if (curr > prev) return 'up'
  if (curr < prev) return 'down'
  return 'flat'
}

// ── Statement table component ────────────────────────────────────────────────

interface TableProps {
  periods: string[]
  rows: ResolvedRow[]
  periodMode: PeriodMode
}

function StatementTable({ periods, rows, periodMode }: TableProps) {
  if (!periods.length || !rows.length) {
    return (
      <p className="text-[11px] text-terminal-ghost py-4 text-center">
        No data available
      </p>
    )
  }

  // Check if every row has all-null values (nothing resolved)
  const hasAnyData = rows.some((r) => r.values.some((v) => v != null))
  if (!hasAnyData) {
    return (
      <p className="text-[11px] text-terminal-ghost py-4 text-center">
        Data not available for this statement
      </p>
    )
  }

  return (
    <div className="overflow-x-auto -mx-1">
      <table className="w-full text-[11px] min-w-[480px]">
        <thead>
          <tr className="border-b border-white/[0.08]">
            <th className="text-left font-sans font-medium text-terminal-ghost py-2.5 pr-4 min-w-[160px]">
              {/* row label column — no header text */}
            </th>
            {periods.map((p) => (
              <th
                key={p}
                className="text-right font-sans font-medium text-terminal-ghost py-2.5 px-3 min-w-[96px] whitespace-nowrap"
              >
                {formatPeriod(p, periodMode)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.label}
              className="border-b border-white/[0.04] hover:bg-white/[0.025] transition-colors"
            >
              <td className="font-sans text-terminal-dim py-2 pr-4 whitespace-nowrap">
                {row.label}
              </td>
              {row.values.map((v, colIdx) => {
                const dir = getDir(row.values, colIdx)
                return (
                  <td
                    key={colIdx}
                    className="text-right font-mono tabular-nums text-terminal-text py-2 px-3 whitespace-nowrap"
                  >
                    <span className="inline-flex items-center justify-end gap-1">
                      {fmtCompact(v, row.isPercent)}
                      {dir === 'up' && (
                        <span className="text-terminal-green text-[9px] leading-none">▲</span>
                      )}
                      {dir === 'down' && (
                        <span className="text-terminal-red text-[9px] leading-none">▼</span>
                      )}
                    </span>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Skeleton ─────────────────────────────────────────────────────────────────

function LoadingSkeleton() {
  return (
    <section className="glass rounded-2xl shadow-glass p-6 animate-pulse">
      <div className="flex items-center justify-between mb-5">
        <div className="h-3 w-44 bg-terminal-muted rounded-lg" />
        <div className="h-3 w-12 bg-terminal-muted rounded-lg" />
      </div>
      <div className="flex items-center justify-between mb-5">
        <div className="flex gap-1.5">
          {[140, 112, 96].map((w) => (
            <div key={w} className="h-7 bg-terminal-muted rounded-lg" style={{ width: w }} />
          ))}
        </div>
        <div className="h-7 w-28 bg-terminal-muted rounded-lg" />
      </div>
      <div className="space-y-2.5">
        {[1, 2, 3, 4, 5, 6].map((i) => (
          <div key={i} className="h-8 bg-terminal-muted rounded-lg" />
        ))}
      </div>
    </section>
  )
}

// ── Main component ───────────────────────────────────────────────────────────

interface Props {
  data: StockFinancialStatements | null
  loading: boolean
}

const MAX_PERIODS = 5

export default function FinancialStatementsModule({ data, loading }: Props) {
  const [activeTab, setActiveTab]   = useState<TabKey>('income_statement')
  const [periodMode, setPeriodMode] = useState<PeriodMode>('quarterly')

  const resolved = useMemo(() => {
    if (!data) return null
    const tableData = data[activeTab][periodMode]
    return resolveCuratedRows(tableData, activeTab, MAX_PERIODS)
  }, [data, activeTab, periodMode])

  if (!data && loading) return <LoadingSkeleton />
  if (!data) return null

  return (
    <section id="section-financials" className="glass rounded-2xl shadow-glass p-6">

      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <h3 className="text-xs font-sans font-semibold text-terminal-dim tracking-wide uppercase">
          Financial Statements
        </h3>
        <FreshnessTag fetchedAt={data.fetched_at} />
      </div>

      {/* Controls row: tabs left, period toggle right */}
      <div className="flex items-center justify-between flex-wrap gap-2 mb-5">

        {/* Statement tabs */}
        <div className="flex gap-1">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={clsx(
                'text-[10px] font-sans px-3 py-1.5 rounded-lg transition-all duration-150',
                activeTab === tab.key
                  ? 'bg-indigo-500/20 text-indigo-400 border border-indigo-500/40'
                  : 'text-terminal-ghost hover:text-terminal-dim border border-transparent hover:border-white/[0.08]',
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Quarterly / Annual pill toggle */}
        <div className="flex gap-0.5 bg-terminal-muted rounded-lg p-0.5">
          {(['quarterly', 'annual'] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => setPeriodMode(mode)}
              className={clsx(
                'text-[10px] font-sans capitalize px-3 py-1 rounded-md transition-all duration-150',
                periodMode === mode
                  ? 'bg-indigo-500/25 text-indigo-400'
                  : 'text-terminal-ghost hover:text-terminal-dim',
              )}
            >
              {mode.charAt(0).toUpperCase() + mode.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      {resolved && (
        <StatementTable
          periods={resolved.periods}
          rows={resolved.rows}
          periodMode={periodMode}
        />
      )}
    </section>
  )
}
