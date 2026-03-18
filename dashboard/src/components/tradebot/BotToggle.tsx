/**
 * BotToggle — prominent "Automated Trading" master switch with live cycle stats.
 * Confirms before enabling if operating with real money.
 */
import React, { useState } from 'react'
import clsx from 'clsx'
import { useBotStore } from '@/store'
import { startBot, stopBot } from '@/services/api'

function fmtTime(iso: string | null): string {
  if (!iso) return '--'
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  } catch { return '--' }
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

    // Safety confirmation for live trading
    if (!botRunning && !simMode && ibkrConnected) {
      const ok = window.confirm(
        'You are about to enable automated trading with a LIVE account.\n\nRules will place real orders. Continue?',
      )
      if (!ok) return
    }

    setBusy(true)
    try {
      if (botRunning) {
        await stopBot()
        setBotRunning(false)
      } else {
        await startBot()
        setBotRunning(true)
      }
    } catch (e) {
      console.error(e)
    } finally {
      setBusy(false)
    }
  }

  const hasStats = botRunning && (cycleStats.rulesEnabled > 0 || cycleStats.symbolsScanned > 0)

  return (
    <div
      className={clsx(
        'rounded-lg border transition-all',
        botRunning
          ? 'bg-green-600/5 border-green-300/30'
          : 'bg-white border-gray-200',
      )}
    >
      {/* Main toggle row */}
      <div className="flex items-center justify-between p-4">
        <div>
          <div className="flex items-center gap-2">
            {botRunning && (
              <span className="relative flex h-2.5 w-2.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-green-500" />
              </span>
            )}
            <span className="text-sm font-semibold text-gray-800">Automated Trading</span>
            {simMode && (
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-amber-600/15 text-amber-600">
                SIMULATION
              </span>
            )}
            {!simMode && !ibkrConnected && (
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-red-600/15 text-red-600">
                IBKR OFFLINE
              </span>
            )}
          </div>
          <p className="text-xs text-gray-500 mt-0.5">
            {botRunning
              ? 'Rules engine is active — scanning markets'
              : 'Bot is stopped — no orders will be placed'}
          </p>
        </div>

        {/* Toggle switch */}
        <button
          onClick={handleToggle}
          disabled={busy || (!ibkrConnected && !simMode)}
          aria-label="Toggle automated trading"
          className={clsx(
            'relative w-14 h-7 rounded-full border-2 transition-all duration-200 focus:outline-none',
            'disabled:opacity-40 disabled:cursor-not-allowed',
            botRunning
              ? 'bg-green-600 border-green-300 shadow-glow-green'
              : 'bg-gray-100 border-gray-200',
          )}
        >
          <span
            className={clsx(
              'absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform duration-200',
              botRunning ? 'translate-x-7' : 'translate-x-0.5',
            )}
          />
        </button>
      </div>

      {/* Live cycle stats strip */}
      {hasStats && (
        <div className="border-t border-green-300/20 px-4 py-2 flex flex-wrap items-center gap-4 text-[10px] font-mono">
          <div className="flex items-center gap-1.5">
            <span className="text-gray-500 uppercase tracking-wider">Rules</span>
            <span className="text-gray-700 font-semibold">{cycleStats.rulesEnabled}</span>
          </div>
          {cycleStats.symbolsScanned > 0 && (
            <div className="flex items-center gap-1.5">
              <span className="text-gray-500 uppercase tracking-wider">Symbols</span>
              <span className="text-gray-700 font-semibold">{cycleStats.symbolsScanned.toLocaleString()}</span>
            </div>
          )}
          <div className="flex items-center gap-1.5">
            <span className="text-gray-500 uppercase tracking-wider">Signals</span>
            <span className={clsx(
              'font-semibold',
              cycleStats.signals > 0 ? 'text-emerald-600' : 'text-gray-400',
            )}>
              {cycleStats.signals}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-gray-500 uppercase tracking-wider">Last Run</span>
            <span className="text-gray-600">{fmtTime(cycleStats.lastRun)}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-gray-500 uppercase tracking-wider">Next</span>
            <span className="text-gray-600">{fmtTime(cycleStats.nextRun)}</span>
          </div>
        </div>
      )}
    </div>
  )
}
