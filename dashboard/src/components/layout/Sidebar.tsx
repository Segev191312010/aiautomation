import React from 'react'
import clsx from 'clsx'
import { useUIStore, useMarketStore, useBotStore } from '@/store'
import type { AppRoute } from '@/types'

// ── Nav items ─────────────────────────────────────────────────────────────────

interface NavItem {
  route: AppRoute
  label: string
  icon:  React.ReactNode
}

const NAV_ITEMS: NavItem[] = [
  {
    route: 'dashboard',
    label: 'Dashboard',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
        <path d="M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z" />
      </svg>
    ),
  },
  {
    route: 'tradebot',
    label: 'TradeBot',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
        <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14H9V8h2v8zm4 0h-2V8h2v8z" />
      </svg>
    ),
  },
  {
    route: 'market',
    label: 'Market',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
        <path d="M3.5 18.49l6-6.01 4 4L22 6.92l-1.41-1.41-7.09 7.97-4-4L2 16.99z" />
      </svg>
    ),
  },
  {
    route: 'screener',
    label: 'Screener',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
        <path d="M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z" />
      </svg>
    ),
  },
  {
    route: 'simulation',
    label: 'Simulation',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
        <path d="M8 5v14l11-7z" />
      </svg>
    ),
  },
  {
    route: 'backtest',
    label: 'Backtest',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
        <path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z" />
      </svg>
    ),
  },
  {
    route: 'rules',
    label: 'Rules',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
        <path d="M3 18h6v-2H3v2zM3 6v2h18V6H3zm0 7h12v-2H3v2z" />
      </svg>
    ),
  },
  {
    route: 'settings',
    label: 'Settings',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
        <path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z" />
      </svg>
    ),
  },
]

// ── Component ─────────────────────────────────────────────────────────────────

export default function Sidebar() {
  const sidebarCollapsed  = useUIStore((s) => s.sidebarCollapsed)
  const toggleSidebar     = useUIStore((s) => s.toggleSidebar)
  const activeRoute       = useUIStore((s) => s.activeRoute)
  const setRoute          = useUIStore((s) => s.setRoute)
  const quotes            = useMarketStore((s) => s.quotes)
  const watchlists        = useMarketStore((s) => s.watchlists)
  const activeWatchlist   = useMarketStore((s) => s.activeWatchlist)
  const setSelectedSymbol = useMarketStore((s) => s.setSelectedSymbol)
  const ibkrConnected     = useBotStore((s) => s.ibkrConnected)
  const simMode           = useBotStore((s) => s.simMode)
  const botRunning        = useBotStore((s) => s.botRunning)

  const watchlist = watchlists.find((w) => w.id === activeWatchlist)

  const width = sidebarCollapsed ? 'w-[60px]' : 'w-[220px]'

  return (
    <aside
      className={clsx(
        'flex flex-col shrink-0 h-screen bg-terminal-surface border-r border-terminal-border',
        'transition-all duration-200 overflow-hidden',
        width,
      )}
    >
      {/* ── Logo / collapse toggle ────────────────────────────────── */}
      <div className="flex items-center h-14 px-3 border-b border-terminal-border shrink-0">
        {!sidebarCollapsed && (
          <div className="flex items-center gap-2 mr-auto">
            <span className="w-7 h-7 rounded bg-terminal-blue/20 flex items-center justify-center">
              <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4 text-terminal-blue">
                <path d="M3.5 18.49l6-6.01 4 4L22 6.92l-1.41-1.41-7.09 7.97-4-4L2 16.99z" />
              </svg>
            </span>
            <span className="font-mono font-semibold text-sm text-terminal-text tracking-wider">
              TRADEBOT
            </span>
          </div>
        )}
        <button
          onClick={toggleSidebar}
          className="p-1.5 rounded text-terminal-dim hover:text-terminal-text hover:bg-terminal-muted transition-colors"
          aria-label="Toggle sidebar"
        >
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
            {sidebarCollapsed ? (
              <path d="M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z" />
            ) : (
              <path d="M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z" />
            )}
          </svg>
        </button>
      </div>

      {/* ── Status pills ──────────────────────────────────────────── */}
      {!sidebarCollapsed && (
        <div className="flex gap-1.5 px-3 py-2 border-b border-terminal-border">
          <span
            className={clsx(
              'text-[10px] font-mono px-1.5 py-0.5 rounded font-semibold',
              ibkrConnected
                ? 'bg-terminal-green/15 text-terminal-green'
                : 'bg-terminal-red/15 text-terminal-red',
            )}
          >
            IBKR
          </span>
          <span
            className={clsx(
              'text-[10px] font-mono px-1.5 py-0.5 rounded font-semibold',
              botRunning
                ? 'bg-terminal-green/15 text-terminal-green'
                : 'bg-terminal-muted text-terminal-dim',
            )}
          >
            BOT
          </span>
          {simMode && (
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded font-semibold bg-terminal-amber/15 text-terminal-amber">
              SIM
            </span>
          )}
        </div>
      )}

      {/* ── Navigation ────────────────────────────────────────────── */}
      <nav className="flex flex-col gap-0.5 px-2 pt-3">
        {NAV_ITEMS.map((item) => {
          const active = activeRoute === item.route
          return (
            <button
              key={item.route}
              onClick={() => setRoute(item.route)}
              title={sidebarCollapsed ? item.label : undefined}
              className={clsx(
                'flex items-center gap-3 px-2 py-2 rounded text-sm transition-colors text-left w-full',
                active
                  ? 'bg-terminal-blue/15 text-terminal-blue'
                  : 'text-terminal-dim hover:text-terminal-text hover:bg-terminal-muted',
              )}
            >
              <span className="shrink-0">{item.icon}</span>
              {!sidebarCollapsed && (
                <span className="truncate font-medium">{item.label}</span>
              )}
            </button>
          )
        })}
      </nav>

      {/* ── Quick watchlist ───────────────────────────────────────── */}
      {!sidebarCollapsed && watchlist && (
        <div className="mt-4 flex-1 overflow-y-auto px-2 min-h-0">
          <p className="text-[10px] font-mono text-terminal-ghost px-2 mb-1 uppercase tracking-widest">
            {watchlist.name}
          </p>
          {watchlist.symbols.map((sym) => {
            const q = quotes[sym]
            const up = q && q.change_pct >= 0
            return (
              <button
                key={sym}
                onClick={() => {
                  setSelectedSymbol(sym)
                  setRoute('market')
                }}
                className="flex items-center justify-between w-full px-2 py-1.5 rounded hover:bg-terminal-muted transition-colors group"
              >
                <span className="text-xs font-mono text-terminal-text group-hover:text-terminal-blue">
                  {sym}
                </span>
                {q && (
                  <div className="text-right">
                    <div className="text-xs font-mono text-terminal-text">{q.price.toFixed(2)}</div>
                    <div
                      className={clsx(
                        'text-[10px] font-mono',
                        up ? 'text-terminal-green' : 'text-terminal-red',
                      )}
                    >
                      {up ? '+' : ''}{q.change_pct.toFixed(2)}%
                    </div>
                  </div>
                )}
              </button>
            )
          })}
        </div>
      )}

      {/* ── Bottom spacer ─────────────────────────────────────────── */}
      <div className="h-4 shrink-0" />
    </aside>
  )
}
