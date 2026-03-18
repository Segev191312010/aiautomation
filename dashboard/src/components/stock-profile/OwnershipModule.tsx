import { useState } from 'react'
import type { StockOwnership } from '@/types'
import FreshnessTag from './FreshnessTag'

type Holder = NonNullable<StockOwnership['top_holders']>[number]

function formatPct(value: number | null | undefined): string {
  if (value == null) return '--'
  return `${(value * 100).toFixed(1)}%`
}

function formatShares(shares: number): string {
  if (shares >= 1e9) return `${(shares / 1e9).toFixed(2)}B`
  if (shares >= 1e6) return `${(shares / 1e6).toFixed(2)}M`
  if (shares >= 1e3) return `${(shares / 1e3).toFixed(1)}K`
  return shares.toLocaleString('en-US')
}

function formatValue(value: number | null | undefined): string | null {
  if (value == null) return null
  if (value >= 1e12) return `$${(value / 1e12).toFixed(2)}T`
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`
  if (value >= 1e6) return `$${(value / 1e6).toFixed(1)}M`
  return `$${Math.round(value).toLocaleString('en-US')}`
}

function StatCard({ label, value, sublabel }: { label: string; value: string; sublabel?: string }) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-3">
      <div className="text-[10px] font-sans uppercase tracking-wide text-zinc-400">{label}</div>
      <div className="mt-1 text-2xl font-mono font-bold text-zinc-50 tabular-nums">{value}</div>
      {sublabel && <div className="mt-1 text-[10px] font-sans text-zinc-400">{sublabel}</div>}
    </div>
  )
}

function HolderRow({ holder }: { holder: Holder }) {
  const value = formatValue(holder.value)
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-3">
      <div className="min-w-0">
        <div className="truncate text-[12px] font-sans font-semibold text-zinc-50" title={holder.name}>
          {holder.name}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] font-mono text-zinc-400">
          <span>{formatShares(holder.shares)} shares</span>
          {value && <span>{value}</span>}
          {holder.date_reported && <span>reported {holder.date_reported}</span>}
        </div>
      </div>
      <div className="self-center rounded-full bg-indigo-50 px-2.5 py-1 text-[11px] font-mono font-semibold text-indigo-700">
        {formatPct(holder.pct)}
      </div>
    </div>
  )
}

function OwnershipBar({
  institutionalPct,
  insiderPct,
}: {
  institutionalPct: number | null
  insiderPct: number | null
}) {
  const institutional = institutionalPct != null ? Math.max(0, Math.min(100, institutionalPct * 100)) : 0
  const insider = insiderPct != null ? Math.max(0, Math.min(100 - institutional, insiderPct * 100)) : 0
  const publicFloat = Math.max(0, 100 - institutional - insider)

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-4">
      <div className="text-[10px] font-sans uppercase tracking-wide text-zinc-400">Share Mix</div>
      <div className="mt-3 flex h-3 overflow-hidden rounded-full bg-zinc-900">
        {institutional > 0 && <div className="h-full bg-indigo-600" style={{ width: `${institutional}%` }} />}
        {insider > 0 && <div className="h-full bg-amber-500" style={{ width: `${insider}%` }} />}
        {publicFloat > 0 && <div className="h-full bg-zinc-800" style={{ width: `${publicFloat}%` }} />}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-[10px] font-sans text-zinc-400">
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm bg-indigo-600" />
          Institutional
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm bg-amber-500" />
          Insider
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm bg-zinc-700" />
          Public / Other
        </span>
      </div>
    </div>
  )
}

function HolderSection({
  title,
  rows,
  expanded,
  onToggle,
}: {
  title: string
  rows: Holder[]
  expanded: boolean
  onToggle: () => void
}) {
  const previewCount = 5
  const visibleRows = expanded ? rows : rows.slice(0, previewCount)
  const canToggle = rows.length > previewCount

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-400">{title}</div>
        {canToggle && (
          <button
            type="button"
            onClick={onToggle}
            className="text-[10px] font-sans font-medium text-indigo-700 transition-colors hover:text-indigo-600"
          >
            {expanded ? 'Show less' : `View all (${rows.length})`}
          </button>
        )}
      </div>

      <div className="space-y-2">
        {visibleRows.map((holder, index) => (
          <HolderRow key={`${holder.name}-${index}`} holder={holder} />
        ))}
      </div>
    </div>
  )
}

interface Props {
  data: StockOwnership | null
  loading: boolean
}

export default function OwnershipModule({ data, loading }: Props) {
  const [showAllInstitutions, setShowAllInstitutions] = useState(false)
  const [showAllFunds, setShowAllFunds] = useState(false)

  if (!data && loading) {
    return (
      <section className="card rounded-lg  p-6 animate-pulse">
        <div className="mb-5 h-3 w-28 rounded-lg bg-zinc-800" />
        <div className="grid gap-3 md:grid-cols-3">
          {Array.from({ length: 3 }, (_, index) => (
            <div key={index} className="h-24 rounded-lg bg-zinc-800" />
          ))}
        </div>
        <div className="mt-4 h-24 rounded-lg bg-zinc-800" />
        <div className="mt-4 space-y-2">
          {Array.from({ length: 4 }, (_, index) => (
            <div key={index} className="h-16 rounded-lg bg-zinc-800" />
          ))}
        </div>
      </section>
    )
  }

  if (!data) return null

  const topHolders = data.top_holders ?? []
  const fundHolders = data.mutual_fund_holders ?? []
  const hasAnyData =
    data.held_pct_institutions != null ||
    data.held_pct_insiders != null ||
    topHolders.length > 0 ||
    fundHolders.length > 0 ||
    data.total_institutional_holders != null

  if (!hasAnyData) return null

  return (
    <section id="section-ownership" className="card rounded-lg  p-6">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-xs font-sans font-medium tracking-wide text-zinc-400">Ownership</h3>
        <FreshnessTag fetchedAt={data.fetched_at} />
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <StatCard
          label="Institutional"
          value={formatPct(data.held_pct_institutions)}
          sublabel="Shares held by institutions"
        />
        <StatCard
          label="Insider"
          value={formatPct(data.held_pct_insiders)}
          sublabel="Shares held by insiders"
        />
        <StatCard
          label="Institutions"
          value={data.total_institutional_holders != null ? data.total_institutional_holders.toLocaleString('en-US') : '--'}
          sublabel="Tracked holders"
        />
      </div>

      <div className="mt-4">
        <OwnershipBar
          institutionalPct={data.held_pct_institutions}
          insiderPct={data.held_pct_insiders}
        />
      </div>

      <div className="mt-5 space-y-5">
        {topHolders.length > 0 && (
          <HolderSection
            title="Top Institutions"
            rows={topHolders}
            expanded={showAllInstitutions}
            onToggle={() => setShowAllInstitutions((value) => !value)}
          />
        )}

        {fundHolders.length > 0 && (
          <HolderSection
            title="Top Funds / ETFs"
            rows={fundHolders}
            expanded={showAllFunds}
            onToggle={() => setShowAllFunds((value) => !value)}
          />
        )}
      </div>
    </section>
  )
}
