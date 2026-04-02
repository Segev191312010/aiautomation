import clsx from 'clsx'
import { SectionHeader } from '@/components/common/SectionHeader'
import LiveActivityFeed from '@/components/tradebot/LiveActivityFeed'
import { EODSummary } from '@/components/tradebot/EODSummary'
import { TradeRow } from '@/components/tradebot/TradeRow'
import { IconArrows } from '@/components/icons'
import type { Trade } from '@/types'

function TradesEmptyState() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-14">
      <div className="flex h-14 w-14 items-center justify-center rounded-3xl border border-[var(--border)] bg-[var(--bg-hover)]">
        <IconArrows className="h-7 w-7 text-[var(--text-muted)]" />
      </div>
      <p className="text-sm font-sans text-[var(--text-secondary)]">No trades yet</p>
      <p className="text-[11px] font-sans text-[var(--text-muted)]">
        Executed orders will appear here.
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
      <section className="animate-fade-in-up">
        <LiveActivityFeed />
      </section>

      <section className="shell-panel animate-fade-in-up p-5 sm:p-6">
        <EODSummary />
        <SectionHeader
          eyebrow="Ledger"
          icon={<IconArrows className="h-3.5 w-3.5 text-[var(--text-secondary)]" />}
          title="Recent Trades"
          badge={
            trades.length > 0 ? (
              <span className="shell-chip px-3 py-1 text-[11px] font-mono">
                {trades.length > 30 ? '30+' : trades.length}
              </span>
            ) : undefined
          }
        />

        {initialLoad ? (
          <div className="space-y-2 pt-1">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="flex items-center gap-3 animate-pulse">
                <div className="h-3 w-24 rounded-lg bg-[var(--bg-hover)]" />
                <div className="h-3 w-12 rounded-lg bg-[var(--bg-hover)]" />
                <div className="h-5 w-10 rounded-lg bg-[var(--bg-hover)]" />
                <div className="ml-auto h-3 w-8 rounded-lg bg-[var(--bg-hover)]" />
                <div className="h-3 w-16 rounded-lg bg-[var(--bg-hover)]" />
                <div className="h-5 w-16 rounded-lg bg-[var(--bg-hover)]" />
              </div>
            ))}
          </div>
        ) : trades.length === 0 ? (
          <TradesEmptyState />
        ) : (
          <div className="overflow-x-auto">
            <table className="table-editorial w-full min-w-[520px]">
              <thead>
                <tr className="border-b border-[var(--border)]">
                  {['Time', 'Symbol', 'Side', 'Qty', 'Fill Price', 'Status'].map((column, i) => (
                    <th
                      key={column}
                      className={clsx(
                        'px-3 py-2 text-[10px] font-sans font-medium uppercase tracking-widest text-[var(--text-muted)]',
                        i === 0 || i === 1 || i === 2 ? 'text-left' : 'text-right',
                      )}
                    >
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.slice(0, 30).map((trade) => (
                  <TradeRow key={trade.id} trade={trade} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
