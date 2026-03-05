import { useState } from 'react'
import clsx from 'clsx'
import type { StockCompanyInfo, StockOverview, StockEarningsDetail } from '@/types'
import FreshnessTag from './FreshnessTag'

// ── Formatters ────────────────────────────────────────────────────────────────

function fmtDollars(v: number | undefined | null): string {
  if (v == null) return '—'
  return `$${Math.round(v).toLocaleString()}`
}

function fmtCompactRevenue(v: number | null | undefined): string {
  if (v == null) return '—'
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`
  if (v >= 1e9)  return `$${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6)  return `$${(v / 1e6).toFixed(1)}M`
  return `$${v.toLocaleString()}`
}

function fmtEmployees(v: number | null | undefined): string {
  if (v == null) return '—'
  return v.toLocaleString()
}

// ── Industry / Sector icon helpers ────────────────────────────────────────────

const SECTOR_ICONS: Record<string, string> = {
  Technology: '💻',
  Healthcare: '🏥',
  Financials: '🏦',
  'Consumer Cyclical': '🛍️',
  'Consumer Defensive': '🛒',
  Energy: '⚡',
  Industrials: '⚙️',
  'Real Estate': '🏢',
  Materials: '🪨',
  Utilities: '💡',
  'Communication Services': '📡',
}

const INDUSTRY_ICONS: Record<string, string> = {
  Semiconductors: '🔬',
  Software: '📦',
  Biotechnology: '🧬',
  Banks: '🏦',
  Insurance: '🛡️',
  'Oil & Gas': '🛢️',
  Retail: '🏪',
  Aerospace: '✈️',
  Pharmaceuticals: '💊',
}

function sectorIcon(sector: string | null | undefined): string {
  if (!sector) return '🏢'
  return SECTOR_ICONS[sector] ?? '🏢'
}

function industryIcon(industry: string | null | undefined): string {
  if (!industry) return '🔬'
  return INDUSTRY_ICONS[industry] ?? '🔧'
}

// ── Sub-components ────────────────────────────────────────────────────────────

interface CompRow { label: string; value: string; emphasize?: boolean }
function CompRow({ label, value, emphasize }: CompRow) {
  return (
    <div
      className={clsx(
        'flex justify-between items-center py-2 border-b border-white/[0.06] last:border-0',
        emphasize && 'border-t border-white/[0.1] pt-2.5',
      )}
    >
      <span
        className={clsx(
          'text-[11px] font-sans',
          emphasize ? 'text-terminal-dim font-semibold' : 'text-terminal-ghost',
        )}
      >
        {label}
      </span>
      <span
        className={clsx(
          'text-[11px] font-mono tabular-nums',
          emphasize ? 'text-terminal-text font-bold' : 'text-terminal-text',
        )}
      >
        {value}
      </span>
    </div>
  )
}

interface InfoCardProps {
  icon: string
  label: string
  value: string
  sub?: string
}
function InfoCard({ icon, label, value, sub }: InfoCardProps) {
  return (
    <div className="flex flex-col gap-1 rounded-xl bg-white/[0.03] border border-white/[0.06] p-3">
      <span className="text-base leading-none">{icon}</span>
      <span className="text-[9px] font-sans text-terminal-ghost uppercase tracking-wide mt-1">{label}</span>
      <span className="text-[13px] font-sans font-semibold text-terminal-text leading-tight">{value}</span>
      {sub && <span className="text-[10px] font-sans text-terminal-ghost">{sub}</span>}
    </div>
  )
}

// ── Earnings Calendar Card ─────────────────────────────────────────────────────

interface EarningsCardProps {
  earningsDetail: StockEarningsDetail | null
}

const MONTH_NAMES = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

