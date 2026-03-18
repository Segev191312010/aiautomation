import { useState } from 'react'
import clsx from 'clsx'
import type { StockCompanyInfo, StockOverview, StockEarningsDetail } from '@/types'
import FreshnessTag from './FreshnessTag'

function fmtDollars(v: number | undefined | null): string {
  if (v == null) return '--'
  return `$${Math.round(v).toLocaleString('en-US')}`
}

function fmtCompactRevenue(v: number | null | undefined): string {
  if (v == null) return '--'
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`
  return `$${v.toLocaleString('en-US')}`
}

function fmtEmployees(v: number | null | undefined): string {
  if (v == null) return '--'
  return v.toLocaleString('en-US')
}

interface CompRowProps {
  label: string
  value: string
  emphasize?: boolean
}

function CompRow({ label, value, emphasize }: CompRowProps) {
  return (
    <div
      className={clsx(
        'flex justify-between items-center py-2 border-b border-gray-200 last:border-0',
        emphasize && 'border-t border-gray-200 pt-2.5',
      )}
    >
      <span className={clsx('text-[11px] font-sans', emphasize ? 'text-gray-600 font-semibold' : 'text-gray-500')}>
        {label}
      </span>
      <span className={clsx('text-[11px] font-mono tabular-nums', emphasize ? 'text-gray-900 font-bold' : 'text-gray-800')}>
        {value}
      </span>
    </div>
  )
}

interface InfoCardProps {
  label: string
  value: string
  sub?: string
}

function InfoCard({ label, value, sub }: InfoCardProps) {
  return (
    <div className="flex flex-col gap-1 rounded-lg bg-gray-50 border border-gray-200 p-3">
      <span className="text-[9px] font-sans text-gray-500 uppercase tracking-wide">{label}</span>
      <span className="text-[13px] font-sans font-semibold text-gray-900 leading-tight">{value}</span>
      {sub && <span className="text-[10px] font-sans text-gray-500">{sub}</span>}
    </div>
  )
}

interface EarningsCardProps {
  earningsDetail: StockEarningsDetail | null
}

const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function EarningsCalendarCard({ earningsDetail }: EarningsCardProps) {
  if (!earningsDetail?.next_date) return null

  const parts = earningsDetail.next_date.split('-')
  const month = MONTH_NAMES[parseInt(parts[1] ?? '1', 10) - 1] ?? '--'
  const day = parts[2] ? parseInt(parts[2], 10).toString() : '--'
  const dow = earningsDetail.day_of_week ?? null

  return (
    <div className="rounded-lg bg-amber-50 border border-amber-200 p-4 flex items-stretch gap-4">
      <div className="flex flex-col items-center justify-center rounded-lg bg-white border border-amber-200 px-4 py-2 min-w-[64px]">
        <span className="text-[10px] font-sans font-semibold text-amber-700 uppercase tracking-wider">{month}</span>
        <span className="text-3xl font-mono font-bold text-gray-900 tabular-nums leading-none">{day}</span>
        {dow && <span className="text-[9px] font-sans text-amber-700/70 mt-0.5">{dow}</span>}
      </div>

      <div className="flex flex-col justify-center gap-1 min-w-0">
        <span className="text-[9px] font-sans text-gray-500 uppercase tracking-wide">Next Earnings</span>
        <span className="text-sm font-sans font-semibold text-gray-900">{earningsDetail.next_date}</span>
        {earningsDetail.revenue_estimate != null && (
          <span className="text-[10px] font-sans text-gray-600">
            Revenue est. <span className="text-amber-700 font-medium">{fmtCompactRevenue(earningsDetail.revenue_estimate)}</span>
          </span>
        )}
        {earningsDetail.eps_estimate != null && (
          <span className="text-[10px] font-sans text-gray-600">
            EPS est. <span className="text-amber-700 font-medium">${earningsDetail.eps_estimate.toFixed(2)}</span>
          </span>
        )}
      </div>
    </div>
  )
}

interface Props {
  data: StockCompanyInfo | null
  overview: StockOverview | null
  earningsDetail: StockEarningsDetail | null
  loading: boolean
}

export default function CompanyOverviewModule({ data, overview, earningsDetail, loading }: Props) {
  const [showOfficers, setShowOfficers] = useState(false)

  if (!data && loading) {
    return (
      <section id="section-company" className="card rounded-lg shadow-card p-6 animate-pulse">
        <div className="h-3 w-40 bg-gray-100 rounded mb-5" />
        <div className="h-16 w-full bg-gray-100 rounded-lg mb-4" />
        <div className="h-24 w-full bg-gray-100 rounded-lg mb-4" />
        <div className="grid grid-cols-3 gap-3">
          <div className="h-20 bg-gray-100 rounded-lg" />
          <div className="h-20 bg-gray-100 rounded-lg" />
          <div className="h-20 bg-gray-100 rounded-lg" />
        </div>
      </section>
    )
  }
  if (!data) return null

  const industry = data.industry ?? overview?.industry ?? null
  const sector = data.sector ?? overview?.sector ?? null
  const employees = data.employees ?? overview?.employees ?? null
  const comp = data.ceo_compensation

  const hasComp =
    comp &&
    (comp.salary != null ||
      comp.stock_awards != null ||
      comp.other_compensation != null ||
      comp.total_compensation != null)

  return (
    <section id="section-company" className="card rounded-lg shadow-card p-6 flex flex-col gap-5">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-sans font-medium text-gray-600 tracking-wide">Company Overview</h3>
        <FreshnessTag fetchedAt={data.fetched_at} />
      </div>

      {overview?.description && <p className="text-[11px] font-sans text-gray-600 leading-relaxed">{overview.description}</p>}

      {data.ceo && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3 rounded-lg bg-gray-50 border border-gray-200 p-4">
            <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-gray-900 shrink-0">
              <span className="text-sm font-sans font-bold text-white">
                {data.ceo
                  .split(' ')
                  .map((w) => w[0])
                  .slice(0, 2)
                  .join('')}
              </span>
            </div>
            <div className="min-w-0">
              <p className="text-sm font-sans font-bold text-gray-900 leading-snug">{data.ceo}</p>
              <p className="text-[10px] font-sans text-gray-500">{data.ceo_title ?? 'Chief Executive Officer'}</p>
            </div>
          </div>

          {hasComp && (
            <div className="rounded-lg bg-gray-50 border border-gray-200 px-4 py-2">
              <span className="text-[9px] font-sans text-gray-500 uppercase tracking-wide">Compensation</span>
              <div className="mt-2">
                {comp!.salary != null && <CompRow label="Salary" value={fmtDollars(comp!.salary)} />}
                {comp!.bonus != null && comp!.bonus > 0 && <CompRow label="Bonus" value={fmtDollars(comp!.bonus)} />}
                {comp!.stock_awards != null && <CompRow label="Stock Awards" value={fmtDollars(comp!.stock_awards)} />}
                {comp!.other_compensation != null && <CompRow label="Other" value={fmtDollars(comp!.other_compensation)} />}
                {comp!.total_compensation != null && <CompRow label="Total Compensation" value={fmtDollars(comp!.total_compensation)} emphasize />}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="grid grid-cols-3 gap-3">
        {industry && <InfoCard label="Industry" value={industry} />}
        {sector && <InfoCard label="Sector" value={sector} />}
        <InfoCard label="IPO Date" value={data.ipo_date ?? '--'} sub={employees != null ? `${fmtEmployees(employees)} employees` : undefined} />
      </div>

      {earningsDetail?.next_date && <EarningsCalendarCard earningsDetail={earningsDetail} />}

      {data.officers && data.officers.length > 0 && (
        <div>
          <button
            onClick={() => setShowOfficers((v) => !v)}
            className="text-[10px] font-sans text-gray-700 hover:text-gray-900 transition-colors"
          >
            {showOfficers ? 'Hide executives' : `View all ${data.officers.length} executives`}
          </button>
          {showOfficers && (
            <div className="mt-2 flex flex-col">
              {data.officers.map((officer, idx) => (
                <div key={idx} className="flex justify-between items-baseline py-1.5 border-b border-gray-100 last:border-0">
                  <span className="text-[10px] font-sans text-gray-700 truncate max-w-[52%]">{officer.name}</span>
                  <span className="text-[9px] font-sans text-gray-500 truncate max-w-[44%] text-right">{officer.title}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
