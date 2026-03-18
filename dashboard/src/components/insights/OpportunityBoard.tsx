import React, { useMemo } from 'react'
import { useMarketStore } from '@/store'
import { rankOpportunitySignals } from '@/utils/opportunitySignals'

function sentimentClass(value: number): string {
  if (value >= 20) return 'text-emerald-400'
  if (value <= -20) return 'text-red-400'
  return 'text-amber-600'
}

function scoreBarClass(score: number): string {
  if (score >= 75) return 'bg-emerald-600'
  if (score >= 55) return 'bg-amber-600'
  return 'bg-zinc-800'
}

function SignalCol({
  title,
  emptyText,
  rows,
  toneClass,
  onSelect,
}: {
  title: string
  emptyText: string
  rows: Array<{
    symbol: string
    confidence: number
    sentimentProxy: number
    reasons: string[]
  }>
  toneClass: string
  onSelect: (symbol: string) => void
}) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-[#FAF8F5]/70 p-3">
      <div className="mb-3 flex items-center justify-between">
        <h3 className={`text-xs font-mono font-semibold ${toneClass}`}>{title}</h3>
        <span className="text-[10px] font-mono text-zinc-500">{rows.length}</span>
      </div>

      {rows.length === 0 ? (
        <p className="text-[11px] font-mono text-zinc-500">{emptyText}</p>
      ) : (
        <div className="space-y-2.5">
          {rows.map((row) => (
            <button
              key={row.symbol}
              onClick={() => onSelect(row.symbol)}
              className="w-full rounded border border-zinc-800/80 bg-zinc-900/50 p-2 text-left transition-colors hover:border-indigo-600/40"
            >
              <div className="mb-1.5 flex items-center justify-between">
                <span className="font-mono text-xs font-semibold text-zinc-100">{row.symbol}</span>
                <span className={`text-[10px] font-mono ${sentimentClass(row.sentimentProxy)}`}>
                  sentiment {row.sentimentProxy > 0 ? '+' : ''}{row.sentimentProxy}
                </span>
              </div>

              <div className="mb-1.5 h-1.5 w-full overflow-hidden rounded bg-zinc-800">
                <div
                  className={`h-full ${scoreBarClass(row.confidence)}`}
                  style={{ width: `${row.confidence}%` }}
                />
              </div>

              <div className="mb-1 text-[10px] font-mono text-zinc-500">
                confidence {row.confidence}%
              </div>

              <div className="flex flex-wrap gap-1">
                {row.reasons.map((reason) => (
                  <span
                    key={`${row.symbol}-${reason}`}
                    className="rounded border border-zinc-800 px-1.5 py-0.5 text-[10px] font-mono text-zinc-400"
                  >
                    {reason}
                  </span>
                ))}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default function OpportunityBoard() {
  const watchlists = useMarketStore((s) => s.watchlists)
  const activeWatchlist = useMarketStore((s) => s.activeWatchlist)
  const quotes = useMarketStore((s) => s.quotes)
  const setSelectedSymbol = useMarketStore((s) => s.setSelectedSymbol)

  const board = useMemo(() => {
    const symbols = watchlists.find((w) => w.id === activeWatchlist)?.symbols ?? []
    const availableQuotes = symbols
      .map((sym) => quotes[sym])
      .filter((q): q is NonNullable<typeof q> => Boolean(q))
    const ranked = rankOpportunitySignals(availableQuotes)
    return {
      buy: ranked.filter((s) => s.kind === 'buy_opportunity').slice(0, 4),
      sell: ranked.filter((s) => s.kind === 'sell_risk').slice(0, 4),
    }
  }, [watchlists, activeWatchlist, quotes])

  return (
    <section className="rounded-lg border border-zinc-800 bg-zinc-900 p-3">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h2 className="text-sm font-mono font-semibold text-zinc-100">Signal Board</h2>
          <p className="text-[10px] font-mono text-zinc-500">
            Inspired by sentiment + disconnect analysis (price, 52W range, momentum, volume)
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <SignalCol
          title="Buy Opportunities"
          emptyText="No high-conviction buy setups in this watchlist now."
          rows={board.buy}
          toneClass="text-emerald-400"
          onSelect={setSelectedSymbol}
        />
        <SignalCol
          title="Sell Risks"
          emptyText="No immediate sell-risk setups in this watchlist now."
          rows={board.sell}
          toneClass="text-red-400"
          onSelect={setSelectedSymbol}
        />
      </div>
    </section>
  )
}
