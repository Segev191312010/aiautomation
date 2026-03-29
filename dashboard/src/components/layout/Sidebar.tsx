import React, { useState } from 'react'
import clsx from 'clsx'
import { useUIStore, useMarketStore, useBotStore, useStockProfileStore } from '@/store'
import type { AppRoute } from '@/types'

// ── Nav group definitions ─────────────────────────────────────────────────────

interface NavChild {
  route: AppRoute
  label: string
  icon: React.ReactNode
}

interface NavGroup {
  id: string
  label: string
  icon: React.ReactNode
  children: NavChild[]
}

// Shared icon size class
const IC = 'w-[18px] h-[18px]'

const NAV_GROUPS: NavGroup[] = [
  {
    id: 'dashboard',
    label: 'Dashboard',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className={IC}>
        <path d="M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z" />
      </svg>
    ),
    children: [
      {
        route: 'dashboard',
        label: 'Dashboard',
        icon: (
          <svg viewBox="0 0 24 24" fill="currentColor" className={IC}>
            <path d="M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z" />
          </svg>
        ),
      },
    ],
  },
  {
    id: 'trading',
    label: 'Trading',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className={IC}>
        <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14H9V8h2v8zm4 0h-2V8h2v8z" />
      </svg>
    ),
    children: [
      {
        route: 'tradebot',
        label: 'TradeBot',
        icon: (
          <svg viewBox="0 0 24 24" fill="currentColor" className={IC}>
            <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14H9V8h2v8zm4 0h-2V8h2v8z" />
          </svg>
        ),
      },
      {
        route: 'advisor',
        label: 'Autopilot',
        icon: (
          <svg viewBox="0 0 24 24" fill="currentColor" className={IC}>
            <path d="M12 2a3 3 0 00-3 3v1H7a3 3 0 00-3 3v2a5 5 0 001 2.97V19a3 3 0 003 3h8a3 3 0 003-3v-5.03A5 5 0 0020 11V9a3 3 0 00-3-3h-2V5a3 3 0 00-3-3zm-1 4V5a1 1 0 112 0v1h-2zm-2 8a1.5 1.5 0 113 0 1.5 1.5 0 01-3 0zm6 0a1.5 1.5 0 113 0 1.5 1.5 0 01-3 0zM8 9h8a1 1 0 011 1v1a3 3 0 01-3 3H10a3 3 0 01-3-3v-1a1 1 0 011-1z" />
          </svg>
        ),
      },
    ],
  },
  {
    id: 'markets',
    label: 'Markets',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className={IC}>
        <path d="M3.5 18.49l6-6.01 4 4L22 6.92l-1.41-1.41-7.09 7.97-4-4L2 16.99z" />
      </svg>
    ),
    children: [
      {
        route: 'market',
        label: 'Market',
        icon: (
          <svg viewBox="0 0 24 24" fill="currentColor" className={IC}>
            <path d="M3.5 18.49l6-6.01 4 4L22 6.92l-1.41-1.41-7.09 7.97-4-4L2 16.99z" />
          </svg>
        ),
      },
      {
        route: 'charts',
        label: 'Charts',
        icon: (
          <svg viewBox="0 0 24 24" fill="currentColor" className={IC}>
            <path d="M4 9h4v11H4zm6-4h4v15h-4zm6 8h4v7h-4z" />
          </svg>
        ),
      },
      {
        route: 'rotation',
        label: 'Rotation',
        icon: (
          <svg viewBox="0 0 24 24" fill="currentColor" className={IC}>
            <path d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46A7.93 7.93 0 0020 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74A7.93 7.93 0 004 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z" />
          </svg>
        ),
      },
      {
        route: 'screener',
        label: 'Screener',
        icon: (
          <svg viewBox="0 0 24 24" fill="currentColor" className={IC}>
            <path d="M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z" />
          </svg>
        ),
      },
      {
        route: 'stock',
        label: 'Stock',
        icon: (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className={IC}>
            <circle cx="12" cy="12" r="10" />
            <path d="M12 16v-4M12 8h.01" />
          </svg>
        ),
      },
    ],
  },
  {
    id: 'analysis',
    label: 'Analysis',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className={IC}>
        <path d="M5 9h2v11H5V9zm4-5h2v16H9V4zm4 8h2v8h-2v-8zm4-4h2v12h-2V8z" />
      </svg>
    ),
    children: [
      {
        route: 'analytics',
        label: 'Analytics',
        icon: (
          <svg viewBox="0 0 24 24" fill="currentColor" className={IC}>
            <path d="M5 9h2v11H5V9zm4-5h2v16H9V4zm4 8h2v8h-2v-8zm4-4h2v12h-2V8z" />
          </svg>
        ),
      },
      {
        route: 'backtest',
        label: 'Backtest',
        icon: (
          <svg viewBox="0 0 24 24" fill="currentColor" className={IC}>
            <path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z" />
          </svg>
        ),
      },
      {
        route: 'simulation',
        label: 'Simulation',
        icon: (
          <svg viewBox="0 0 24 24" fill="currentColor" className={IC}>
            <path d="M8 5v14l11-7z" />
          </svg>
        ),
      },
    ],
  },
  {
    id: 'system',
    label: 'System',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className={IC}>
        <path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z" />
      </svg>
    ),
    children: [
      {
        route: 'alerts',
        label: 'Alerts',
        icon: (
          <svg viewBox="0 0 24 24" fill="currentColor" className={IC}>
            <path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.89 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z" />
          </svg>
        ),
      },
      {
        route: 'settings',
        label: 'Settings',
        icon: (
          <svg viewBox="0 0 24 24" fill="currentColor" className={IC}>
            <path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z" />
          </svg>
        ),
      },
    ],
  },
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatPrice(value?: number): string {
  if (value == null) return '--'
  return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function findGroupForRoute(route: AppRoute): string | null {
  for (const g of NAV_GROUPS) {
    if (g.children.some((c) => c.route === route)) return g.id
  }
  return null
}

// ── Chevron icon ──────────────────────────────────────────────────────────────

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      className={clsx('w-3 h-3 transition-transform duration-150 shrink-0', open ? 'rotate-90' : 'rotate-0')}
    >
      <path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z" />
    </svg>
  )
}

