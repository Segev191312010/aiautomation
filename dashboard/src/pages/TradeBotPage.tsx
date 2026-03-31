/**
 * TradeBotPage — Command Center for automated trading.
 *
 * Tabs:
 *  1. Positions (default) — KPI cards, quick order, positions table
 *  2. Rules               — inline RulesPage (lazy)
 *  3. Autopilot           — inline AutopilotPage (lazy)
 *  4. Activity            — LiveActivityFeed + recent trades log
 *
 * KPI cards and BotToggle are always visible above the tab bar.
 */
import React, { Suspense, useEffect, useState, lazy } from 'react'
import clsx from 'clsx'
import { fmtUSD } from '@/utils/formatters'
import { IconDollar, IconWallet, IconTrendUp, IconTrendDown } from '@/components/icons'
import { KPISkeletonCard } from '@/components/tradebot/KPISkeletonCard'
import { PositionsContent } from '@/components/tradebot/PositionsContent'
import { ActivityContent } from '@/components/tradebot/ActivityContent'
import BotToggle from '@/components/tradebot/BotToggle'
import TradeBotTabs from '@/components/tradebot/TradeBotTabs'
import { useAccountStore, useBotStore, useSimStore, useUIStore } from '@/store'
import { fetchTrades, fetchSimAccount, fetchSimPositions, fetchAccountSummary, fetchPositions } from '@/services/api'
import type { AccountSummary } from '@/types'

const RulesPage     = lazy(() => import('@/pages/RulesPage'))
const AutopilotPage = lazy(() => import('@/pages/AutopilotPage'))

function TabFallback() {
  return (
    <div className="flex items-center justify-center py-20">
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 rounded-full border-2 border-indigo-500/30 border-t-indigo-500 animate-spin" />
        <span className="text-xs font-sans text-zinc-500">Loading…</span>
      </div>
    </div>
  )
}

