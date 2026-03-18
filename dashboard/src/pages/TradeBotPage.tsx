/**
 * TradeBotPage — Command Center for automated trading.
 *
 * Sections:
 *  1. KPI header (Net Liq, Cash, Unrealized P&L, Realized P&L)
 *  2. Master Toggle (Automated Trading On/Off)
 *  3. Quick Order form
 *  4. Positions table
 *  5. Recent trades log
 */
import React, { useEffect, useState } from 'react'
import clsx from 'clsx'
import KPICard from '@/components/tradebot/KPICard'
import BotToggle from '@/components/tradebot/BotToggle'
import PositionsTable from '@/components/tradebot/PositionsTable'
import { useToast } from '@/components/ui/ToastProvider'
import { useAccountStore, useBotStore, useSimStore } from '@/store'
import { fetchTrades, fetchSimAccount, fetchSimPositions, fetchAccountSummary, fetchPositions, placeManualOrder } from '@/services/api'
import LiveActivityFeed from '@/components/tradebot/LiveActivityFeed'
import type { Trade, SimAccountState, AccountSummary } from '@/types'

function fmtUSD(v: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(v)
}

function fmtTimestamp(ts: string): string {
  const d = new Date(ts)
  const today = new Date()
  const isToday =
    d.getFullYear() === today.getFullYear() &&
    d.getMonth() === today.getMonth() &&
    d.getDate() === today.getDate()
  if (isToday) {
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  }
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
    ' ' +
    d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
}

function isSimAccount(a: AccountSummary | SimAccountState): a is SimAccountState {
  return 'is_sim' in a && a.is_sim === true
}

// ── SVG Icons ─────────────────────────────────────────────────────────────────

function IconDollar({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <line x1="12" y1="1" x2="12" y2="23" />
      <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
    </svg>
  )
}

function IconWallet({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <rect x="1" y="4" width="22" height="16" rx="2" ry="2" />
      <line x1="1" y1="10" x2="23" y2="10" />
    </svg>
  )
}

function IconTrendUp({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
      <polyline points="17 6 23 6 23 12" />
    </svg>
  )
}

function IconTrendDown({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <polyline points="23 18 13.5 8.5 8.5 13.5 1 6" />
      <polyline points="17 18 23 18 23 12" />
    </svg>
  )
}

function IconLightning({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
    </svg>
  )
}

function IconBriefcase({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <rect x="2" y="7" width="20" height="14" rx="2" ry="2" />
      <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" />
    </svg>
  )
}

function IconArrows({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <line x1="12" y1="5" x2="12" y2="19" />
      <polyline points="19 12 12 19 5 12" />
    </svg>
  )
}

// ── KPI skeleton ───────────────────────────────────────────────────────────────

function KPISkeletonCard() {
  return (
    <div className="bg-zinc-900/80 border border-zinc-800 rounded-2xl p-5 flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <div className="w-7 h-7 rounded-lg bg-zinc-800/40 animate-pulse" />
        <div className="h-2.5 w-24 rounded-lg bg-zinc-800/40 animate-pulse" />
      </div>
      <div className="h-7 w-36 rounded-xl bg-zinc-800/30 animate-pulse" />
    </div>
  )
}

// ── Section header ─────────────────────────────────────────────────────────────

function SectionHeader({ icon, title, badge }: { icon: React.ReactNode; title: string; badge?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2.5 mb-4">
      <div className="w-7 h-7 rounded-lg bg-zinc-800/50 flex items-center justify-center flex-shrink-0">
        {icon}
      </div>
      <h2 className="text-sm font-sans font-semibold text-zinc-100 tracking-wide">
        {title}
      </h2>
      {badge}
    </div>
  )
}

// ── Quick Order form ───────────────────────────────────────────────────────────

