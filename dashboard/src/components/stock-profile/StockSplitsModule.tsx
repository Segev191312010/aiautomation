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
      <section className="card rounded-lg shadow-card p-6 animate-pulse">
        <div className="h-3 w-28 bg-gray-100 rounded-xl mb-4" />
        <div className="h-8 w-full bg-gray-100 rounded-xl mb-2" />
        <div className="h-8 w-full bg-gray-100 rounded-xl mb-2" />
        <div className="h-8 w-full bg-gray-100 rounded-xl" />
      </section>
    )
  }
  if (!data) return null

  const hasSplits = data.splits && data.splits.length > 0

  return (
    <section id="section-splits" className="card rounded-lg shadow-card p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-xs font-sans font-medium text-gray-500 tracking-wide">Stock Splits</h3>
        <FreshnessTag fetchedAt={data.fetched_at} />
      </div>

      {!hasSplits ? (
        <p className="text-[11px] font-sans text-gray-400 text-center py-4">
          No stock splits on record
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="pb-2 text-left text-[9px] font-sans font-medium text-gray-400 uppercase tracking-wide">
                  Date
                </th>
                <th className="pb-2 text-left text-[9px] font-sans font-medium text-gray-400 uppercase tracking-wide">
                  Type
                </th>
                <th className="pb-2 text-right text-[9px] font-sans font-medium text-gray-400 uppercase tracking-wide">
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
                    className="border-b border-gray-100 last:border-0 hover:bg-gray-50/70 transition-colors"
                  >
                    <td className="py-2.5 text-[11px] font-sans text-gray-500">
                      {formatDate(split.date)}
                    </td>
                    <td className="py-2.5">
                      <span
                        className={[
                          'text-[10px] font-sans font-medium px-2 py-0.5 rounded-md',
                          isForward
                            ? 'bg-green-50 text-green-600'
                            : isReverse
                              ? 'bg-red-50 text-red-600'
                              : 'bg-gray-50/60 text-gray-500',
                        ].join(' ')}
                      >
                        {formatType(split.type)}
                      </span>
                    </td>
                    <td className="py-2.5 text-right text-[12px] font-mono font-bold tabular-nums text-gray-800">
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
