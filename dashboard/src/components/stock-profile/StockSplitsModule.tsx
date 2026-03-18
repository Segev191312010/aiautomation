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
      <section className="card rounded-lg  p-6 animate-pulse">
        <div className="h-3 w-28 bg-zinc-800 rounded-xl mb-4" />
        <div className="h-8 w-full bg-zinc-800 rounded-xl mb-2" />
        <div className="h-8 w-full bg-zinc-800 rounded-xl mb-2" />
        <div className="h-8 w-full bg-zinc-800 rounded-xl" />
      </section>
    )
  }
  if (!data) return null

  const hasSplits = data.splits && data.splits.length > 0

  return (
    <section id="section-splits" className="card rounded-lg  p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-xs font-sans font-medium text-zinc-400 tracking-wide">Stock Splits</h3>
        <FreshnessTag fetchedAt={data.fetched_at} />
      </div>

      {!hasSplits ? (
        <p className="text-[11px] font-sans text-zinc-500 text-center py-4">
          No stock splits on record
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-zinc-800">
                <th className="pb-2 text-left text-[9px] font-sans font-medium text-zinc-500 uppercase tracking-wide">
                  Date
                </th>
                <th className="pb-2 text-left text-[9px] font-sans font-medium text-zinc-500 uppercase tracking-wide">
                  Type
                </th>
                <th className="pb-2 text-right text-[9px] font-sans font-medium text-zinc-500 uppercase tracking-wide">
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
                    className="border-b border-zinc-800 last:border-0 hover:bg-zinc-900/70 transition-colors"
                  >
                    <td className="py-2.5 text-[11px] font-sans text-zinc-400">
                      {formatDate(split.date)}
                    </td>
                    <td className="py-2.5">
                      <span
                        className={[
                          'text-[10px] font-sans font-medium px-2 py-0.5 rounded-md',
                          isForward
                            ? 'bg-emerald-500/10 text-emerald-400'
                            : isReverse
                              ? 'bg-red-500/10 text-red-400'
                              : 'bg-zinc-900/60 text-zinc-400',
                        ].join(' ')}
                      >
                        {formatType(split.type)}
                      </span>
                    </td>
                    <td className="py-2.5 text-right text-[12px] font-mono font-bold tabular-nums text-zinc-100">
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
