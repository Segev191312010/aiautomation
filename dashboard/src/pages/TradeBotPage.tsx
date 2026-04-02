import React, { Suspense, lazy, useEffect, useState } from 'react'
import KPICard from '@/components/tradebot/KPICard'
import { PositionsContent } from '@/components/tradebot/PositionsContent'
import { ActivityContent } from '@/components/tradebot/ActivityContent'
import BotToggle from '@/components/tradebot/BotToggle'
import TradeBotTabs from '@/components/tradebot/TradeBotTabs'
import { fmtUSD } from '@/utils/formatters'
import { useAccountStore, useBotStore, useSimStore, useUIStore } from '@/store'
import { fetchTrades, fetchSimAccount, fetchSimPositions, fetchAccountSummary, fetchPositions } from '@/services/api'
import type { AccountSummary } from '@/types'
import ErrorBoundary from '@/components/ui/ErrorBoundary'

const RulesPage = lazy(() => import('@/pages/RulesPage'))
const AutopilotPage = lazy(() => import('@/pages/AutopilotPage'))

function TabFallback() {
  return (
    <div className="flex items-center justify-center py-20">
      <div className="flex flex-col items-center gap-3">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-[var(--border)] border-t-[var(--accent)]" />
        <span className="text-xs font-sans text-[var(--text-muted)]">Loading...</span>
      </div>
    </div>
  )
}

function HeroSignal({
  label,
  value,
  accent,
}: {
  label: string
  value: string
  accent?: 'default' | 'success' | 'warning'
}) {
  const toneClass =
    accent === 'success'
      ? 'border-[rgba(31,157,104,0.18)] bg-[rgba(31,157,104,0.1)] text-[var(--success)]'
      : accent === 'warning'
        ? 'border-[rgba(245,158,11,0.2)] bg-[rgba(245,158,11,0.1)] text-[var(--accent)]'
        : 'border-[var(--border)] bg-[var(--bg-hover)] text-[var(--text-primary)]'

  return (
    <div className={`rounded-[24px] border p-4 ${toneClass}`}>
      <div className="shell-kicker">{label}</div>
      <div className="mt-3 text-2xl font-semibold leading-none">{value}</div>
    </div>
  )
}

