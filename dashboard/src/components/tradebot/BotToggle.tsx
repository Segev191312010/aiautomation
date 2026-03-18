/**
 * BotToggle — "Automated Trading" master switch with live cycle stats.
 */
import React, { useState } from 'react'
import clsx from 'clsx'
import { useBotStore } from '@/store'
import { startBot, stopBot } from '@/services/api'

function fmtTime(iso: string | null): string {
  if (!iso) return '--'
  try { return new Date(iso).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) }
  catch { return '--' }
}

export default function BotToggle() {
  const botRunning    = useBotStore(s => s.botRunning)
  const ibkrConnected = useBotStore(s => s.ibkrConnected)
  const simMode       = useBotStore(s => s.simMode)
  const setBotRunning = useBotStore(s => s.setBotRunning)
  const cycleStats    = useBotStore(s => s.cycleStats)
  const [busy, setBusy] = useState(false)

  const handleToggle = async () => {
    if (busy) return
    if (!botRunning && !simMode && ibkrConnected) {
      if (!window.confirm('Enable automated trading on LIVE account?\n\nReal orders will be placed.')) return
    }
    setBusy(true)
    try {
      if (botRunning) { await stopBot(); setBotRunning(false) }
      else { await startBot(); setBotRunning(true) }
    } catch (e) { console.error(e) }
    finally { setBusy(false) }
  }

  const hasStats = botRunning && (cycleStats.rulesEnabled > 0 || cycleStats.symbolsScanned > 0)

  return (
    <div className={clsx(
      'rounded-xl border transition-all',
      botRunning ? 'bg-emerald-500/5 border-emerald-500/30' : 'bg-zinc-900/80 border-zinc-800',
    )}>
      <div className="flex items-center justify-between p-4">
        <div>
          <div className="flex items-center gap-2">
            {botRunning && (
              <span className="relative flex h-2.5 w-2.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500" />
              </span>
            )}
            <span className="text-sm font-semibold text-zinc-100">Automated Trading</span>
            {simMode && (
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400">SIM</span>
            )}
            {!simMode && !ibkrConnected && (
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-red-500/15 text-red-400">IBKR OFFLINE</span>
            )}
          </div>
          <p className="text-xs text-zinc-500 mt-0.5">
            {botRunning ? 'Rules engine active — scanning markets' : 'Bot stopped — no orders placed'}
          </p>
        </div>
        <button
          onClick={handleToggle}
          disabled={busy || (!ibkrConnected && !simMode)}
          aria-label="Toggle automated trading"
          className={clsx(
            'relative w-14 h-7 rounded-full border-2 transition-all duration-200 focus:outline-none',
            'disabled:opacity-40 disabled:cursor-not-allowed',
            botRunning ? 'bg-emerald-600 border-emerald-400' : 'bg-zinc-800 border-zinc-700',
          )}
        >
          <span className={clsx(
            'absolute top-0.5 w-5 h-5 rounded-full bg-zinc-900 shadow transition-transform duration-200',
            botRunning ? 'translate-x-7' : 'translate-x-0.5',
          )} />
        </button>
      </div>

      {hasStats && (
        <div className="border-t border-zinc-800 px-4 py-2 flex flex-wrap items-center gap-4 text-[10px] font-mono">
          <div className="flex items-center gap-1.5">
            <span className="text-zinc-500 uppercase tracking-wider">Rules</span>
            <span className="text-zinc-300 font-semibold">{cycleStats.rulesEnabled}</span>
          </div>
          {cycleStats.symbolsScanned > 0 && (
            <div className="flex items-center gap-1.5">
              <span className="text-zinc-500 uppercase tracking-wider">Symbols</span>
              <span className="text-zinc-300 font-semibold">{cycleStats.symbolsScanned.toLocaleString()}</span>
            </div>
          )}
          <div className="flex items-center gap-1.5">
            <span className="text-zinc-500 uppercase tracking-wider">Signals</span>
            <span className={clsx('font-semibold', cycleStats.signals > 0 ? 'text-emerald-400' : 'text-zinc-600')}>
              {cycleStats.signals}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-zinc-500 uppercase tracking-wider">Last</span>
            <span className="text-zinc-400">{fmtTime(cycleStats.lastRun)}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-zinc-500 uppercase tracking-wider">Next</span>
            <span className="text-zinc-400">{fmtTime(cycleStats.nextRun)}</span>
          </div>
        </div>
      )}
    </div>
  )
}
