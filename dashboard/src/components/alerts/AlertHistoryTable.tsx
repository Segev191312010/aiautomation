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

// ── Row component ─────────────────────────────────────────────────────────────

function HistoryRow({ h }: { h: AlertHistory }) {
  return (
    <tr className="text-sm font-sans text-terminal-dim border-b border-white/[0.06] last:border-0 hover:bg-white/[0.03] transition-colors">
      {/* Fired At — keep font-mono for timestamp */}
      <td className="px-3 py-3 whitespace-nowrap font-mono text-terminal-ghost text-xs">
        {new Date(h.fired_at).toLocaleString()}
      </td>

      {/* Alert Name */}
      <td className="px-3 py-3 text-terminal-text whitespace-nowrap">
        {h.alert_name}
      </td>

      {/* Symbol — keep font-mono for ticker */}
      <td className="px-3 py-3 font-mono text-terminal-amber font-semibold whitespace-nowrap">
        {h.symbol}
      </td>

      {/* Condition */}
      <td className="px-3 py-3 font-mono whitespace-nowrap">
        {h.condition_summary}
      </td>

      {/* Price — right-aligned, green, $ prefix; keep font-mono for number */}
      <td className="px-3 py-3 text-right font-mono text-terminal-green tabular-nums whitespace-nowrap">
        ${h.price_at_trigger.toFixed(2)}
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
      <div className="flex items-center justify-center py-14">
        <p className="text-sm font-sans text-terminal-ghost">
          No alerts have fired yet.
        </p>
      </div>
    )
  }

  return (
    <div className="glass rounded-2xl overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-white/[0.06]">
              {COLUMNS.map((col) => (
                <th
                  key={col}
                  className={[
                    'px-3 py-2.5 text-xs font-sans font-medium text-terminal-dim tracking-wide',
                    col === 'Price' ? 'text-right' : 'text-left',
                  ].join(' ')}
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((h) => (
              <HistoryRow key={h.id} h={h} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