export default function TradeBotPage() {
  const { account, positions, setTrades, trades } = useAccountStore()
  const { simMode } = useBotStore()
  const { simAccount, setSimAccount, setSimPositions } = useSimStore()
  const [initialLoad, setInitialLoad] = useState(true)

  const tradebotTab    = useUIStore((s) => s.tradebotTab)
  const setTradebotTab = useUIStore((s) => s.setTradebotTab)

  const displayAccount = simMode ? simAccount : account

  const netLiq    = displayAccount ? ('net_liquidation' in displayAccount ? displayAccount.net_liquidation : (displayAccount as AccountSummary).balance) : null
  const cash      = displayAccount?.cash ?? null
  const unrealPnl = displayAccount?.unrealized_pnl ?? null
  const realPnl   = displayAccount?.realized_pnl ?? null

  useEffect(() => {
    const load = async () => {
      try {
        const t = await fetchTrades(50)
        setTrades(t)
      } catch { /* ignore */ }

      if (simMode) {
        try {
          const [acc, pos] = await Promise.all([fetchSimAccount(), fetchSimPositions()])
          setSimAccount(acc)
          setSimPositions(pos)
        } catch { /* ignore */ }
      } else {
        try {
          const [acc, pos] = await Promise.all([fetchAccountSummary(), fetchPositions()])
          useAccountStore.getState().setAccount(acc)
          useAccountStore.getState().setPositions(pos)
        } catch { /* ignore */ }
      }
      setInitialLoad(false)
    }
    load()
    const t = setInterval(load, 10_000)
    return () => clearInterval(t)
  }, [simMode, setTrades, setSimAccount, setSimPositions])

  return (
    <div className="flex flex-col gap-5">

      {/* ── KPI header — always visible ───────────────────────────────────── */}
      <section className="animate-fade-in-up">
        <div className="flex items-center gap-2 mb-3">
          <h2 className="text-xs font-sans font-medium text-zinc-400 tracking-widest uppercase">
            Account Overview
          </h2>
          {simMode && (
            <span className="text-[10px] font-mono px-2 py-0.5 rounded-lg bg-amber-600/15 text-amber-600 border border-amber-300/20">
              SIMULATION
            </span>
          )}
        </div>

        {initialLoad && !displayAccount ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <KPISkeletonCard />
            <KPISkeletonCard />
            <KPISkeletonCard />
            <KPISkeletonCard />
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {/* Net Liquidation */}
            <div className="bg-zinc-900/80 border border-zinc-800 rounded-2xl p-5 flex flex-col gap-2 border-l-2 border-l-indigo-600/60">
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-lg bg-indigo-50 flex items-center justify-center flex-shrink-0">
                  <IconDollar className="w-3.5 h-3.5 text-indigo-600" />
                </div>
                <span className="text-[10px] font-sans font-medium text-zinc-400 tracking-widest uppercase">
                  Net Liquidation
                </span>
              </div>
              <span className="text-2xl font-mono font-bold tabular-nums text-zinc-100">
                {netLiq != null ? fmtUSD(netLiq) : '—'}
              </span>
            </div>

            {/* Cash */}
            <div className="bg-zinc-900/80 border border-zinc-800 rounded-2xl p-5 flex flex-col gap-2 border-l-2 border-l-blue-500/50">
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-lg bg-blue-500/15 flex items-center justify-center flex-shrink-0">
                  <IconWallet className="w-3.5 h-3.5 text-blue-400" />
                </div>
                <span className="text-[10px] font-sans font-medium text-zinc-400 tracking-widest uppercase">
                  Cash
                </span>
              </div>
              <span className="text-2xl font-mono font-bold tabular-nums text-zinc-100">
                {cash != null ? fmtUSD(cash) : '—'}
              </span>
            </div>

            {/* Unrealized P&L */}
            <div className={clsx(
              'bg-zinc-900/80 border border-zinc-800 rounded-2xl p-5 flex flex-col gap-2 border-l-2',
              unrealPnl == null
                ? 'border-l-white/[0.08]'
                : unrealPnl >= 0
                ? 'border-l-emerald-500/60'
                : 'border-l-red-500/60',
            )}>
              <div className="flex items-center gap-2">
                <div className={clsx(
                  'w-6 h-6 rounded-lg flex items-center justify-center flex-shrink-0',
                  unrealPnl == null
                    ? 'bg-zinc-800/50'
                    : unrealPnl >= 0
                    ? 'bg-emerald-500/15'
                    : 'bg-red-500/15',
                )}>
                  {unrealPnl == null || unrealPnl >= 0
                    ? <IconTrendUp className={clsx('w-3.5 h-3.5', unrealPnl == null ? 'text-zinc-500' : 'text-emerald-400')} />
                    : <IconTrendDown className="w-3.5 h-3.5 text-red-400" />
                  }
                </div>
                <span className="text-[10px] font-sans font-medium text-zinc-400 tracking-widest uppercase">
                  Unrealized P&L
                </span>
              </div>
              <span className={clsx(
                'text-2xl font-mono font-bold tabular-nums',
                unrealPnl == null
                  ? 'text-zinc-100'
                  : unrealPnl >= 0
                  ? 'text-emerald-400'
                  : 'text-red-400',
              )}>
                {unrealPnl != null
                  ? (unrealPnl >= 0 ? '+' : '') + fmtUSD(unrealPnl)
                  : '—'}
              </span>
            </div>

            {/* Realized P&L */}
            <div className={clsx(
              'bg-zinc-900/80 border border-zinc-800 rounded-2xl p-5 flex flex-col gap-2 border-l-2',
              realPnl == null
                ? 'border-l-white/[0.08]'
                : realPnl >= 0
                ? 'border-l-emerald-500/60'
                : 'border-l-red-500/60',
            )}>
              <div className="flex items-center gap-2">
                <div className={clsx(
                  'w-6 h-6 rounded-lg flex items-center justify-center flex-shrink-0',
                  realPnl == null
                    ? 'bg-zinc-800/50'
                    : realPnl >= 0
                    ? 'bg-emerald-500/15'
                    : 'bg-red-500/15',
                )}>
                  {realPnl == null || realPnl >= 0
                    ? <IconTrendUp className={clsx('w-3.5 h-3.5', realPnl == null ? 'text-zinc-500' : 'text-emerald-400')} />
                    : <IconTrendDown className="w-3.5 h-3.5 text-red-400" />
                  }
                </div>
                <span className="text-[10px] font-sans font-medium text-zinc-400 tracking-widest uppercase">
                  Realized P&L
                </span>
              </div>
              <span className={clsx(
                'text-2xl font-mono font-bold tabular-nums',
                realPnl == null
                  ? 'text-zinc-100'
                  : realPnl >= 0
                  ? 'text-emerald-400'
                  : 'text-red-400',
              )}>
                {realPnl != null
                  ? (realPnl >= 0 ? '+' : '') + fmtUSD(realPnl)
                  : '—'}
              </span>
            </div>
          </div>
        )}
      </section>

      {/* ── Master toggle — always visible ────────────────────────────────── */}
      <section className="animate-fade-in-up" style={{ animationDelay: '40ms' }}>
        <BotToggle />
      </section>

      {/* ── Tab bar ───────────────────────────────────────────────────────── */}
      <div className="animate-fade-in-up" style={{ animationDelay: '60ms' }}>
        <TradeBotTabs
          activeTab={tradebotTab}
          onTabChange={(tab) => setTradebotTab(tab as 'positions' | 'rules' | 'insights' | 'activity')}
          tabs={[
            { id: 'positions', label: 'Positions' },
            { id: 'rules',     label: 'Rules' },
            { id: 'insights',  label: 'Autopilot' },
            { id: 'activity',  label: 'Activity' },
          ]}
        />
      </div>

      {/* ── Tab content ───────────────────────────────────────────────────── */}
      <div className="animate-fade-in-up" style={{ animationDelay: '80ms' }}>
        {tradebotTab === 'positions' && (
          <PositionsContent positions={positions} initialLoad={initialLoad} />
        )}

        {tradebotTab === 'rules' && (
          <Suspense fallback={<TabFallback />}>
            <RulesPage />
          </Suspense>
        )}

        {tradebotTab === 'insights' && (
          <Suspense fallback={<TabFallback />}>
            <AutopilotPage />
          </Suspense>
        )}

        {tradebotTab === 'activity' && (
          <ActivityContent trades={trades} initialLoad={initialLoad} />
        )}
      </div>

    </div>
  )
}
