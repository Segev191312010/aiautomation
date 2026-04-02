import React, { useEffect, useMemo, useState } from 'react'
import clsx from 'clsx'
import { useUIStore, useMarketStore, useBotStore, useStockProfileStore } from '@/store'
import type { AppRoute } from '@/types'

type IconComponent = ({ className }: { className?: string }) => React.ReactElement

interface NavChild {
  route: AppRoute
  label: string
  icon: IconComponent
}

interface NavGroup {
  id: string
  label: string
  icon: IconComponent
  children: NavChild[]
}

const IconOverview: IconComponent = ({ className }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className={className}>
    <rect x="3" y="4" width="7" height="7" rx="1.5" />
    <rect x="14" y="4" width="7" height="4" rx="1.5" />
    <rect x="14" y="11" width="7" height="9" rx="1.5" />
    <rect x="3" y="14" width="7" height="6" rx="1.5" />
  </svg>
)

const IconTrade: IconComponent = ({ className }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className={className}>
    <path d="M4 18h4V8H4v10ZM10 18h4V4h-4v14ZM16 18h4v-7h-4v7Z" />
  </svg>
)

const IconWave: IconComponent = ({ className }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className={className}>
    <path d="M3 16c2.4 0 2.4-8 4.8-8s2.4 8 4.8 8 2.4-8 4.8-8S19.8 16 22 16" />
  </svg>
)

const IconRotation: IconComponent = ({ className }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className={className}>
    <path d="M5 9a7 7 0 0 1 11.5-3.9L19 7" />
    <path d="M19 15a7 7 0 0 1-11.5 3.9L5 17" />
    <path d="M19 7h-4V3" />
    <path d="M5 17h4v4" />
  </svg>
)

const IconSearch: IconComponent = ({ className }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className={className}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </svg>
)

const IconBuilding: IconComponent = ({ className }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className={className}>
    <path d="M4 21V7.5L12 3l8 4.5V21" />
    <path d="M9 9h.01M9 13h.01M9 17h.01M15 9h.01M15 13h.01M15 17h.01" />
  </svg>
)

const IconExperiment: IconComponent = ({ className }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className={className}>
    <path d="M10 3v5l-5.5 9.2A3 3 0 0 0 7 21h10a3 3 0 0 0 2.5-4.8L14 8V3" />
    <path d="M8.5 14h7" />
  </svg>
)

const IconRules: IconComponent = ({ className }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className={className}>
    <path d="M8 7h11M8 12h11M8 17h11" />
    <path d="M3.5 7h.01M3.5 12h.01M3.5 17h.01" />
  </svg>
)

const IconBell: IconComponent = ({ className }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className={className}>
    <path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4l2-2Z" />
    <path d="M10 20a2 2 0 0 0 4 0" />
  </svg>
)

const IconSettings: IconComponent = ({ className }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className={className}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1 1 0 0 0 .2 1.1l.1.1a2 2 0 0 1-2.8 2.8l-.1-.1a1 1 0 0 0-1.1-.2 1 1 0 0 0-.6.9V20a2 2 0 0 1-4 0v-.2a1 1 0 0 0-.6-.9 1 1 0 0 0-1.1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1 1 0 0 0 .2-1.1 1 1 0 0 0-.9-.6H4a2 2 0 0 1 0-4h.2a1 1 0 0 0 .9-.6 1 1 0 0 0-.2-1.1l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1 1 0 0 0 1.1.2H9a1 1 0 0 0 .6-.9V4a2 2 0 0 1 4 0v.2a1 1 0 0 0 .6.9 1 1 0 0 0 1.1-.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1 1 0 0 0-.2 1.1V9c0 .4.2.8.6.9H20a2 2 0 0 1 0 4h-.2a1 1 0 0 0-.9.6Z" />
  </svg>
)

const IconRobot: IconComponent = ({ className }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className={className}>
    <rect x="5" y="7" width="14" height="10" rx="3" />
    <path d="M12 3v4M8 13h.01M16 13h.01M9 17v2M15 17v2M3 10v4M21 10v4" />
  </svg>
)

const IconSpark: IconComponent = ({ className }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className={className}>
    <path d="m13 2-1.7 5.1L6 9l5.3 1.9L13 16l1.7-5.1L20 9l-5.3-1.9L13 2Z" />
    <path d="M5 18.5 4 21l-1-2.5L.5 17l2.5-1L4 13.5 5 16l2.5 1L5 18.5ZM19 18.5l-1 2.5-1-2.5-2.5-1 2.5-1 1-2.5 1 2.5 2.5 1-2.5 1Z" />
  </svg>
)

