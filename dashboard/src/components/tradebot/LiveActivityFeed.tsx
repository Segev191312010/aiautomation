/**
 * LiveActivityFeed — Real-time display of trade signals, fills, and brackets.
 * Shows rule name, bracket SL/TP, and % of account for each event.
 */
import React, { useRef, useEffect } from 'react'
import { useAccountStore } from '@/store'
import type { ActivityEvent } from '@/types'

function timeAgo(ts: string): string {
  const diff = (Date.now() - new Date(ts).getTime()) / 1000
  if (diff < 60) return `${Math.floor(diff)}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function StatusBadge({ status }: { status: ActivityEvent['status'] }) {
  const cls =
    status === 'FILLED'    ? 'bg-emerald-500/20 text-emerald-400' :
    status === 'PENDING'   ? 'bg-amber-500/20 text-amber-400' :
                             'bg-red-500/20 text-red-400'
  return (
    <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${cls}`}>
      {status}
    </span>
  )
}

function BracketBar({ fill, sl, tp }: { fill: number; sl: number; tp: number }) {
  const range = tp - sl
  if (range <= 0) return null
  const fillPos = ((fill - sl) / range) * 100
  return (
    <div className="flex items-center gap-2 text-[10px]">
      <span className="text-red-400">${sl.toFixed(2)}</span>
      <div className="flex-1 h-1.5 bg-zinc-800 rounded-full relative overflow-hidden">
        <div
          className="absolute inset-y-0 left-0 bg-gradient-to-r from-red-500 via-zinc-500 to-emerald-500 rounded-full"
          style={{ width: '100%' }}
        />
        <div
          className="absolute top-[-1px] w-2 h-2 bg-zinc-900 rounded-full border border-zinc-600"
          style={{ left: `calc(${fillPos}% - 4px)` }}
        />
      </div>
      <span className="text-emerald-400">${tp.toFixed(2)}</span>
    </div>
  )
}

function ActivityCard({ event }: { event: ActivityEvent }) {
  const isBuy = event.action === 'BUY'

  return (
    <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-3 hover:border-zinc-700 transition-colors">
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2">
          <span className={`text-xs font-bold ${isBuy ? 'text-emerald-400' : 'text-red-400'}`}>
            {event.action}
          </span>
          <span className="text-sm font-semibold text-zinc-100">{event.symbol}</span>
          {event.price && (
            <span className="text-xs text-zinc-400">@${event.price.toFixed(2)}</span>
          )}
          <span className="text-[10px] text-zinc-600">x{event.qty}</span>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={event.status} />
          <span className="text-[10px] text-zinc-600">{timeAgo(event.timestamp)}</span>
        </div>
      </div>

      <div className="flex items-center gap-3 mb-1.5">
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-400 font-medium">
          {event.ruleName}
        </span>
        {event.pctOfAccount != null && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400">
            {event.pctOfAccount.toFixed(1)}% of acct
          </span>
        )}
      </div>

      {event.slPrice && event.tpPrice && event.price && (
        <BracketBar fill={event.price} sl={event.slPrice} tp={event.tpPrice} />
      )}
    </div>
  )
}

export default function LiveActivityFeed() {
  const feed = useAccountStore((s) => s.activityFeed)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to top on new events
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = 0
    }
  }, [feed.length])

  return (
    <div className="bg-zinc-950/50 border border-zinc-800 rounded-2xl p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-sans font-medium text-zinc-400 tracking-widest uppercase">
          Live Activity
        </h3>
        <div className="flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
          </span>
          <span className="text-[10px] text-zinc-500">{feed.length} events</span>
        </div>
      </div>

      {feed.length === 0 ? (
        <div className="text-center py-8 text-zinc-600 text-sm">
          No activity yet — waiting for signals and fills...
        </div>
      ) : (
        <div ref={scrollRef} className="space-y-2 max-h-80 overflow-y-auto pr-1">
          {feed.map((event) => (
            <ActivityCard key={event.id} event={event} />
          ))}
        </div>
      )}
    </div>
  )
}
