import clsx from 'clsx'
import { SectionHeader } from '@/components/common/SectionHeader'
import LiveActivityFeed from '@/components/tradebot/LiveActivityFeed'
import { EODSummary } from '@/components/tradebot/EODSummary'
import { TradeRow } from '@/components/tradebot/TradeRow'
import { IconArrows } from '@/components/icons'
import type { Trade } from '@/types'

function TradesEmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-14 gap-3">
      <div className="w-14 h-14 rounded-2xl bg-zinc-800/30 flex items-center justify-center">
        <IconArrows className="w-7 h-7 text-zinc-500/50" />
      </div>
      <p className="text-sm font-sans text-zinc-500">No trades yet</p>
      <p className="text-[11px] font-sans text-zinc-500/60">
        Executed orders will appear here
      </p>
    </div>
  )
}

interface ActivityContentProps {
  trades: Trade[]
  initialLoad: boolean
}

export function ActivityContent({ trades, initialLoad }: ActivityContentProps) {
  return (
    <div className="flex flex-col gap-5">
      {/* Live activity feed */}
      <section className="animate-fade-in-up">
        <LiveActivityFeed />
      </section>

      {/* Trade log */}
      <section className="bg-zinc-900/80 border border-zinc-800 rounded-2xl p-5 animate-fade-in-up">
        <EODSummary />
        <SectionHeader
          eyebrow=""
          icon={<IconArrows className="w-3.5 h-3.5 text-zinc-400" />}
          title="Recent Trades"
          badge={
            trades.length > 0 ? (
              <span className="ml-auto text-[11px] font-mono text-zinc-500 bg-zinc-800/50 px-2 py-0.5 rounded-lg">
                {trades.length > 30 ? '30+' : trades.length}
              </span>
            ) : undefined
          }
        />

        {initialLoad ? (
          <div className="space-y-2 pt-1">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="flex gap-3 items-center animate-pulse">
                <div className="h-3 w-24 rounded-lg bg-zinc-800/40" />
                <div className="h-3 w-12 rounded-lg bg-zinc-800/30" />
                <div className="h-5 w-10 rounded-lg bg-zinc-800/20" />
                <div className="h-3 w-8 rounded-lg bg-zinc-800/30 ml-auto" />
                <div className="h-3 w-16 rounded-lg bg-zinc-800/20" />
                <div className="h-5 w-16 rounded-lg bg-zinc-800/20" />
              </div>
            ))}
          </div>
        ) : trades.length === 0 ? (
          <TradesEmptyState />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px]">
              <thead>
                <tr className="border-b border-zinc-800">
                  {['Time', 'Symbol', 'Side', 'Qty', 'Fill Price', 'Status'].map((c, i) => (
                    <th
                      key={c}
                      className={clsx(
                        'py-2 px-3 text-[10px] font-sans font-medium uppercase tracking-widest text-zinc-500',
                        i === 0 || i === 1 || i === 2 ? 'text-left' : 'text-right',
                      )}
                    >
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.slice(0, 30).map((t) => (
                  <TradeRow key={t.id} trade={t} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
