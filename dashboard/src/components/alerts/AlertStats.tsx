/**
 * AlertStats — alert performance dashboard panel.
 *
 * Shows:
 *   - Fired counts: today / this week / this month
 *   - Top 5 most-active symbols
 *   - Activity bar chart (last 14 days) — pure SVG, no library dep
 *
 * Data: useAlertStore().alertStats  (pre-computed in store or from backend)
 * Action: calls fetchAlertStats() on mount if stats are null.
 */
import { useEffect } from 'react'
import { useAlertStore } from '@/store'
import type { AlertStats as AlertStatsType } from '@/types'

// ── Sub-components ────────────────────────────────────────────────────────────

function StatCard({
  label,
  value,
  accent,
}: {
  label: string
  value: number
  accent: 'indigo' | 'amber' | 'green'
}) {
  const accentMap = {
    indigo: 'bg-indigo-50 text-indigo-600 border-indigo-100',
    amber:  'bg-amber-50  text-amber-600  border-amber-100',
    green:  'bg-green-50  text-green-600  border-green-100',
  }
  return (
    <div className={`flex flex-col items-center justify-center px-4 py-3 rounded-xl border ${accentMap[accent]}`}>
      <span className="text-xl font-mono font-bold tabular-nums leading-none">{value}</span>
      <span className="text-[11px] font-sans text-current/70 mt-1">{label}</span>
    </div>
  )
}

// ── Mini bar chart (SVG) ──────────────────────────────────────────────────────

interface BarChartProps {
  data:    { date: string; count: number }[]
  height?: number
}

function MiniBarChart({ data, height = 48 }: BarChartProps) {
  if (data.length === 0) return null

  const max     = Math.max(...data.map((d) => d.count), 1)
  const barW    = 100 / data.length
  const gap     = 0.8  // percentage gap between bars

  return (
    <svg
      viewBox={`0 0 100 ${height}`}
      preserveAspectRatio="none"
      className="w-full"
      style={{ height }}
      aria-label="Alert activity over the last 14 days"
      role="img"
    >
      {data.map((d, i) => {
        const barHeight = height * (d.count / max)
        const x         = i * barW + gap / 2
        const y         = height - barHeight
        const w         = barW - gap
        const isEmpty   = d.count === 0
        return (
          <rect
            key={d.date}
            x={`${x}%`}
            y={y}
            width={`${w}%`}
            height={barHeight}
            rx="1"
            className={isEmpty ? 'fill-gray-100' : 'fill-indigo-400'}
            aria-label={`${d.date}: ${d.count} alerts`}
          />
        )
      })}
    </svg>
  )
}

// ── Top symbols list ──────────────────────────────────────────────────────────

function TopSymbolsList({ symbols }: { symbols: { symbol: string; count: number }[] }) {
  if (symbols.length === 0) {
    return (
      <p className="text-[11px] font-sans text-gray-400 italic">No data yet.</p>
    )
  }

  const maxCount = symbols[0]?.count ?? 1

  return (
    <ul className="space-y-2">
      {symbols.map(({ symbol, count }) => (
        <li key={symbol} className="flex items-center gap-2">
          {/* Symbol tag */}
          <span className="w-16 text-right font-mono text-xs font-semibold text-amber-600 shrink-0">
            {symbol}
          </span>
          {/* Progress bar */}
          <div className="flex-1 h-1.5 rounded-full bg-gray-100 overflow-hidden">
            <div
              className="h-full rounded-full bg-amber-400"
              style={{ width: `${(count / maxCount) * 100}%` }}
            />
          </div>
          {/* Count */}
          <span className="text-[11px] font-mono text-gray-400 shrink-0 w-6 text-right tabular-nums">
            {count}
          </span>
        </li>
      ))}
    </ul>
  )
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function StatsSkeleton() {
  return (
    <div className="space-y-4 animate-pulse">
      <div className="grid grid-cols-3 gap-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-16 rounded-xl bg-gray-100" />
        ))}
      </div>
      <div className="h-14 rounded-xl bg-gray-100" />
      <div className="h-20 rounded-xl bg-gray-100" />
    </div>
  )
}

