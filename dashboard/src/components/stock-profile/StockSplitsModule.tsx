import type { StockSplits } from '@/types'
import FreshnessTag from './FreshnessTag'

interface Props {
  data: StockSplits | null
  loading: boolean
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr)
  if (isNaN(d.getTime())) return dateStr
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function formatType(type: string): string {
  const lower = type.toLowerCase()
  if (lower === 'forward') return 'Forward'
  if (lower === 'reverse') return 'Reverse'
  // Capitalize first letter as fallback
  return type.charAt(0).toUpperCase() + type.slice(1)
}

export default function StockSplitsModule({ data, loading }: Props) {
  if (!data && loading) {
    return (
      <section className="glass rounded-2xl shadow-glass p-6 animate-pulse">
        <div className="h-3 w-28 bg-terminal-muted rounded-xl mb-4" />
        <div className="h-8 w-full bg-terminal-muted rounded-xl mb-2" />
        <div className="h-8 w-full bg-terminal-muted rounded-xl mb-2" />
        <div className="h-8 w-full bg-terminal-muted rounded-xl" />
      </section>
    )
  }
  if (!data) return null

  const hasSplits = data.splits && data.splits.length > 0

  return (
    <section id="section-splits" className="glass rounded-2xl shadow-glass p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-xs font-sans font-medium text-terminal-dim tracking-wide">Stock Splits</h3>
        <FreshnessTag fetchedAt={data.fetched_at} />
      </div>

      {!hasSplits ? (
        <p className="text-[11px] font-sans text-terminal-ghost text-center py-4">
          No stock splits on record
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="pb-2 text-left text-[9px] font-sans font-medium text-terminal-ghost uppercase tracking-wide">
                  Date
                </th>
                <th className="pb-2 text-left text-[9px] font-sans font-medium text-terminal-ghost uppercase tracking-wide">
                  Type
                </th>
                <th className="pb-2 text-right text-[9px] font-sans font-medium text-terminal-ghost uppercase tracking-wide">
                  Ratio
                </th>
              </tr>
            </thead>
            <tbody>
              {data.splits.map((split, i) => {
                const isForward = split.type.toLowerCase() === 'forward'
                const isReverse = split.type.toLowerCase() === 'reverse'
                return (
                  <tr
                    key={i}
                    className="border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02] transition-colors"
                  >
                    <td className="py-2.5 text-[11px] font-sans text-terminal-dim">
                      {formatDate(split.date)}
                    </td>
                    <td className="py-2.5">
                      <span
                        className={[
                          'text-[10px] font-sans font-medium px-2 py-0.5 rounded-md',
                          isForward
                            ? 'bg-terminal-green/10 text-terminal-green'
                            : isReverse
                              ? 'bg-terminal-red/10 text-terminal-red'
                              : 'bg-white/[0.05] text-terminal-dim',
                        ].join(' ')}
                      >
                        {formatType(split.type)}
                      </span>
                    </td>
                    <td className="py-2.5 text-right text-[12px] font-mono font-bold tabular-nums text-terminal-text">
                      {split.ratio}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
