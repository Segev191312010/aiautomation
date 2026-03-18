/**
 * AlertHistoryTable — read-only log of all fired alert events.
 *
 * Data: useAlertStore().history (sorted by fired_at DESC client-side).
 * Columns: Fired At | Alert Name | Symbol | Condition | Price
 */
import { useAlertStore } from '@/store'
import type { AlertHistory } from '@/types'

// ── Column header list ────────────────────────────────────────────────────────

const COLUMNS = ['Fired At', 'Alert Name', 'Symbol', 'Condition', 'Price'] as const

// ── Timestamp formatter ───────────────────────────────────────────────────────

function formatTimestamp(isoStr: string): { date: string; time: string } {
  const d = new Date(isoStr)
  return {
    date: d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }),
    time: d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
  }
}

// ── Row component ─────────────────────────────────────────────────────────────

function HistoryRow({ h, index }: { h: AlertHistory; index: number }) {
  const { date, time } = formatTimestamp(h.fired_at)
  const isEven = index % 2 === 0

  return (
    <tr
      className={[
        'text-sm font-sans transition-colors',
        isEven ? 'bg-transparent' : 'bg-gray-50/60',
        'hover:bg-gray-100/60',
      ].join(' ')}
    >
      {/* Fired At — keep font-mono for timestamp */}
      <td className="px-4 py-3 whitespace-nowrap">
        <div className="flex flex-col gap-0.5">
          <span className="font-mono text-[11px] text-gray-500">{date}</span>
          <span className="font-mono text-[10px] text-gray-400">{time}</span>
        </div>
      </td>

      {/* Alert Name */}
      <td className="px-4 py-3 max-w-[160px]">
        <span className="text-xs font-sans text-gray-800 truncate block" title={h.alert_name}>
          {h.alert_name}
        </span>
      </td>

      {/* Symbol — keep font-mono for ticker */}
      <td className="px-4 py-3 whitespace-nowrap">
        <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-amber-50 border border-amber-600/20 font-mono text-xs font-semibold text-amber-600">
          {h.symbol}
        </span>
      </td>

      {/* Condition */}
      <td className="px-4 py-3 whitespace-nowrap">
        <span className="font-mono text-xs text-gray-500">
          {h.condition_summary}
        </span>
      </td>

      {/* Price — right-aligned, green, $ prefix; keep font-mono for number */}
      <td className="px-4 py-3 text-right whitespace-nowrap">
        <span className="font-mono text-xs font-semibold text-green-600 tabular-nums">
          ${h.price_at_trigger.toFixed(2)}
        </span>
      </td>
    </tr>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function AlertHistoryTable() {
  const history = useAlertStore((s) => s.history)

  // Sort DESC by fired_at on the client so ordering is always correct
  const sorted = [...history].sort(
    (a, b) => new Date(b.fired_at).getTime() - new Date(a.fired_at).getTime(),
  )

  if (sorted.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 gap-3">
        <div className="w-10 h-10 rounded-xl bg-gray-50 flex items-center justify-center">
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5 text-gray-400">
            <path d="M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z" />
          </svg>
        </div>
        <div className="text-center">
          <p className="text-sm font-sans text-gray-500 font-medium">No history yet</p>
          <p className="text-xs font-sans text-gray-400 mt-1">Triggered alerts will appear here.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="card rounded-2xl overflow-hidden border border-gray-200">
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50">
              {COLUMNS.map((col) => (
                <th
                  key={col}
                  className={[
                    'px-4 py-3 text-[11px] font-sans font-semibold text-gray-400 tracking-wider uppercase',
                    col === 'Price' ? 'text-right' : 'text-left',
                  ].join(' ')}
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {sorted.map((h, i) => (
              <HistoryRow key={h.id} h={h} index={i} />
            ))}
          </tbody>
        </table>
      </div>

      {/* Row count footer */}
      <div className="px-4 py-2.5 border-t border-gray-200 bg-gray-50">
        <p className="text-[11px] font-sans text-gray-400">
          {sorted.length} {sorted.length === 1 ? 'event' : 'events'} in history
        </p>
      </div>
    </div>
  )
}