function EarningsCalendarCard({ earningsDetail }: EarningsCardProps) {
  if (!earningsDetail?.next_date) return null

  // Parse YYYY-MM-DD
  const parts = earningsDetail.next_date.split('-')
  const month = MONTH_NAMES[parseInt(parts[1] ?? '1', 10) - 1] ?? '—'
  const day   = parts[2] ? parseInt(parts[2], 10).toString() : '—'
  const dow   = earningsDetail.day_of_week ?? null

  return (
    <div className="rounded-xl bg-indigo-500/10 border border-indigo-500/20 p-4 flex items-stretch gap-4">
      {/* Calendar block */}
      <div className="flex flex-col items-center justify-center rounded-lg bg-indigo-600/20 border border-indigo-500/30 px-4 py-2 min-w-[64px]">
        <span className="text-[10px] font-sans font-semibold text-indigo-300 uppercase tracking-wider">{month}</span>
        <span className="text-3xl font-mono font-bold text-terminal-text tabular-nums leading-none">{day}</span>
        {dow && (
          <span className="text-[9px] font-sans text-indigo-300/70 mt-0.5">{dow}</span>
        )}
      </div>

      {/* Details */}
      <div className="flex flex-col justify-center gap-1 min-w-0">
        <span className="text-[9px] font-sans text-terminal-ghost uppercase tracking-wide">Next Earnings</span>
        <span className="text-sm font-sans font-semibold text-terminal-text">
          {earningsDetail.next_date}
        </span>
        {earningsDetail.revenue_estimate != null && (
          <span className="text-[10px] font-sans text-terminal-dim">
            Rev. est. <span className="text-indigo-300 font-medium">{fmtCompactRevenue(earningsDetail.revenue_estimate)}</span>
          </span>
        )}
        {earningsDetail.eps_estimate != null && (
          <span className="text-[10px] font-sans text-terminal-dim">
            EPS est. <span className="text-indigo-300 font-medium">${earningsDetail.eps_estimate.toFixed(2)}</span>
          </span>
        )}
      </div>
    </div>
  )
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  data: StockCompanyInfo | null
  overview: StockOverview | null
  earningsDetail: StockEarningsDetail | null
  loading: boolean
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function CompanyOverviewModule({ data, overview, earningsDetail, loading }: Props) {
  const [showOfficers, setShowOfficers] = useState(false)

  if (!data && loading) {
    return (
      <section className="glass rounded-2xl shadow-glass p-6 animate-pulse">
        <div className="h-3 w-40 bg-terminal-muted rounded-xl mb-5" />
        <div className="h-16 w-full bg-terminal-muted rounded-xl mb-4" />
        <div className="h-24 w-full bg-terminal-muted rounded-xl mb-4" />
        <div className="grid grid-cols-3 gap-3">
          <div className="h-20 bg-terminal-muted rounded-xl" />
          <div className="h-20 bg-terminal-muted rounded-xl" />
          <div className="h-20 bg-terminal-muted rounded-xl" />
        </div>
      </section>
    )
  }
  if (!data) return null

  const industry = data.industry ?? overview?.industry ?? null
  const sector   = data.sector   ?? overview?.sector   ?? null
  const employees = data.employees ?? overview?.employees ?? null
  const comp = data.ceo_compensation

  const hasComp = comp && (
    comp.salary != null ||
    comp.stock_awards != null ||
    comp.other_compensation != null ||
    comp.total_compensation != null
  )

  return (
    <section id="section-company" className="glass rounded-2xl shadow-glass p-6 flex flex-col gap-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-sans font-medium text-terminal-dim tracking-wide">Company Overview</h3>
        <FreshnessTag fetchedAt={data.fetched_at} />
      </div>

      {/* Description */}
      {overview?.description && (
        <p className="text-[11px] font-sans text-terminal-dim leading-relaxed">
          {overview.description}
        </p>
      )}

      {/* CEO Card + Compensation */}
      {data.ceo && (
        <div className="flex flex-col gap-3">
          {/* CEO profile card */}
          <div className="flex items-center gap-3 rounded-xl bg-white/[0.04] border border-white/[0.08] p-4">
            {/* Avatar placeholder */}
            <div className="flex items-center justify-center w-10 h-10 rounded-full bg-indigo-600/20 border border-indigo-500/30 shrink-0">
              <span className="text-sm font-sans font-bold text-indigo-300">
                {data.ceo.split(' ').map(w => w[0]).slice(0, 2).join('')}
              </span>
            </div>
            <div className="min-w-0">
              <p className="text-sm font-sans font-bold text-terminal-text leading-snug">{data.ceo}</p>
              <p className="text-[10px] font-sans text-terminal-ghost">{data.ceo_title ?? 'Chief Executive Officer'}</p>
            </div>
          </div>

          {/* Compensation table */}
          {hasComp && (
            <div className="rounded-xl bg-white/[0.03] border border-white/[0.06] px-4 py-2">
              <span className="text-[9px] font-sans text-terminal-ghost uppercase tracking-wide">Compensation</span>
              <div className="mt-2">
                {comp!.salary != null && (
                  <CompRow label="Salary" value={fmtDollars(comp!.salary)} />
                )}
                {comp!.bonus != null && comp!.bonus > 0 && (
                  <CompRow label="Bonus" value={fmtDollars(comp!.bonus)} />
                )}
                {comp!.stock_awards != null && (
                  <CompRow label="Stock Awards" value={fmtDollars(comp!.stock_awards)} />
                )}
                {comp!.other_compensation != null && (
                  <CompRow label="Other" value={fmtDollars(comp!.other_compensation)} />
                )}
                {comp!.total_compensation != null && (
                  <CompRow label="Total Compensation" value={fmtDollars(comp!.total_compensation)} emphasize />
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Industry / Sector / IPO + Employees grid */}
      <div className="grid grid-cols-3 gap-3">
        {industry && (
          <InfoCard
            icon={industryIcon(industry)}
            label="Industry"
            value={industry}
          />
        )}
        {sector && (
          <InfoCard
            icon={sectorIcon(sector)}
            label="Sector"
            value={sector}
          />
        )}
        <InfoCard
          icon="📅"
          label="IPO Date"
          value={data.ipo_date ?? '—'}
          sub={employees != null ? `${fmtEmployees(employees)} employees` : undefined}
        />
      </div>

      {/* Upcoming Earnings */}
      {earningsDetail?.next_date && (
        <EarningsCalendarCard earningsDetail={earningsDetail} />
      )}

      {/* Officers expandable list */}
      {data.officers && data.officers.length > 0 && (
        <div>
          <button
            onClick={() => setShowOfficers(v => !v)}
            className="text-[10px] font-sans text-indigo-400 hover:text-indigo-300 transition-colors"
          >
            {showOfficers ? '▾ Hide executives' : `▸ View all ${data.officers.length} executives`}
          </button>
          {showOfficers && (
            <div className="mt-2 flex flex-col">
              {data.officers.map((o, i) => (
                <div
                  key={i}
                  className="flex justify-between items-baseline py-1.5 border-b border-white/[0.04] last:border-0"
                >
                  <span className="text-[10px] font-sans text-terminal-dim truncate max-w-[52%]">{o.name}</span>
                  <span className="text-[9px] font-sans text-terminal-ghost truncate max-w-[44%] text-right">{o.title}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