const NAV_GROUPS: NavGroup[] = [
  {
    id: 'home',
    label: 'Overview',
    icon: IconOverview,
    children: [
      { route: 'dashboard', label: 'Dashboard', icon: IconOverview },
      { route: 'analytics', label: 'Analytics', icon: IconTrade },
      { route: 'advisor', label: 'Autopilot', icon: IconRobot },
    ],
  },
  {
    id: 'trading',
    label: 'Trading',
    icon: IconTrade,
    children: [
      { route: 'tradebot', label: 'TradeBot', icon: IconTrade },
      { route: 'rules', label: 'Rules', icon: IconRules },
      { route: 'alerts', label: 'Alerts', icon: IconBell },
    ],
  },
  {
    id: 'markets',
    label: 'Markets',
    icon: IconWave,
    children: [
      { route: 'market', label: 'Market', icon: IconWave },
      { route: 'charts', label: 'Charts', icon: IconWave },
      { route: 'rotation', label: 'Rotation', icon: IconRotation },
      { route: 'screener', label: 'Screener', icon: IconSearch },
      { route: 'stock', label: 'Stock', icon: IconBuilding },
    ],
  },
  {
    id: 'research',
    label: 'Sandbox',
    icon: IconExperiment,
    children: [
      { route: 'backtest', label: 'Backtest', icon: IconExperiment },
      { route: 'simulation', label: 'Simulation', icon: IconSpark },
      { route: 'settings', label: 'Settings', icon: IconSettings },
    ],
  },
]