function QuickOrderForm() {
  const toast = useToast()
  const [sym,    setSym]    = useState('')
  const [qty,    setQty]    = useState(1)
  const [action, setAction] = useState<'BUY' | 'SELL'>('BUY')
  const [status, setStatus] = useState('')
  const [busy,   setBusy]   = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!sym || qty <= 0) return
    setBusy(true)
    setStatus('')
    try {
      const r = await placeManualOrder({ symbol: sym.toUpperCase(), action, quantity: qty })
      const msg = r.message ?? 'Order placed'
      setStatus(msg)
      toast.success(msg)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Order failed'
      setStatus(msg)
      toast.error(msg)
    } finally {
      setBusy(false)
    }
  }

  const isBuy = action === 'BUY'
  const canSubmit = sym.length > 0 && qty > 0

  return (
    <div className="flex flex-col gap-5">
      {/* Inputs row */}
      <div className="flex flex-wrap items-end gap-4">
        {/* Symbol */}
        <div className="flex flex-col gap-1.5">
          <label className="text-[11px] font-sans font-medium text-zinc-400 tracking-widest uppercase">
            Symbol
          </label>
          <input
            value={sym}
            onChange={(e) => setSym(e.target.value)}
            placeholder="AAPL"
            className="w-28 text-sm font-mono bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-2 text-zinc-100 focus:border-indigo-600/50 focus:outline-none uppercase tracking-wider placeholder:text-zinc-500/50"
          />
        </div>

        {/* Quantity */}
        <div className="flex flex-col gap-1.5">
          <label className="text-[11px] font-sans font-medium text-zinc-400 tracking-widest uppercase">
            Quantity
          </label>
          <input
            type="number"
            min={1}
            value={qty}
            onChange={(e) => setQty(Number(e.target.value))}
            className="w-24 text-sm font-mono bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-2 text-zinc-100 focus:border-indigo-600/50 focus:outline-none"
          />
        </div>

        {/* Side toggle */}
        <div className="flex flex-col gap-1.5">
          <label className="text-[11px] font-sans font-medium text-zinc-400 tracking-widest uppercase">
            Side
          </label>
          <div className="flex rounded-xl overflow-hidden border border-zinc-800">
            {(['BUY', 'SELL'] as const).map((a) => (
              <button
                key={a}
                type="button"
                onClick={() => setAction(a)}
                className={clsx(
                  'text-sm font-sans font-semibold px-5 py-2 transition-all duration-150',
                  action === a && a === 'BUY'
                    ? 'bg-emerald-500/20 text-emerald-400 border-r border-zinc-800'
                    : action === a && a === 'SELL'
                    ? 'bg-red-500/20 text-red-400'
                    : a === 'BUY'
                    ? 'text-zinc-500 hover:text-zinc-400 bg-transparent border-r border-zinc-800'
                    : 'text-zinc-500 hover:text-zinc-400 bg-transparent',
                )}
              >
                {a}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Order preview */}
      {sym && qty > 0 && (
        <div className={clsx(
          'flex items-center gap-2 px-4 py-2.5 rounded-xl border text-xs font-mono',
          isBuy
            ? 'border-emerald-500/20 bg-emerald-500/[0.05] text-emerald-400/80'
            : 'border-red-500/20 bg-red-500/[0.05] text-red-400/80',
        )}>
          <span className="opacity-60">Preview:</span>
          <span className="font-semibold">{isBuy ? 'BUY' : 'SELL'}</span>
          <span className="text-zinc-400">{qty} share{qty !== 1 ? 's' : ''} of</span>
          <span className="font-semibold text-zinc-100">{sym.toUpperCase()}</span>
          <span className="text-zinc-500 ml-1">— Market Order</span>
        </div>
      )}

      {/* Submit row */}
      <form onSubmit={handleSubmit} className="flex items-center gap-3 flex-wrap">
        <button
          type="submit"
          disabled={busy || !canSubmit}
          className={clsx(
            'flex items-center gap-2 text-sm font-sans font-semibold px-6 py-2.5 rounded-xl transition-all duration-150',
            'disabled:opacity-40 disabled:cursor-not-allowed',
            isBuy
              ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/25 hover:border-emerald-500/50'
              : 'bg-red-500/15 text-red-400 border border-red-500/30 hover:bg-red-500/25 hover:border-red-500/50',
          )}
        >
          <IconLightning className="w-3.5 h-3.5" />
          {busy ? 'Placing…' : `Place ${action} Order`}
        </button>
        {status && (
          <span className="text-[11px] font-sans text-zinc-400">{status}</span>
        )}
      </form>
    </div>
  )
}

// ── Trade log row ──────────────────────────────────────────────────────────────

const STATUS_DOT: Record<string, string> = {
  FILLED:    'bg-emerald-400',
  PENDING:   'bg-amber-600',
  CANCELLED: 'bg-zinc-600',
  ERROR:     'bg-red-400',
}

const STATUS_BADGE: Record<string, string> = {
  FILLED:    'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  PENDING:   'text-amber-600 bg-amber-500/10 border-amber-500/20',
  CANCELLED: 'text-zinc-500 bg-zinc-800/60 border-zinc-800',
  ERROR:     'text-red-400 bg-red-500/10 border-red-500/20',
}

function TradeRow({ trade }: { trade: Trade }) {
  const isBuy = trade.action === 'BUY'
  const dotClass    = STATUS_DOT[trade.status]    ?? 'bg-zinc-600'
  const badgeClass  = STATUS_BADGE[trade.status]  ?? 'text-zinc-500 bg-zinc-800/60 border-zinc-800'

  return (
    <tr
      className={clsx(
        'border-b border-zinc-800 transition-colors group',
        isBuy
          ? 'hover:bg-emerald-500/[0.04]'
          : 'hover:bg-red-500/[0.04]',
      )}
    >
      <td className="py-2.5 px-3 font-mono text-[11px] text-zinc-500 tabular-nums whitespace-nowrap">
        {fmtTimestamp(trade.timestamp)}
      </td>
      <td className="py-2.5 px-3 font-mono text-sm font-semibold text-zinc-100 tracking-wide">
        {trade.symbol}
      </td>
      <td className="py-2.5 px-3">
        <span
          className={clsx(
            'inline-flex items-center gap-1.5 font-mono text-xs font-semibold px-2 py-0.5 rounded-lg border',
            isBuy
              ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
              : 'text-red-400 bg-red-500/10 border-red-500/20',
          )}
        >
          <span
            className={clsx(
              'w-1.5 h-1.5 rounded-full flex-shrink-0',
              isBuy ? 'bg-emerald-400' : 'bg-red-400',
            )}
          />
          {trade.action}
        </span>
      </td>
      <td className="py-2.5 px-3 font-mono text-sm text-zinc-400 tabular-nums text-right">
        {trade.quantity}
      </td>
      <td className="py-2.5 px-3 font-mono text-sm text-zinc-400 tabular-nums text-right">
        {trade.fill_price != null ? fmtUSD(trade.fill_price) : (
          <span className="text-zinc-500">—</span>
        )}
      </td>
      <td className="py-2.5 px-3 text-right">
        <span
          className={clsx(
            'inline-flex items-center gap-1.5 font-mono text-[11px] font-medium px-2 py-0.5 rounded-lg border',
            badgeClass,
          )}
        >
          <span className={clsx('w-1.5 h-1.5 rounded-full flex-shrink-0', dotClass)} />
          {trade.status}
        </span>
      </td>
    </tr>
  )
}

// ── Positions empty state ──────────────────────────────────────────────────────

function PositionsEmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-14 gap-3">
      <div className="w-14 h-14 rounded-2xl bg-zinc-800/30 flex items-center justify-center">
        <IconBriefcase className="w-7 h-7 text-zinc-500/50" />
      </div>
      <p className="text-sm font-sans text-zinc-500">No open positions</p>
      <p className="text-[11px] font-sans text-zinc-500/60">
        Use the Quick Order form to enter a trade
      </p>
    </div>
  )
}

