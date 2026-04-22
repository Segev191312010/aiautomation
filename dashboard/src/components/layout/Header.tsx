import React from 'react'
import { useLocation } from 'react-router-dom'
import clsx from 'clsx'
import { useBotStore, useMarketStore, useStockProfileStore, useUIStore } from '@/store'
import { connectIBKR, startBot, stopBot } from '@/services/api'
import AlertBell from '@/components/alerts/AlertBell'
import { SHORTCUT_FOCUS_SEARCH } from '@/hooks/useKeyboardShortcuts'
import type { ThemePreference } from '@/store'
import type { AppRoute } from '@/types'
import { getRouteFromPath, navigateToRoute } from '@/utils/routes'

const PAGE_META: Record<AppRoute, { eyebrow: string; title: string; description: string }> = {
  dashboard: {
    eyebrow: 'Control Room',
    title: 'Desk Overview',
    description: 'Jump into live market context, portfolio posture, and the next action without leaving the shell.',
  },
  tradebot: {
    eyebrow: 'Execution',
    title: 'TradeBot Console',
    description: 'Monitor positions, toggle automation, and review intraday activity from one command surface.',
  },
  market: {
    eyebrow: 'Price Action',
    title: 'Market Workspace',
    description: 'Full chart workspace with overlays, live data, and fast symbol switching.',
  },
  charts: {
    eyebrow: 'Visuals',
    title: 'Chart Studio',
    description: 'Focused chart views for cleaner pattern work and faster inspection.',
  },
  rotation: {
    eyebrow: 'Leadership',
    title: 'Rotation Monitor',
    description: 'Track sector leadership shifts, heat, and momentum rotation without leaving the desk.',
  },
  screener: {
    eyebrow: 'Discovery',
    title: 'Screener Lab',
    description: 'Run broad scans, compare opportunities, and move straight into chart or profile flows.',
  },
  swing: {
    eyebrow: 'Discovery',
    title: 'Swing Screener',
    description: 'Market metrics, guru-inspired scans, and breadth analysis for swing trade setups.',
  },
  stock: {
    eyebrow: 'Research',
    title: 'Equity Profile',
    description: 'Company context, financials, and analyst signals around the active symbol.',
  },
  simulation: {
    eyebrow: 'Practice',
    title: 'Simulation Arena',
    description: 'Replay and paper-trade with the same controls used in the live workflow.',
  },
  backtest: {
    eyebrow: 'Validation',
    title: 'Backtest Bench',
    description: 'Stress strategies, inspect performance, and compare outcomes before promoting ideas.',
  },
  rules: {
    eyebrow: 'Logic',
    title: 'Rules Engine',
    description: 'Shape the condition layer that drives automation, alerts, and trade decisions.',
  },
  alerts: {
    eyebrow: 'Signal Flow',
    title: 'Alert Center',
    description: 'Own notification rules, triggered history, and delivery preferences in one place.',
  },
  analytics: {
    eyebrow: 'Performance',
    title: 'Portfolio Analytics',
    description: 'Measure risk, exposure, and realized outcomes without faking unavailable sections.',
  },
  advisor: {
    eyebrow: 'Autonomy',
    title: 'AI Autopilot',
    description: 'Review operator guardrails, interventions, and decision traces from a single console.',
  },
  settings: {
    eyebrow: 'Preferences',
    title: 'System Settings',
    description: 'Tune platform behavior, data defaults, and execution preferences for the desk.',
  },
}