// ── Sidebar ───────────────────────────────────────────────────────────────────

export default function Sidebar() {
  const sidebarCollapsed  = useUIStore((s) => s.sidebarCollapsed)
  const toggleSidebar     = useUIStore((s) => s.toggleSidebar)
  const activeRoute       = useUIStore((s) => s.activeRoute)
  const setRoute          = useUIStore((s) => s.setRoute)
  const quotes            = useMarketStore((s) => s.quotes)
  const watchlists        = useMarketStore((s) => s.watchlists)
  const activeWatchlist   = useMarketStore((s) => s.activeWatchlist)
  const selectedSymbol    = useMarketStore((s) => s.selectedSymbol)
  const setSelectedSymbol = useMarketStore((s) => s.setSelectedSymbol)
  const setProfileSymbol  = useStockProfileStore((s) => s.setSymbol)
  const ibkrConnected     = useBotStore((s) => s.ibkrConnected)
  const botRunning        = useBotStore((s) => s.botRunning)

  // Track which groups are open. Default: open the group that owns the active route.
  const [openGroups, setOpenGroups] = useState<Set<string>>(() => {
    const activeGroup = findGroupForRoute(activeRoute)
    return new Set(activeGroup ? [activeGroup] : ['dashboard'])
  })

  const toggleGroup = (id: string) => {
    setOpenGroups((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  const handleChildClick = (route: AppRoute, groupId: string) => {
    setRoute(route)
    // Ensure the group stays open when navigating into it
    setOpenGroups((prev) => {
      if (prev.has(groupId)) return prev
      return new Set([...prev, groupId])
    })
  }

  const watchlist   = watchlists.find((w) => w.id === activeWatchlist)
  const selectedQuote = quotes[selectedSymbol]
  const width = sidebarCollapsed ? 'w-[56px]' : 'w-[232px]'

  const jumpTo = (route: 'market' | 'stock', symbol: string) => {
    setSelectedSymbol(symbol)
    setProfileSymbol(symbol)
    setRoute(route)
  }

  return (
    <aside
      className={clsx(
        'flex flex-col shrink-0 h-screen bg-[var(--bg-sidebar)] border-r border-[var(--border)]',
        'transition-all duration-200 overflow-hidden',
        width,
      )}
    >
      {/* ── Logo / collapse toggle ──────────────────────────────────────────── */}
      <div className="flex items-center h-14 px-3 shrink-0 border-b border-[var(--border)]">
        {!sidebarCollapsed ? (
          <>
            <div className="flex items-center gap-2 mr-auto">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--bg-hover)]">
                <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4 text-[var(--text-primary)]">
                  <path d="M3.5 18.49l6-6.01 4 4L22 6.92l-1.41-1.41-7.09 7.97-4-4L2 16.99z" />
                </svg>
              </div>
              <div>
                <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-[var(--text-muted)]">TradeBot</div>
                <div className="text-xs font-mono font-semibold text-[var(--text-primary)]">Market Desk</div>
              </div>
            </div>
            <button
              onClick={toggleSidebar}
              className="p-1 rounded text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors shrink-0"
              aria-label="Toggle sidebar"
            >
              <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
                <path d="M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z" />
              </svg>
            </button>
          </>
        ) : (
          <button
            onClick={toggleSidebar}
            className="mx-auto flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--bg-hover)] text-[var(--text-primary)]"
            aria-label="Expand sidebar"
          >
            <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
              <path d="M3.5 18.49l6-6.01 4 4L22 6.92l-1.41-1.41-7.09 7.97-4-4L2 16.99z" />
            </svg>
          </button>
        )}
      </div>

      {/* ── Nav groups ─────────────────────────────────────────────────────── */}
      <nav className="flex flex-col gap-0.5 px-2 pt-3 shrink-0">
        {NAV_GROUPS.map((group) => {
          // A group with a single child whose route matches the group id
          // is treated as a direct link (Dashboard).
          const isSingleDirect = group.children.length === 1 && group.children[0].route === (group.id as AppRoute)
          const isGroupActive  = group.children.some((c) => c.route === activeRoute)
          const isOpen         = openGroups.has(group.id)

          if (isSingleDirect) {
            // Render as a flat nav item — no expand/collapse chrome needed
            const child = group.children[0]
            const active = activeRoute === child.route
            return (
              <button
                key={group.id}
                onClick={() => setRoute(child.route)}
                title={sidebarCollapsed ? group.label : undefined}
                className={clsx(
                  'flex items-center gap-3 px-2.5 py-2 rounded-lg text-[13px] text-left w-full transition-colors duration-100',
                  active
                    ? 'bg-[var(--bg-hover)] text-[var(--text-primary)] font-medium'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)]',
                )}
              >
                <span className="shrink-0">{group.icon}</span>
                {!sidebarCollapsed && <span className="truncate font-sans">{group.label}</span>}
              </button>
            )
          }

          return (
            <div key={group.id}>
              {/* Group header */}
              <button
                onClick={() => {
                  if (sidebarCollapsed) return // collapsed: clicking the icon navigates to first child
                  toggleGroup(group.id)
                }}
                title={sidebarCollapsed ? group.label : undefined}
                className={clsx(
                  'flex items-center gap-3 px-2.5 py-2 rounded-lg text-[13px] text-left w-full transition-colors duration-100',
                  isGroupActive
                    ? 'text-[var(--text-primary)] font-medium'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
                  'hover:bg-[var(--bg-hover)]',
                )}
              >
                <span className="shrink-0">{group.icon}</span>
                {!sidebarCollapsed && (
                  <>
                    <span className="truncate font-sans flex-1">{group.label}</span>
                    <Chevron open={isOpen} />
                  </>
                )}
              </button>

              {/* Children — visible when expanded and sidebar is not collapsed */}
              {!sidebarCollapsed && isOpen && (
                <div className="mt-0.5 mb-1 flex flex-col gap-0.5 pl-4">
                  {group.children.map((child) => {
                    const active = activeRoute === child.route
                    return (
                      <button
                        key={child.route}
                        onClick={() => handleChildClick(child.route, group.id)}
                        className={clsx(
                          'flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-[12px] text-left w-full transition-colors duration-100',
                          active
                            ? 'bg-[var(--bg-hover)] text-[var(--text-primary)] font-medium'
                            : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)]',
                        )}
                      >
                        <span className="shrink-0 opacity-70">{child.icon}</span>
                        <span className="truncate font-sans">{child.label}</span>
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </nav>

      {/* ── Focus widget ───────────────────────────────────────────────────── */}
      {!sidebarCollapsed && (
        <div className="px-2 pt-4 shrink-0">
          <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-hover)] p-3">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="text-[10px] font-sans uppercase tracking-[0.2em] text-[var(--text-muted)]">Focus</div>
                <div className="mt-1 text-lg font-mono font-bold text-[var(--text-primary)]">{selectedSymbol}</div>
              </div>
              <div
                className={clsx(
                  'rounded-full px-2 py-1 text-[10px] font-mono',
                  selectedQuote?.change_pct != null && selectedQuote.change_pct >= 0
                    ? 'bg-emerald-500/10 text-emerald-300'
                    : 'bg-red-500/10 text-red-700',
                )}
              >
                {selectedQuote?.change_pct != null
                  ? `${selectedQuote.change_pct >= 0 ? '+' : ''}${selectedQuote.change_pct.toFixed(2)}%`
                  : '--'}
              </div>
            </div>

            <div className="mt-3 flex items-end justify-between gap-2">
              <div>
                <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-500">Last Price</div>
                <div className="text-base font-mono font-semibold text-zinc-50">{formatPrice(selectedQuote?.price)}</div>
              </div>
              <div className="text-right">
                <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-500">Feed</div>
                <div className="text-[11px] font-mono text-zinc-400">
                  {selectedQuote?.live_source === 'ibkr' ? 'IBKR' : 'Yahoo'}
                </div>
              </div>
            </div>

            <div className="mt-3 grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => jumpTo('market', selectedSymbol)}
                className="rounded-lg border border-zinc-800 bg-zinc-950 px-2.5 py-2 text-[11px] font-sans font-medium text-white transition-colors hover:bg-zinc-900"
              >
                Open Market
              </button>
              <button
                type="button"
                onClick={() => jumpTo('stock', selectedSymbol)}
                className="rounded-lg border border-zinc-800 bg-zinc-900 px-2.5 py-2 text-[11px] font-sans font-medium text-zinc-200 transition-colors hover:border-zinc-700 hover:text-zinc-50"
              >
                Stock Analysis
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Watchlist ──────────────────────────────────────────────────────── */}
      {!sidebarCollapsed && watchlist && (
        <div className="mt-4 flex-1 overflow-y-auto px-2 min-h-0">
          <div className="px-2 mb-2 flex items-center justify-between">
            <p className="text-[10px] font-mono text-zinc-500 uppercase tracking-widest">
              Watchlist
            </p>
            <p className="text-[10px] font-mono text-zinc-500">
              {watchlist.symbols.length} names
            </p>
          </div>

          <div className="space-y-1">
            {watchlist.symbols.map((sym) => {
              const q    = quotes[sym]
              const up   = q && q.change_pct >= 0
              const active = sym === selectedSymbol

              return (
                <button
                  key={sym}
                  onClick={() => jumpTo('market', sym)}
                  className={clsx(
                    'flex items-center w-full px-2 py-2 rounded-lg transition-colors group border',
                    active
                      ? 'bg-zinc-800 border-zinc-800'
                      : 'bg-zinc-900 border-transparent hover:bg-zinc-900 hover:border-zinc-800',
                  )}
                >
                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    {q ? (
                      <span className={clsx('w-1.5 h-1.5 rounded-full shrink-0', up ? 'bg-emerald-500' : 'bg-red-500')} />
                    ) : (
                      <span className="w-1.5 h-1.5 rounded-full shrink-0 bg-zinc-700" />
                    )}
                    <span className="text-xs font-mono text-zinc-100 group-hover:text-zinc-50 truncate">
                      {sym}
                    </span>
                  </div>

                  {q && (
                    <div className="text-right shrink-0 ml-2">
                      <div className="text-[11px] font-mono text-zinc-200 leading-tight tabular-nums">
                        {formatPrice(q.price)}
                      </div>
                      <div
                        className={clsx(
                          'text-[10px] font-mono leading-tight tabular-nums',
                          up ? 'text-emerald-400' : 'text-red-400',
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
        </div>
      )}

      {/* ── IBKR / Bot status ──────────────────────────────────────────────── */}
      {!sidebarCollapsed && (
        <div className="border-t border-[#E8E4DF] px-4 py-3 shrink-0">
          <div className="flex items-center justify-between text-[10px] font-mono text-zinc-400">
            <span className="inline-flex items-center gap-1.5">
              <span className={clsx('h-1.5 w-1.5 rounded-full', ibkrConnected ? 'bg-emerald-500' : 'bg-red-400')} />
              IBKR
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className={clsx('h-1.5 w-1.5 rounded-full', botRunning ? 'bg-emerald-500' : 'bg-zinc-700')} />
              BOT
            </span>
          </div>
        </div>
      )}
    </aside>
  )
}