// ── Trade log empty state ──────────────────────────────────────────────────────

function TradesEmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-14 gap-3">
      <div className="w-14 h-14 rounded-2xl bg-zinc-800/30 flex items-center justify-center">
        <IconArrows className="w-7 h-7 text-zinc-500/50" />
      </div>
      <p className="text-sm font-sans text-zinc-500">No trades yet</p>
      <p className="text-[11px] font-sans text-zinc-500/60">
        Executed orders will appear here
      </p>
    </div>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────────

export default function TradeBotPage() {
  const { account, positions, setTrades, trades } = useAccountStore()
  const { simMode } = useBotStore()
  const { simAccount, simPositions, setSimAccount, setSimPositions } = useSimStore()
  const [initialLoad, setInitialLoad] = useState(true)

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
        // Live mode: fetch real account + positions
        try {
          const [acc, pos] = await Promise.all([fetchAccountSummary(), fetchPositions()])
          useAccountStore.getState().setAccount(acc)
          useAccountStore.getState().setPositions(pos)
        } catch { /* ignore */ }
      }
      setInitialLoad(false)
    }
    load()
    const t = setInterval(load, 10_000)  // poll every 10s
    return () => clearInterval(t)
  }, [simMode, setTrades, setSimAccount, setSimPositions])

  return (
    <div className="flex flex-col gap-5">

      {/* ── KPI header ───────────────────────────────────────────────────── */}
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
            {/* Net Liquidation — indigo accent */}
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

            {/* Cash — blue accent */}
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

            {/* Unrealized P&L — green/red accent */}
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

            {/* Realized P&L — green/red accent */}
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

      {/* ── Master toggle ─────────────────────────────────────────────────── */}
      <section className="animate-fade-in-up" style={{ animationDelay: '40ms' }}>
        <BotToggle />
      </section>

      {/* ── Quick order ───────────────────────────────────────────────────── */}
      <section
        className="bg-zinc-900/80 border border-zinc-800 rounded-2xl p-5 animate-fade-in-up"
        style={{ animationDelay: '80ms' }}
      >
        <SectionHeader
          icon={<IconLightning className="w-3.5 h-3.5 text-amber-600" />}
          title="Quick Order"
        />
        <QuickOrderForm />
      </section>

      {/* ── Live Activity Feed ──────────────────────────────────────────── */}
      <section className="animate-fade-in-up" style={{ animationDelay: '100ms' }}>
        <LiveActivityFeed />
      </section>

      {/* ── Positions table ───────────────────────────────────────────────── */}
      <section
        className="bg-zinc-900/80 border border-zinc-800 rounded-2xl p-5 animate-fade-in-up"
        style={{ animationDelay: '120ms' }}
      >
        <SectionHeader
          icon={<IconBriefcase className="w-3.5 h-3.5 text-zinc-400" />}
          title="Open Positions"
          badge={
            positions.length > 0 ? (
              <span className="ml-auto text-[11px] font-mono text-zinc-500 bg-zinc-800/50 px-2 py-0.5 rounded-lg">
                {positions.length}
              </span>
            ) : undefined
          }
        />
        {positions.length === 0 && !initialLoad
          ? <PositionsEmptyState />
          : <PositionsTable />
        }
      </section>

      {/* ── Trade log ─────────────────────────────────────────────────────── */}
      <section
        className="bg-zinc-900/80 border border-zinc-800 rounded-2xl p-5 animate-fade-in-up"
        style={{ animationDelay: '160ms' }}
      >
        <SectionHeader
          icon={<IconArrows className="w-3.5 h-3.5 text-zinc-400" />}
          title="Recent Trades"
          badge={
            trades.length > 0 ? (
              <span className="ml-auto text-[11px] font-mono text-zinc-500 bg-zinc-800/50 px-2 py-0.5 rounded-lg">
                {trades.length > 30 ? '30+' : trades.length}
              </span>
            ) : undefined
          }
        />

        {initialLoad ? (
          <div className="space-y-2 pt-1">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="flex gap-3 items-center animate-pulse">
                <div className="h-3 w-24 rounded-lg bg-zinc-800/40" />
                <div className="h-3 w-12 rounded-lg bg-zinc-800/30" />
                <div className="h-5 w-10 rounded-lg bg-zinc-800/20" />
                <div className="h-3 w-8 rounded-lg bg-zinc-800/30 ml-auto" />
                <div className="h-3 w-16 rounded-lg bg-zinc-800/20" />
                <div className="h-5 w-16 rounded-lg bg-zinc-800/20" />
              </div>
            ))}
          </div>
        ) : trades.length === 0 ? (
          <TradesEmptyState />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px]">
              <thead>
                <tr className="border-b border-zinc-800">
                  {['Time', 'Symbol', 'Side', 'Qty', 'Fill Price', 'Status'].map((c, i) => (
                    <th
                      key={c}
                      className={clsx(
                        'py-2 px-3 text-[10px] font-sans font-medium uppercase tracking-widest text-zinc-500',
                        i === 0 || i === 1 || i === 2 ? 'text-left' : 'text-right',
                      )}
                    >
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.slice(0, 30).map((t) => (
                  <TradeRow key={t.id} trade={t} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

    </div>
  )
}
