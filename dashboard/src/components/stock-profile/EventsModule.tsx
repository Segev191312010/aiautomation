import type { StockEvents } from '@/types'
import FreshnessTag from './FreshnessTag'

function daysUntil(dateStr: string): number | null {
  try {
    const target = new Date(dateStr + 'T00:00:00Z')
    const now = new Date()
    const diff = target.getTime() - now.getTime()
    return Math.ceil(diff / (1000 * 60 * 60 * 24))
  } catch {
    return null
  }
}

function EventRow({ label, date }: { label: string; date: string | null }) {
  if (!date) return null
  const days = daysUntil(date)
  const isPast = days != null && days < 0
  const countdown = days != null
    ? isPast ? `${Math.abs(days)}d ago` : days === 0 ? 'Today' : `in ${days}d`
    : null

  return (
    <div className="flex justify-between items-center py-2 border-b border-white/[0.06] last:border-0">
      <span className="text-[11px] font-sans text-terminal-dim">{label}</span>
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-mono text-terminal-text">{date}</span>
        {countdown && (
          <span className={`text-[10px] font-sans px-1.5 py-0.5 rounded-xl ${
            isPast ? 'text-terminal-ghost' : 'text-terminal-amber bg-terminal-amber/10'
          }`}>
            {countdown}
          </span>
        )}
      </div>
    </div>
  )
}

interface Props { data: StockEvents | null; loading: boolean }

export default function EventsModule({ data, loading }: Props) {
  if (!data && loading) {
    return (
      <section className="glass rounded-2xl shadow-glass p-6 animate-pulse">
        <div className="h-3 w-32 bg-terminal-muted rounded-xl mb-4" />
        <div className="h-4 w-full bg-terminal-muted rounded-xl mb-2" />
        <div className="h-4 w-full bg-terminal-muted rounded-xl" />
      </section>
    )
  }
  if (!data) return null

  const hasAny = data.next_earnings_date || data.ex_dividend_date
  if (!hasAny) {
    return (
      <section id="section-events" className="glass rounded-2xl shadow-glass p-6">
        <h3 className="text-xs font-sans font-medium text-terminal-dim tracking-wide mb-2">Events</h3>
        <span className="text-[11px] font-sans text-terminal-ghost">No upcoming events found</span>
      </section>
    )
  }

  return (
    <section id="section-events" className="glass rounded-2xl shadow-glass p-6">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-sans font-medium text-terminal-dim tracking-wide">Events</h3>
        <FreshnessTag fetchedAt={data.fetched_at} />
      </div>
      <EventRow label="Next Earnings" date={data.next_earnings_date} />
      <EventRow label="Ex-Dividend" date={data.ex_dividend_date} />
    </section>
  )
}
