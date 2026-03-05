import type { StockOwnership } from '@/types'
import FreshnessTag from './FreshnessTag'

function PctBar({ label, pct }: { label: string; pct: number | null }) {
  const width = pct != null ? Math.min(100, pct * 100) : 0
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-[10px]">
        <span className="font-sans text-terminal-dim">{label}</span>
        <span className="font-mono text-terminal-text tabular-nums">{pct != null ? `${(pct * 100).toFixed(1)}%` : '—'}</span>
      </div>
      <div className="h-1.5 bg-terminal-muted rounded-full overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-indigo-500 to-purple-500 rounded-full transition-all duration-300"
          style={{ width: `${width}%` }}
        />
      </div>
    </div>
  )
}

interface Props { data: StockOwnership | null; loading: boolean }

export default function OwnershipModule({ data, loading }: Props) {
  if (!data && loading) {
    return (
      <section className="glass rounded-2xl shadow-glass p-6 animate-pulse">
        <div className="h-3 w-28 bg-terminal-muted rounded-xl mb-4" />
        <div className="h-4 w-full bg-terminal-muted rounded-xl mb-3" />
        <div className="h-4 w-full bg-terminal-muted rounded-xl" />
      </section>
    )
  }
  if (!data) return null

  return (
    <section id="section-ownership" className="glass rounded-2xl shadow-glass p-6">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-sans font-medium text-terminal-dim tracking-wide">Ownership</h3>
        <FreshnessTag fetchedAt={data.fetched_at} />
      </div>

      <div className="flex flex-col gap-3 mb-4">
        <PctBar label="Institutional" pct={data.held_pct_institutions} />
        <PctBar label="Insiders" pct={data.held_pct_insiders} />
      </div>

      {data.top_holders && data.top_holders.length > 0 && (
        <div>
          <span className="text-[9px] font-sans text-terminal-ghost uppercase tracking-wide">Top Holders</span>
          <div className="mt-1.5 flex flex-col">
            {data.top_holders.slice(0, 5).map((h, i) => (
              <div key={i} className="flex justify-between py-1 border-b border-white/[0.06] last:border-0">
                <span className="text-[10px] font-sans text-terminal-dim truncate max-w-[60%]">{h.name}</span>
                <span className="text-[10px] font-mono text-terminal-text tabular-nums">
                  {h.pct ? `${(h.pct * 100).toFixed(2)}%` : `${(h.shares / 1e6).toFixed(1)}M shares`}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