function formatPrice(value?: number): string {
  if (value == null) return '--'
  return `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function formatChange(value?: number): string {
  if (value == null) return '--'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

function themeCopy(theme: ThemePreference) {
  if (theme === 'dark') return 'Night'
  if (theme === 'light') return 'Day'
  return 'Auto'
}

function ThemeGlyph({ theme }: { theme: ThemePreference }) {
  if (theme === 'dark') {
    return (
      <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4">
        <path
          d="M13.8 2.6a6.7 6.7 0 1 0 3.6 11.9A7.8 7.8 0 0 1 13.8 2.6Z"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    )
  }

  if (theme === 'light') {
    return (
      <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4">
        <circle cx="10" cy="10" r="3.5" stroke="currentColor" strokeWidth="1.5" />
        <path d="M10 1.5V4M10 16v2.5M1.5 10H4M16 10h2.5M3.7 3.7l1.8 1.8M14.5 14.5l1.8 1.8M3.7 16.3l1.8-1.8M14.5 5.5l1.8-1.8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    )
  }

  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4">
      <path d="M10 3v14M3 10h14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  )
}

export default function Header() {
  const location = useLocation()
  const { theme, setTheme } = useUIStore()
  const { ibkrConnected, botRunning, simMode, setBotRunning, setIBKR } = useBotStore()
  const selectedSymbol = useMarketStore((s) => s.selectedSymbol)
  const selectedQuote = useMarketStore((s) => s.quotes[selectedSymbol])
  const setSelectedSymbol = useMarketStore((s) => s.setSelectedSymbol)
  const setProfileSymbol = useStockProfileStore((s) => s.setSymbol)
  const [searchInput, setSearchInput] = React.useState(selectedSymbol)
  const activeRoute = getRouteFromPath(location.pathname)
  const searchRef = React.useRef<HTMLInputElement>(null)

  React.useEffect(() => {
    setSearchInput(selectedSymbol)
  }, [selectedSymbol])

  React.useEffect(() => {
    const handler = () => searchRef.current?.focus()
    window.addEventListener(SHORTCUT_FOCUS_SEARCH, handler)
    return () => window.removeEventListener(SHORTCUT_FOCUS_SEARCH, handler)
  }, [])

  const cycleTheme = () => {
    const next: Record<ThemePreference, ThemePreference> = { light: 'dark', dark: 'system', system: 'light' }
    setTheme(next[theme])
  }

  const commitTicker = React.useCallback((route: 'market' | 'stock') => {
    const symbol = searchInput.trim().toUpperCase()
    if (!symbol) return
    setSelectedSymbol(symbol)
    setProfileSymbol(symbol)
    navigateToRoute(route)
  }, [searchInput, setProfileSymbol, setSelectedSymbol])

  const handleConnectIBKR = async () => {
    try {
      const response = await connectIBKR()
      setIBKR(response.connected)
    } catch (error) {
      console.error(error)
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
    } catch (error) {
      console.error(error)
    }
  }

  const pageMeta = PAGE_META[activeRoute] ?? PAGE_META.dashboard

  return (
    <header className="relative z-20 px-4 pb-4 pt-4 sm:px-6 lg:px-8">
      <div className="shell-panel relative overflow-hidden px-4 py-4 sm:px-5">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(245,158,11,0.12),transparent_34%)]" />
        <div
          className="pointer-events-none absolute inset-x-10 top-0 h-px bg-gradient-to-r from-transparent to-transparent"
          style={{ backgroundImage: 'linear-gradient(90deg, transparent, var(--accent), transparent)' }}
        />

        <div className="relative flex flex-col gap-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0 max-w-3xl">
              <div className="shell-kicker">{pageMeta.eyebrow}</div>
              <div className="mt-2 flex flex-wrap items-center gap-2.5">
                <h1 className="display-font text-[1.9rem] leading-none text-[var(--text-primary)] sm:text-[2.2rem]">
                  {pageMeta.title}
                </h1>
                <span className="shell-chip text-[11px] font-medium">
                  <span className="h-2 w-2 rounded-full bg-[var(--accent)]" />
                  Active {selectedSymbol}
                </span>
                <span className="shell-chip text-[11px] font-medium">
                  {formatPrice(selectedQuote?.price)}
                  <span className={clsx(
                    'font-mono',
                    (selectedQuote?.change_pct ?? 0) >= 0 ? 'text-[var(--success)]' : 'text-[var(--danger)]',
                  )}>
                    {formatChange(selectedQuote?.change_pct)}
                  </span>
                </span>
              </div>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--text-secondary)]">
                {pageMeta.description}
              </p>
            </div>

            <div className="flex flex-wrap items-center justify-end gap-2">
              {simMode && (
                <span className="shell-chip border-[rgba(245,158,11,0.28)] bg-[rgba(245,158,11,0.12)] text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--accent)]">
                  Simulation
                </span>
              )}

              <div className="shell-chip px-3 py-2 text-[11px]">
                <Clock />
              </div>

              <button
                type="button"
                onClick={cycleTheme}
                className="shell-chip px-3 py-2 text-[11px] font-semibold transition-colors hover:text-[var(--text-primary)]"
                title="Cycle theme"
              >
                <ThemeGlyph theme={theme} />
                Theme {themeCopy(theme)}
              </button>

              <button
                type="button"
                onClick={handleConnectIBKR}
                className={clsx(
                  'shell-chip px-3 py-2 text-[11px] font-semibold transition-colors',
                  ibkrConnected ? 'text-[var(--success)]' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
                )}
                title={ibkrConnected ? 'IBKR connected' : 'Connect IBKR'}
              >
                <span className={clsx('h-2 w-2 rounded-full', ibkrConnected ? 'bg-[var(--success)]' : 'bg-[var(--danger)]')} />
                {ibkrConnected ? 'IBKR live' : 'Connect IBKR'}
              </button>

              <button
                type="button"
                onClick={handleBotToggle}
                className={clsx(
                  'shell-chip px-3 py-2 text-[11px] font-semibold transition-colors',
                  botRunning ? 'text-[var(--success)]' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
                )}
                title={botRunning ? 'Stop bot' : 'Start bot'}
              >
                <span className={clsx('h-2 w-2 rounded-full', botRunning ? 'bg-[var(--success)]' : 'bg-[var(--text-muted)]')} />
                {botRunning ? 'Bot active' : 'Bot idle'}
              </button>

              <div className="shell-chip px-2.5 py-2">
                <AlertBell />
              </div>
            </div>
          </div>

          <div className="flex flex-wrap gap-3">
            <form
              onSubmit={(event) => {
                event.preventDefault()
                commitTicker('market')
              }}
              className="flex min-w-0 flex-1 flex-wrap items-center gap-2"
            >
              <div className="relative min-w-[16rem] flex-1">
                <svg
                  className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-muted)]"
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
                  onChange={(event) => setSearchInput(event.target.value.toUpperCase())}
                  placeholder="Jump to ticker... (Ctrl+K)"
                  className="w-full rounded-2xl border border-[var(--border)] bg-[var(--bg-input)] py-3 pl-10 pr-4 text-sm font-medium text-[var(--text-primary)] outline-none transition-colors placeholder:text-[var(--text-muted)] focus:border-[var(--accent)]"
                />
              </div>

              <button
                type="submit"
                className="rounded-2xl bg-[var(--accent)] px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-[var(--accent-hover)]"
              >
                Open market
              </button>

              <button
                type="button"
                onClick={() => commitTicker('stock')}
                className="rounded-2xl border border-[var(--border)] bg-[var(--bg-hover)] px-4 py-3 text-sm font-semibold text-[var(--text-primary)] transition-colors hover:border-[var(--accent)] hover:text-[var(--accent)]"
              >
                Open profile
              </button>

                <button
                  type="button"
                  onClick={() => navigateToRoute('dashboard')}
                  className="rounded-2xl border border-[var(--border)] bg-transparent px-4 py-3 text-sm font-semibold text-[var(--text-secondary)] transition-colors hover:border-[var(--accent)] hover:text-[var(--text-primary)]"
                >
                  Command view
              </button>
            </form>
          </div>
        </div>
      </div>
    </header>
  )
}

function Clock() {
  const [time, setTime] = React.useState(new Date())

  React.useEffect(() => {
    const timer = setInterval(() => setTime(new Date()), 1_000)
    return () => clearInterval(timer)
  }, [])

  const datePart = time.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  const timePart = time.toLocaleTimeString('en-US', { hour12: false })

  return (
    <span className="whitespace-nowrap font-mono text-[11px] text-[var(--text-secondary)]">
      {datePart} {timePart}
    </span>
  )
}