export default function TradeBotPage() {
  const { account, positions, setTrades, trades } = useAccountStore()
  const { simMode, botRunning, ibkrConnected } = useBotStore()
  const { simAccount, setSimAccount, setSimPositions } = useSimStore()
  const [initialLoad, setInitialLoad] = useState(true)

  const tradebotTab = useUIStore((s) => s.tradebotTab)
  const setTradebotTab = useUIStore((s) => s.setTradebotTab)

  const displayAccount = simMode ? simAccount : account
  const netLiq = displayAccount
    ? 'net_liquidation' in displayAccount
      ? displayAccount.net_liquidation
      : (displayAccount as AccountSummary).balance
    : null
  const cash = displayAccount?.cash ?? null
  const unrealPnl = displayAccount?.unrealized_pnl ?? null
  const realPnl = displayAccount?.realized_pnl ?? null

  useEffect(() => {
    const load = async () => {
      try {
        const recentTrades = await fetchTrades(50)
        setTrades(recentTrades)
      } catch {
        // Ignore feed failures and keep the desk interactive.
      }

      if (simMode) {
        try {
          const [acc, pos] = await Promise.all([fetchSimAccount(), fetchSimPositions()])
          setSimAccount(acc)
          setSimPositions(pos)
        } catch {
          // Ignore simulation refresh failure.
        }
      } else {
        try {
          const [acc, pos] = await Promise.all([fetchAccountSummary(), fetchPositions()])
          useAccountStore.getState().setAccount(acc)
          useAccountStore.getState().setPositions(pos)
        } catch {
          // Ignore live refresh failure.
        }
      }

      setInitialLoad(false)
    }

    void load()
    const timer = setInterval(load, 10_000)
    return () => clearInterval(timer)
  }, [simMode, setTrades, setSimAccount, setSimPositions])

  return (
    <div className="flex flex-col gap-6 pb-4">
      <ErrorBoundary>
        <section className="grid gap-6 xl:grid-cols-[minmax(0,1.25fr)_minmax(320px,0.75fr)]">
          <div className="shell-panel relative overflow-hidden p-6 sm:p-7">
            <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(245,158,11,0.16),transparent_34%)]" />
            <div className="relative">
              <div className="shell-kicker">Automation desk</div>
              <div className="mt-3 flex flex-wrap items-center gap-2.5">
                <h2 className="display-font text-[2.7rem] leading-none text-[var(--text-primary)] sm:text-[3.2rem]">
                  TradeBot
                </h2>
                <span className="shell-chip text-[11px] font-semibold">
                  {simMode ? 'Simulation' : 'Live account'}
                </span>
                <span className="shell-chip text-[11px] font-semibold">
                  {botRunning ? 'Bot active' : 'Bot idle'}
                </span>
                <span className="shell-chip text-[11px] font-semibold">
                  {ibkrConnected ? 'IBKR connected' : 'IBKR offline'}
                </span>
              </div>

              <p className="mt-4 max-w-3xl text-sm leading-7 text-[var(--text-secondary)]">
                Keep execution, automation controls, live activity, and rule management in one command surface.
                The data feed stays live while the page stays thin.
              </p>

              <div className="mt-5 flex flex-wrap gap-2">
                <span className="shell-chip text-[11px] font-medium">
                  {positions.length} open position{positions.length === 1 ? '' : 's'}
                </span>
                <span className="shell-chip text-[11px] font-medium">
                  {trades.length} recent trade{trades.length === 1 ? '' : 's'}
                </span>
                <span className="shell-chip text-[11px] font-medium">
                  Refreshes every 10s
                </span>
              </div>
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-3 xl:grid-cols-1">
            <HeroSignal
              label="Mode"
              value={simMode ? 'Simulation' : 'Live'}
              accent={simMode ? 'warning' : 'default'}
            />
            <HeroSignal
              label="Execution"
              value={botRunning ? 'Autonomous' : 'Manual'}
              accent={botRunning ? 'success' : 'default'}
            />
            <HeroSignal
              label="Connectivity"
              value={ibkrConnected ? 'Streaming' : 'Disconnected'}
              accent={ibkrConnected ? 'success' : 'warning'}
            />
          </div>
        </section>
      </ErrorBoundary>

      <ErrorBoundary>
        <section className="shell-panel p-5 sm:p-6">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div className="max-w-2xl">
              <div className="shell-kicker">Capital posture</div>
              <h2 className="display-font mt-2 text-[1.75rem] leading-none text-[var(--text-primary)]">
                Account overview
              </h2>
              <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
                Live or simulated balances, capital available, and current P&amp;L on the desk.
              </p>
            </div>

            {simMode && (
              <span className="shell-chip border-[rgba(245,158,11,0.28)] bg-[rgba(245,158,11,0.12)] text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--accent)]">
                Simulation mode
              </span>
            )}
          </div>

          {initialLoad && !displayAccount ? (
            <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] p-4 animate-pulse">
                  <div className="h-2.5 w-24 rounded bg-[var(--bg-card)]" />
                  <div className="mt-4 h-7 w-28 rounded bg-[var(--bg-card)]" />
                </div>
              ))}
            </div>
          ) : (
            <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <KPICard label="Net Liquidation" value={netLiq != null ? fmtUSD(netLiq) : '--'} highlight />
              <KPICard label="Cash" value={cash != null ? fmtUSD(cash) : '--'} />
              <KPICard
                label="Unrealized P&L"
                value={unrealPnl != null ? `${unrealPnl >= 0 ? '+' : ''}${fmtUSD(unrealPnl)}` : '--'}
                positive={unrealPnl != null ? unrealPnl >= 0 : undefined}
              />
              <KPICard
                label="Realized P&L"
                value={realPnl != null ? `${realPnl >= 0 ? '+' : ''}${fmtUSD(realPnl)}` : '--'}
                positive={realPnl != null ? realPnl >= 0 : undefined}
              />
            </div>
          )}
        </section>
      </ErrorBoundary>

      <ErrorBoundary>
        <section className="animate-fade-in-up" style={{ animationDelay: '40ms' }}>
          <BotToggle />
        </section>
      </ErrorBoundary>

      <ErrorBoundary>
        <section className="animate-fade-in-up" style={{ animationDelay: '60ms' }}>
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <div className="shell-kicker">Workspace</div>
              <p className="mt-2 max-w-xl text-sm leading-6 text-[var(--text-secondary)]">
                Switch between position management, rules, autopilot, and live activity without leaving the execution desk.
              </p>
            </div>

            <TradeBotTabs
              activeTab={tradebotTab}
              onTabChange={(tab) => setTradebotTab(tab as 'positions' | 'rules' | 'insights' | 'activity')}
              tabs={[
                { id: 'positions', label: 'Positions' },
                { id: 'rules', label: 'Rules' },
                { id: 'insights', label: 'Autopilot' },
                { id: 'activity', label: 'Activity' },
              ]}
            />
          </div>
        </section>
      </ErrorBoundary>

      <div className="animate-fade-in-up" style={{ animationDelay: '80ms' }}>
        {tradebotTab === 'positions' && (
          <ErrorBoundary>
            <PositionsContent positions={positions} initialLoad={initialLoad} />
          </ErrorBoundary>
        )}

        {tradebotTab === 'rules' && (
          <ErrorBoundary>
            <Suspense fallback={<TabFallback />}>
              <RulesPage />
            </Suspense>
          </ErrorBoundary>
        )}

        {tradebotTab === 'insights' && (
          <ErrorBoundary>
            <Suspense fallback={<TabFallback />}>
              <AutopilotPage />
            </Suspense>
          </ErrorBoundary>
        )}

        {tradebotTab === 'activity' && (
          <ErrorBoundary>
            <ActivityContent trades={trades} initialLoad={initialLoad} />
          </ErrorBoundary>
        )}
      </div>
    </div>
  )
}
