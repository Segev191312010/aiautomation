import React from 'react'
import clsx from 'clsx'
import { useBotStore, useMarketStore, useStockProfileStore, useUIStore } from '@/store'
import { connectIBKR, startBot, stopBot } from '@/services/api'
import AlertBell from '@/components/alerts/AlertBell'
import { SHORTCUT_FOCUS_SEARCH } from '@/hooks/useKeyboardShortcuts'
import type { ThemePreference } from '@/store'

const PAGE_LABELS: Record<string, string> = {
  dashboard:  'Dashboard',
  tradebot:   'TradeBot',
  market:     'Market',
  screener:   'Screener',
  stock:      'Stock Analysis',
  backtest:   'Backtest',
  simulation: 'Simulation',
  rules:      'Rules',
  alerts:     'Alerts & Notifications',
  settings:   'Settings',
}

export default function Header() {
  const { activeRoute, setRoute, theme, setTheme } = useUIStore()
  const { ibkrConnected, botRunning, simMode, setBotRunning, setIBKR } = useBotStore()
  const selectedSymbol = useMarketStore((s) => s.selectedSymbol)
  const setSelectedSymbol = useMarketStore((s) => s.setSelectedSymbol)
  const setProfileSymbol = useStockProfileStore((s) => s.setSymbol)
  const [searchInput, setSearchInput] = React.useState(selectedSymbol)
  const searchRef = React.useRef<HTMLInputElement>(null)

  React.useEffect(() => {
    setSearchInput(selectedSymbol)
  }, [selectedSymbol])

  // Ctrl+K → focus search input
  React.useEffect(() => {
    const handler = () => searchRef.current?.focus()
    window.addEventListener(SHORTCUT_FOCUS_SEARCH, handler)
    return () => window.removeEventListener(SHORTCUT_FOCUS_SEARCH, handler)
  }, [])

  const cycleTheme = () => {
    const next: Record<ThemePreference, ThemePreference> = { light: 'dark', dark: 'system', system: 'light' }
    setTheme(next[theme])
  }

  const resolvedTheme = document.documentElement.getAttribute('data-theme') ?? 'light'

  const commitTicker = React.useCallback((route: 'market' | 'stock') => {
    const symbol = searchInput.trim().toUpperCase()
    if (!symbol) return
    setSelectedSymbol(symbol)
    setProfileSymbol(symbol)
    setRoute(route)
  }, [searchInput, setProfileSymbol, setRoute, setSelectedSymbol])

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
    <header className="flex items-center h-14 px-5 shrink-0 bg-zinc-900 border-b border-[#E8E4DF] gap-4">
      <div className="min-w-0 shrink-0">
        <div className="text-[10px] font-sans uppercase tracking-[0.22em] text-zinc-500">
          Workspace
        </div>
        <h1 className="text-sm font-mono font-semibold text-zinc-50 tracking-wide">
          {PAGE_LABELS[activeRoute] ?? 'Dashboard'}
        </h1>
      </div>

      <div className="flex-1 flex justify-center min-w-0">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            commitTicker('market')
          }}
          className="flex w-full max-w-[38rem] items-center gap-2"
        >
          <div className="relative flex-1 min-w-0">
            <svg
              className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-500 pointer-events-none"
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="6.5" cy="6.5" r="4.5" />
              <path d="M10.5 10.5L14 14" />
            </svg>
            <input
              ref={searchRef}
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value.toUpperCase())}
              placeholder="Jump to ticker… (Ctrl+K)"
              className="w-full pl-9 pr-3 py-2 text-xs font-mono placeholder:text-[var(--text-muted)] bg-[var(--bg-input)] rounded-lg border border-[var(--border)] text-[var(--text-primary)] outline-none focus:border-[var(--accent)] transition-colors"
            />
          </div>
          <button
            type="submit"
            className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-[11px] font-sans font-medium text-white transition-colors hover:bg-zinc-900"
          >
            Market
          </button>
          <button
            type="button"
            onClick={() => commitTicker('stock')}
            className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-[11px] font-sans font-medium text-zinc-200 transition-colors hover:border-zinc-700 hover:text-zinc-50"
          >
            Analysis
          </button>
        </form>
      </div>

      <div className="flex items-center gap-3 shrink-0">
        {simMode && (
          <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-1 text-[10px] font-mono text-amber-700 tracking-wider">
            SIM
          </span>
        )}

        <AlertBell />

        <button
          onClick={handleConnectIBKR}
          title={ibkrConnected ? 'IBKR connected' : 'Click to connect IBKR'}
          className="flex items-center gap-1.5 text-[11px] font-mono text-zinc-400 hover:text-zinc-100 transition-colors"
        >
          <span
            className={clsx(
              'w-1.5 h-1.5 rounded-full',
              ibkrConnected ? 'bg-emerald-500' : 'bg-red-400',
            )}
          />
          IBKR
        </button>

        <button
          onClick={handleBotToggle}
          title={botRunning ? 'Stop bot' : 'Start bot'}
          className="flex items-center gap-1.5 text-[11px] font-mono text-zinc-400 hover:text-zinc-100 transition-colors"
        >
          <span
            className={clsx(
              'w-1.5 h-1.5 rounded-full',
              botRunning ? 'bg-emerald-500' : 'bg-zinc-700',
            )}
          />
          BOT
        </button>

        <Clock />
      </div>
    </header>
  )
}

function Clock() {
  const [time, setTime] = React.useState(new Date())

  React.useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1_000)
    return () => clearInterval(t)
  }, [])

  const datePart = time.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  const timePart = time.toLocaleTimeString('en-US', { hour12: false })

  return (
    <span className="text-[11px] font-mono text-zinc-500 tabular-nums whitespace-nowrap">
      {datePart} {timePart}
    </span>
  )
}