// ── Empty state ───────────────────────────────────────────────────────────────

function EmptyStats() {
  return (
    <div className="flex flex-col items-center justify-center py-10 gap-3">
      <div className="w-10 h-10 rounded-xl bg-gray-50 flex items-center justify-center">
        <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5 text-gray-300">
          <path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-5 14H7v-2h7v2zm3-4H7v-2h10v2zm0-4H7V7h10v2z" />
        </svg>
      </div>
      <p className="text-xs font-sans text-gray-400 text-center">
        No statistics yet.<br />Triggered alerts will appear here.
      </p>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function AlertStats() {
  const stats          = useAlertStore((s) => s.alertStats)
  const fetchAlertStats = useAlertStore((s) => s.fetchAlertStats)
  const history        = useAlertStore((s) => s.history)

  useEffect(() => {
    if (!stats) {
      void fetchAlertStats()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // If still null (never fetched) and no history, show skeleton briefly then empty
  if (!stats) {
    if (history.length === 0) {
      return (
        <div className="card rounded-2xl border border-gray-200 p-5">
          <SectionHeader title="Activity" />
          <EmptyStats />
        </div>
      )
    }
    return (
      <div className="card rounded-2xl border border-gray-200 p-5">
        <SectionHeader title="Activity" />
        <StatsSkeleton />
      </div>
    )
  }

  return <AlertStatsPanel stats={stats} />
}

function SectionHeader({ title }: { title: string }) {
  return (
    <div className="flex items-center gap-2 mb-4">
      <div className="flex items-center justify-center w-5 h-5 rounded-md bg-indigo-50 shrink-0">
        <svg viewBox="0 0 24 24" fill="currentColor" className="w-3 h-3 text-indigo-600">
          <path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-5 14H7v-2h7v2zm3-4H7v-2h10v2zm0-4H7V7h10v2z" />
        </svg>
      </div>
      <h3 className="text-xs font-sans font-semibold text-gray-700 tracking-wide uppercase">
        {title}
      </h3>
    </div>
  )
}

function AlertStatsPanel({ stats }: { stats: AlertStatsType }) {
  // Date formatter: show Mon DD
  function fmtDate(iso: string): string {
    const d = new Date(iso + 'T00:00:00')
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  }

  const hasActivity = stats.total_month > 0 || stats.daily_counts.some((d) => d.count > 0)

  return (
    <div className="card rounded-2xl border border-gray-200 p-5 space-y-5">
      <SectionHeader title="Alert Activity" />

      {/* ── Summary KPIs ─────────────────────────────────────────────── */}
      <div className="grid grid-cols-3 gap-3">
        <StatCard label="Today"      value={stats.total_today} accent="indigo" />
        <StatCard label="This week"  value={stats.total_week}  accent="amber"  />
        <StatCard label="This month" value={stats.total_month} accent="green"  />
      </div>

      {!hasActivity ? (
        <EmptyStats />
      ) : (
        <>
          {/* ── Activity bar chart ──────────────────────────────────── */}
          {stats.daily_counts.length > 0 && (
            <div>
              <p className="text-[10px] font-sans font-semibold text-gray-400 uppercase tracking-wider mb-2">
                Last 14 days
              </p>
              <MiniBarChart data={stats.daily_counts} height={44} />
              {/* X-axis labels: first and last date only */}
              {stats.daily_counts.length >= 2 && (
                <div className="flex justify-between mt-1">
                  <span className="text-[10px] font-mono text-gray-400">
                    {fmtDate(stats.daily_counts[0]!.date)}
                  </span>
                  <span className="text-[10px] font-mono text-gray-400">
                    {fmtDate(stats.daily_counts[stats.daily_counts.length - 1]!.date)}
                  </span>
                </div>
              )}
            </div>
          )}

          {/* ── Top symbols ─────────────────────────────────────────── */}
          {stats.top_symbols.length > 0 && (
            <div>
              <p className="text-[10px] font-sans font-semibold text-gray-400 uppercase tracking-wider mb-2.5">
                Most active symbols
              </p>
              <TopSymbolsList symbols={stats.top_symbols} />
            </div>
          )}
        </>
      )}
    </div>
  )
}