function formatPrice(value?: number): string {
  if (value == null) return '--'
  return `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function formatChange(value?: number): string {
  if (value == null) return '--'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

function findGroupForRoute(route: AppRoute): string | null {
  for (const group of NAV_GROUPS) {
    if (group.children.some((child) => child.route === route)) return group.id
  }
  return null
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      className={clsx('h-3.5 w-3.5 transition-transform duration-200', open && 'rotate-90')}
    >
      <path d="m7 4 6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export default function Sidebar() {
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed)
  const toggleSidebar = useUIStore((s) => s.toggleSidebar)
  const activeRoute = useUIStore((s) => s.activeRoute)
  const setRoute = useUIStore((s) => s.setRoute)
  const quotes = useMarketStore((s) => s.quotes)
  const watchlists = useMarketStore((s) => s.watchlists)
  const activeWatchlist = useMarketStore((s) => s.activeWatchlist)
  const selectedSymbol = useMarketStore((s) => s.selectedSymbol)
  const setSelectedSymbol = useMarketStore((s) => s.setSelectedSymbol)
  const setProfileSymbol = useStockProfileStore((s) => s.setSymbol)
  const ibkrConnected = useBotStore((s) => s.ibkrConnected)
  const botRunning = useBotStore((s) => s.botRunning)

  const [openGroups, setOpenGroups] = useState<Set<string>>(() => {
    const activeGroup = findGroupForRoute(activeRoute)
    return new Set(activeGroup ? [activeGroup] : ['home'])
  })

  useEffect(() => {
    const activeGroup = findGroupForRoute(activeRoute)
    if (!activeGroup) return
    setOpenGroups((previous) => (previous.has(activeGroup) ? previous : new Set([...previous, activeGroup])))
  }, [activeRoute])

  const watchlist = useMemo(
    () => watchlists.find((item) => item.id === activeWatchlist),
    [watchlists, activeWatchlist],
  )
  const selectedQuote = quotes[selectedSymbol]
  const focusSymbols = useMemo(() => watchlist?.symbols.slice(0, 8) ?? [], [watchlist])
  const compactRoutes = useMemo(() => NAV_GROUPS.flatMap((group) => group.children), [])
  const expandedDesktop = !sidebarCollapsed
  const widthClass = sidebarCollapsed ? 'w-[92px]' : 'w-[92px] lg:w-[304px]'

  const toggleGroup = (id: string) => {
    setOpenGroups((previous) => {
      const next = new Set(previous)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleRoute = (route: AppRoute, groupId: string) => {
    setRoute(route)
    setOpenGroups((previous) => (previous.has(groupId) ? previous : new Set([...previous, groupId])))
  }

  const jumpTo = (route: 'market' | 'stock', symbol: string) => {
    setSelectedSymbol(symbol)
    setProfileSymbol(symbol)
    setRoute(route)
  }

  return (
    <aside className={clsx('relative z-20 h-screen shrink-0 p-3 sm:p-4', widthClass)}>
      <div className="shell-panel relative flex h-full flex-col overflow-hidden border-[rgba(255,255,255,0.08)] bg-[var(--bg-sidebar)] text-[#f5efe6]">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(245,158,11,0.16),transparent_36%)]" />
        <div className="pointer-events-none absolute inset-y-10 right-0 w-px bg-gradient-to-b from-transparent via-[rgba(255,255,255,0.12)] to-transparent" />

        <div className="relative border-b border-[rgba(255,255,255,0.08)] px-3 py-3">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-[rgba(255,255,255,0.12)] bg-[rgba(255,255,255,0.06)] text-[var(--accent-strong)]">
              <IconSpark className="h-5 w-5" />
            </div>

            <div className={clsx(expandedDesktop ? 'hidden lg:block' : 'hidden')}>
              <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#9da5b2]">
                Trading Desk
              </div>
              <div className="display-font text-[1.15rem] leading-none text-[#f7f1e7]">
                Mercury Board
              </div>
            </div>

            <button
              type="button"
              onClick={toggleSidebar}
              className="ml-auto hidden rounded-2xl border border-[rgba(255,255,255,0.12)] bg-[rgba(255,255,255,0.04)] p-2 text-[#d7c9b3] transition-colors hover:border-[rgba(255,255,255,0.24)] hover:text-white lg:flex"
              aria-label="Toggle sidebar"
            >
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" className={clsx('h-4 w-4 transition-transform', !sidebarCollapsed && 'rotate-180')}>
                <path d="m7 4 6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </div>

          <div className="mt-3 grid grid-cols-2 gap-2">
            <StatusBadge active={ibkrConnected} label="Feed" value={ibkrConnected ? 'IBKR' : 'Offline'} />
            <StatusBadge active={botRunning} label="Bot" value={botRunning ? 'Running' : 'Idle'} />
          </div>
        </div>

        <div className="relative flex-1 overflow-y-auto px-3 py-3">
          <div className={clsx(expandedDesktop ? 'hidden lg:flex lg:flex-col lg:gap-2' : 'hidden')}>
            {NAV_GROUPS.map((group) => {
              const GroupIcon = group.icon
              const groupOpen = openGroups.has(group.id)
              const groupActive = group.children.some((child) => child.route === activeRoute)

              return (
                <section
                  key={group.id}
                  className={clsx(
                    'rounded-[22px] border px-2 py-2 transition-colors',
                    groupActive
                      ? 'border-[rgba(245,158,11,0.24)] bg-[rgba(255,255,255,0.05)]'
                      : 'border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.03)]',
                  )}
                >
                  <button
                    type="button"
                    onClick={() => toggleGroup(group.id)}
                    className="flex w-full items-center gap-3 rounded-2xl px-2.5 py-2 text-left text-[#ddd3c4] transition-colors hover:bg-[rgba(255,255,255,0.04)]"
                  >
                    <div className={clsx(
                      'flex h-9 w-9 items-center justify-center rounded-2xl border',
                      groupActive
                        ? 'border-[rgba(245,158,11,0.28)] bg-[rgba(245,158,11,0.12)] text-[var(--accent-strong)]'
                        : 'border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.04)] text-[#c8baa3]',
                    )}>
                      <GroupIcon className="h-[18px] w-[18px]" />
                    </div>

                    <div className="min-w-0 flex-1">
                      <div className="text-xs font-semibold uppercase tracking-[0.18em] text-[#8d95a1]">
                        {group.label}
                      </div>
                      <div className="mt-0.5 text-sm font-medium text-[#f5efe6]">
                        {group.children.length} destinations
                      </div>
                    </div>

                    <Chevron open={groupOpen} />
                  </button>

                  {groupOpen && (
                    <div className="mt-2 space-y-1">
                      {group.children.map((child) => {
                        const ChildIcon = child.icon
                        const active = child.route === activeRoute
                        return (
                          <button
                            key={child.route}
                            type="button"
                            onClick={() => handleRoute(child.route, group.id)}
                            className={clsx(
                              'flex w-full items-center gap-3 rounded-2xl px-3 py-2.5 text-left transition-all',
                              active
                                ? 'bg-[rgba(245,158,11,0.14)] text-white shadow-[0_12px_24px_-18px_rgba(245,158,11,0.6)]'
                                : 'text-[#c4baa9] hover:bg-[rgba(255,255,255,0.05)] hover:text-white',
                            )}
                          >
                            <ChildIcon className="h-4 w-4" />
                            <span className="text-sm font-medium">{child.label}</span>
                          </button>
                        )
                      })}
                    </div>
                  )}
                </section>
              )
            })}
          </div>

          <div className={clsx(expandedDesktop ? 'flex flex-col gap-2 lg:hidden' : 'flex flex-col gap-2')}>
            {compactRoutes.map((route) => {
              const RouteIcon = route.icon
              const active = route.route === activeRoute
              return (
                <button
                  key={route.route}
                  type="button"
                  title={route.label}
                  onClick={() => setRoute(route.route)}
                  className={clsx(
                    'flex h-12 w-full items-center justify-center rounded-2xl border transition-all',
                    active
                      ? 'border-[rgba(245,158,11,0.28)] bg-[rgba(245,158,11,0.14)] text-[var(--accent-strong)]'
                      : 'border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.03)] text-[#c4baa9] hover:bg-[rgba(255,255,255,0.05)] hover:text-white',
                  )}
                >
                  <RouteIcon className="h-[18px] w-[18px]" />
                </button>
              )
            })}
          </div>
        </div>

        <div className="relative border-t border-[rgba(255,255,255,0.08)] px-3 py-3">
          <div className={clsx(
            'rounded-[24px] border border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.04)] p-3',
            expandedDesktop ? 'hidden lg:block' : 'hidden',
          )}>
            <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-[#8d95a1]">
              Focus symbol
            </div>
            <div className="mt-2 flex items-end justify-between gap-2">
              <div>
                <div className="display-font text-[1.55rem] leading-none text-white">{selectedSymbol}</div>
                <div className="mt-1 text-sm text-[#d3c8b8]">{formatPrice(selectedQuote?.price)}</div>
              </div>
              <div className={clsx(
                'text-sm font-semibold',
                (selectedQuote?.change_pct ?? 0) >= 0 ? 'text-[var(--success)]' : 'text-[var(--danger)]',
              )}>
                {formatChange(selectedQuote?.change_pct)}
              </div>
            </div>

            <div className="mt-3 text-[10px] font-semibold uppercase tracking-[0.18em] text-[#8d95a1]">
              Watchlist radar
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {focusSymbols.map((symbol) => (
                <button
                  key={symbol}
                  type="button"
                  onClick={() => jumpTo('market', symbol)}
                  className={clsx(
                    'rounded-full border px-2.5 py-1 text-[11px] font-semibold transition-colors',
                    symbol === selectedSymbol
                      ? 'border-[rgba(245,158,11,0.32)] bg-[rgba(245,158,11,0.16)] text-white'
                      : 'border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.02)] text-[#d3c8b8] hover:bg-[rgba(255,255,255,0.05)] hover:text-white',
                  )}
                >
                  {symbol}
                </button>
              ))}
            </div>
          </div>

          <div className={clsx(expandedDesktop ? 'flex items-center justify-center lg:hidden' : 'flex items-center justify-center')}>
            <button
              type="button"
              onClick={() => jumpTo('market', selectedSymbol)}
              className="flex h-12 w-full items-center justify-center rounded-2xl border border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.04)] text-[#f5efe6] transition-colors hover:bg-[rgba(255,255,255,0.06)]"
              title={`Open ${selectedSymbol} in market view`}
            >
              <IconWave className="h-[18px] w-[18px]" />
            </button>
          </div>
        </div>
      </div>
    </aside>
  )
}

function StatusBadge({ active, label, value }: { active: boolean; label: string; value: string }) {
  return (
    <div className={clsx(
      'rounded-2xl border px-2.5 py-2',
      active
        ? 'border-[rgba(52,211,153,0.22)] bg-[rgba(52,211,153,0.08)]'
        : 'border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.03)]',
    )}>
      <div className="text-[9px] font-semibold uppercase tracking-[0.18em] text-[#8d95a1]">{label}</div>
      <div className="mt-1 flex items-center gap-1.5 text-[11px] font-semibold text-[#f5efe6]">
        <span className={clsx('h-2 w-2 rounded-full', active ? 'bg-[var(--success)]' : 'bg-[#64748b]')} />
        {value}
      </div>
    </div>
  )
}
