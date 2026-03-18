import type { StockEarningsDetail, StockEvents } from '@/types'
import FreshnessTag from './FreshnessTag'

const MONTH_NAMES = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

function parseYMD(dateStr: string): { year: number; month: number; day: number } | null {
  const parts = dateStr.split('-')
  if (parts.length !== 3) return null
  const year = parseInt(parts[0] ?? '0', 10)
  const month = parseInt(parts[1] ?? '0', 10)
  const day = parseInt(parts[2] ?? '0', 10)
  if (!year || !month || !day) return null
  return { year, month, day }
}

function daysUntil(dateStr: string): number | null {
  const parsed = parseYMD(dateStr)
  if (!parsed) return null
  const today = new Date()
  const todayMidnight = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())
  const targetMidnight = Date.UTC(parsed.year, parsed.month - 1, parsed.day)
  return Math.round((targetMidnight - todayMidnight) / (1000 * 60 * 60 * 24))
}

function countdownLabel(days: number): string {
  if (days === 0) return 'Today'
  if (days > 0) return `in ${days} day${days === 1 ? '' : 's'}`
  return `${Math.abs(days)} day${Math.abs(days) === 1 ? '' : 's'} ago`
}

function formatRevenue(value: number | null | undefined): string {
  if (value == null) return '--'
  if (value >= 1e12) return `$${(value / 1e12).toFixed(2)}T`
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`
  if (value >= 1e6) return `$${(value / 1e6).toFixed(1)}M`
  return `$${Math.round(value).toLocaleString('en-US')}`
}

function EventCard({
  label,
  date,
  accent,
  details,
}: {
  label: string
  date: string
  accent: 'indigo' | 'amber'
  details?: { label: string; value: string }[]
}) {
  const parsed = parseYMD(date)
  const days = daysUntil(date)
  const monthName = parsed ? MONTH_NAMES[parsed.month - 1] ?? '--' : '--'
  const dayNum = parsed ? String(parsed.day) : '--'
  const badgeClass =
    days != null && days < 0
      ? 'bg-zinc-800 text-zinc-400'
      : accent === 'indigo'
        ? 'bg-indigo-100 text-indigo-700'
        : 'bg-amber-100 text-amber-700'
  const panelClass =
    accent === 'indigo'
      ? 'border-indigo-100 bg-indigo-50'
      : 'border-amber-100 bg-amber-50'

  return (
    <div className={`rounded-lg border p-4 ${panelClass}`}>
      <div className="flex items-start gap-4">
        <div className="flex min-w-[64px] flex-col items-center rounded-lg border border-white bg-zinc-900 px-3 py-2">
          <div className="text-[10px] font-sans font-semibold uppercase tracking-wide text-zinc-400">{monthName}</div>
          <div className="text-3xl font-mono font-bold leading-none text-zinc-50">{dayNum}</div>
        </div>

        <div className="min-w-0 flex-1">
          <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-400">{label}</div>
          <div className="mt-1 text-sm font-sans font-semibold text-zinc-50">{date}</div>

          {details && details.length > 0 && (
            <div className="mt-3 grid gap-2 sm:grid-cols-3">
              {details.map((detail) => (
                <div key={detail.label} className="rounded-md border border-white bg-zinc-900/80 px-3 py-2">
                  <div className="text-[9px] font-sans uppercase tracking-wide text-zinc-400">{detail.label}</div>
                  <div className="mt-1 text-[11px] font-mono font-semibold text-zinc-100">{detail.value}</div>
                </div>
              ))}
            </div>
          )}
        </div>

        {days != null && (
          <div className={`shrink-0 rounded-full px-2.5 py-1 text-[10px] font-sans font-medium ${badgeClass}`}>
            {countdownLabel(days)}
          </div>
        )}
      </div>
    </div>
  )
}

function EmptyEvents() {
  return (
    <div className="rounded-lg border border-dashed border-zinc-800 bg-zinc-900 px-4 py-8 text-center text-[11px] font-sans text-zinc-400">
      No upcoming earnings or dividend events available.
    </div>
  )
}

function EventsSkeleton() {
  return (
    <section className="card rounded-lg  p-6 animate-pulse">
      <div className="mb-4 h-3 w-24 rounded-lg bg-zinc-800" />
      <div className="space-y-3">
        <div className="h-32 rounded-lg bg-zinc-800" />
        <div className="h-24 rounded-lg bg-zinc-800" />
      </div>
    </section>
  )
}

interface Props {
  data: StockEvents | null
  earningsDetail: StockEarningsDetail | null
  loading: boolean
}

export default function EventsModule({ data, earningsDetail, loading }: Props) {
  if (!data && !earningsDetail && loading) return <EventsSkeleton />
  if (!data && !earningsDetail) return null

  const nextEarningsDate = data?.next_earnings_date ?? earningsDetail?.next_date ?? null
  const earningsDetails = [
    earningsDetail?.day_of_week ? { label: 'Day', value: earningsDetail.day_of_week } : null,
    earningsDetail?.eps_estimate != null ? { label: 'EPS Est.', value: `$${earningsDetail.eps_estimate.toFixed(2)}` } : null,
    earningsDetail?.revenue_estimate != null ? { label: 'Revenue Est.', value: formatRevenue(earningsDetail.revenue_estimate) } : null,
  ].filter((value): value is { label: string; value: string } => value != null)

  const hasAny = nextEarningsDate || data?.ex_dividend_date
  const freshness = data?.fetched_at ?? earningsDetail?.fetched_at ?? Date.now() / 1000

  return (
    <section id="section-events" className="card rounded-lg  p-6">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-xs font-sans font-medium tracking-wide text-zinc-400">Events</h3>
        <FreshnessTag fetchedAt={freshness} />
      </div>

      <div className="mt-4 space-y-3">
        {hasAny ? (
          <>
            {nextEarningsDate && (
              <EventCard
                label="Next Earnings"
                date={nextEarningsDate}
                accent="indigo"
                details={earningsDetails}
              />
            )}
            {data?.ex_dividend_date && (
              <EventCard
                label="Ex-Dividend Date"
                date={data.ex_dividend_date}
                accent="amber"
              />
            )}
          </>
        ) : (
          <EmptyEvents />
        )}
      </div>
    </section>
  )
}
