/**
 * BotToggle — prominent "Automated Trading" master switch.
 * Confirms before enabling if operating with real money.
 *
 * Works in three modes:
 *  - LIVE: IBKR connected — requires confirmation before enabling
 *  - SIMULATION: virtual account, no real orders
 *  - MOCK: backend offline / demo mode — toggle stays interactive client-side
 */
import React, { useState } from 'react'
import clsx from 'clsx'
import { useBotStore } from '@/store'
import { startBot, stopBot } from '@/services/api'

export default function BotToggle() {
  const { botRunning, ibkrConnected, simMode, mockMode, setBotRunning } = useBotStore()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const handleToggle = async () => {
    if (busy) return

    // Safety confirmation for live trading
    if (!botRunning && !simMode && !mockMode && ibkrConnected) {
      const ok = window.confirm(
        '⚠️  You are about to enable automated trading with a LIVE account.\n\nRules will place real orders. Continue?',
      )
      if (!ok) return
    }

    setBusy(true)
    setError('')
    const next = !botRunning
    try {
      if (botRunning) {
        await stopBot()
      } else {
        await startBot()
      }
      setBotRunning(next)
    } catch (e) {
      // Backend offline (demo / mock mode): keep the toggle responsive
      // client-side so the user can still explore the UI.
      if (mockMode) {
        setBotRunning(next)
      } else {
        setError(e instanceof Error ? e.message : 'Failed to toggle bot')
        console.error(e)
      }
    } finally {
      setBusy(false)
    }
  }

  // Toggle is disabled only when no mode is available at all.
  const disabled = busy || (!ibkrConnected && !simMode && !mockMode)

  return (
    <div
      className={clsx(
        'flex items-center justify-between p-4 rounded-lg border transition-all',
        botRunning
          ? 'bg-terminal-green/5 border-terminal-green/30'
          : 'bg-terminal-surface border-terminal-border',
      )}
    >
      <div>
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-terminal-text">Automated Trading</span>
          {simMode && (
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-terminal-amber/15 text-terminal-amber">
              SIMULATION
            </span>
          )}
          {!simMode && mockMode && (
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-terminal-amber/15 text-terminal-amber">
              MOCK
            </span>
          )}
          {!simMode && !mockMode && !ibkrConnected && (
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-terminal-red/15 text-terminal-red">
              IBKR OFFLINE
            </span>
          )}
        </div>
        <p className="text-xs text-terminal-dim mt-0.5">
          {botRunning
            ? mockMode
              ? 'Mock bot active — no real orders will be placed'
              : 'Rules engine is active — monitoring markets'
            : 'Bot is stopped — no orders will be placed'}
        </p>
        {error && (
          <p className="text-xs text-terminal-red mt-1 font-mono">{error}</p>
        )}
      </div>

      {/* Toggle switch */}
      <button
        onClick={handleToggle}
        disabled={disabled}
        aria-label="Toggle automated trading"
        className={clsx(
          'relative w-14 h-7 rounded-full border-2 transition-all duration-200 focus:outline-none',
          'disabled:opacity-40 disabled:cursor-not-allowed',
          botRunning
            ? 'bg-terminal-green border-terminal-green shadow-glow-green'
            : 'bg-terminal-muted border-terminal-border',
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
  )
}
