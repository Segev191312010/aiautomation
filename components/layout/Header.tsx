import React from 'react'
import clsx from 'clsx'
import { useBotStore, useUIStore } from '@/store'
import { connectIBKR, startBot, stopBot } from '@/services/api'

const PAGE_LABELS: Record<string, string> = {
  dashboard:  'Dashboard',
  tradebot:   'TradeBot Command Center',
  market:     'Market Analyzer',
  simulation: 'Simulation Engine',
  rules:      'Automation Rules',
  settings:   'Settings',
}

export default function Header() {
  const { activeRoute } = useUIStore()
  const { ibkrConnected, botRunning, simMode, mockMode, setBotRunning, setIBKR } = useBotStore()

  const handleConnectIBKR = async () => {
    try {
      const r = await connectIBKR()
      setIBKR(r.connected)
    } catch (e) {
      console.error(e)
    }
  }

  const handleBotToggle = async () => {
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
    }
  }

  return (
    <header className="flex items-center h-14 px-4 border-b border-terminal-border bg-terminal-surface shrink-0 gap-4">
      {/* ── Page title ─────────────────────────────────────────────── */}
      <h1 className="text-sm font-semibold text-terminal-text tracking-wide mr-auto">
        {PAGE_LABELS[activeRoute] ?? 'Dashboard'}
      </h1>

      {/* ── Mode badges ────────────────────────────────────────────── */}
      <div className="flex items-center gap-2">
        {mockMode && (
          <span className="text-[10px] font-mono px-2 py-0.5 rounded-full border border-terminal-amber/40 text-terminal-amber">
            MOCK DATA
          </span>
        )}
        {simMode && (
          <span className="text-[10px] font-mono px-2 py-0.5 rounded-full border border-terminal-amber/40 text-terminal-amber animate-pulse-slow">
            SIMULATION
          </span>
        )}
      </div>

      {/* ── IBKR connection ────────────────────────────────────────── */}
      <button
        onClick={handleConnectIBKR}
        title={ibkrConnected ? 'IBKR connected (click to reconnect)' : 'Click to connect IBKR'}
        className={clsx(
          'flex items-center gap-1.5 text-[11px] font-mono px-2.5 py-1 rounded border transition-colors',
          ibkrConnected
            ? 'border-terminal-green/40 text-terminal-green bg-terminal-green/5 hover:bg-terminal-green/10'
            : 'border-terminal-red/40 text-terminal-red bg-terminal-red/5 hover:bg-terminal-red/10',
        )}
      >
        <span
          className={clsx(
            'w-1.5 h-1.5 rounded-full',
            ibkrConnected ? 'bg-terminal-green animate-pulse' : 'bg-terminal-red',
          )}
        />
        IBKR
      </button>

      {/* ── Bot toggle ─────────────────────────────────────────────── */}
      <button
        onClick={handleBotToggle}
        title={botRunning ? 'Stop bot' : 'Start bot'}
        className={clsx(
          'flex items-center gap-1.5 text-[11px] font-mono px-2.5 py-1 rounded border transition-colors',
          botRunning
            ? 'border-terminal-green/40 text-terminal-green bg-terminal-green/5 hover:bg-terminal-green/10'
            : 'border-terminal-border text-terminal-dim bg-terminal-muted hover:text-terminal-text',
        )}
      >
        <span
          className={clsx(
            'w-1.5 h-1.5 rounded-full',
            botRunning ? 'bg-terminal-green animate-pulse' : 'bg-terminal-dim',
          )}
        />
        BOT
      </button>

      {/* ── Clock ──────────────────────────────────────────────────── */}
      <Clock />
    </header>
  )
}

function Clock() {
  const [time, setTime] = React.useState(new Date())
  React.useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1_000)
    return () => clearInterval(t)
  }, [])
  return (
    <span className="text-[11px] font-mono text-terminal-dim tabular-nums">
      {time.toLocaleTimeString('en-US', { hour12: false })}
    </span>
  )
}
