/**
 * TradeBotPage — Command Center for automated trading.
 *
 * Sections:
 *  1. KPI header (Net Liq, Cash, Unrealized P&L, Realized P&L)
 *  2. Master Toggle (Automated Trading On/Off)
 *  3. Positions table
 *  4. Recent trades log
 */
import React, { useEffect, useState } from 'react'
import clsx from 'clsx'
import KPICard from '@/components/tradebot/KPICard'
import BotToggle from '@/components/tradebot/BotToggle'
import PositionsTable from '@/components/tradebot/PositionsTable'
import { SkeletonCard, SkeletonTable } from '@/components/ui/Skeleton'
import { useToast } from '@/components/ui/ToastProvider'
import { useAccountStore, useBotStore, useSimStore } from '@/store'
import { fetchTrades, fetchSimAccount, fetchSimPositions, placeManualOrder } from '@/services/api'
import type { Trade, SimAccountState, AccountSummary } from '@/types'

function fmtUSD(v: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(v)
}

function isSimAccount(a: AccountSummary | SimAccountState): a is SimAccountState {
  return 'is_sim' in a && a.is_sim === true
}

// ── Quick Order form ──────────────────────────────────────────────────────────

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

  return (
    <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3">
      <div className="flex flex-col gap-1.5">
        <label className="text-xs font-sans font-medium text-terminal-dim tracking-wide">Symbol</label>
        <input
          value={sym}
          onChange={(e) => setSym(e.target.value)}
          placeholder="AAPL"
          className="w-24 text-xs font-mono bg-terminal-input border border-white/[0.06] rounded-xl px-3 py-1.5 text-terminal-text focus:border-indigo-500/50 focus:outline-none uppercase"
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <label className="text-xs font-sans font-medium text-terminal-dim tracking-wide">Qty</label>
        <input
          type="number"
          min={1}
          value={qty}
          onChange={(e) => setQty(Number(e.target.value))}
          className="w-20 text-xs font-mono bg-terminal-input border border-white/[0.06] rounded-xl px-3 py-1.5 text-terminal-text focus:border-indigo-500/50 focus:outline-none"
        />
      </div>
      <div className="flex gap-1.5">
        {(['BUY', 'SELL'] as const).map((a) => (
          <button
            key={a}
            type="button"
            onClick={() => setAction(a)}
            className={clsx(
              'text-xs font-sans font-medium px-3 py-1.5 rounded-xl border transition-colors',
              action === a && a === 'BUY'
                ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-400'
                : action === a && a === 'SELL'
                ? 'border-red-500/40 bg-red-500/15 text-red-400'
                : 'border-white/[0.06] text-terminal-ghost hover:text-terminal-dim',
            )}
          >
            {a}
          </button>
        ))}
      </div>
      <button
        type="submit"
        disabled={busy}
        className="text-xs font-sans font-medium px-4 py-1.5 rounded-xl bg-indigo-500/15 text-indigo-400 hover:bg-indigo-500/25 disabled:opacity-40 transition-colors"
      >
        {busy ? 'Placing…' : 'Place Order'}
      </button>
      {status && (
        <span className="text-[11px] font-sans text-terminal-dim">{status}</span>
      )}
    </form>
  )
}

// ── Trade log row ─────────────────────────────────────────────────────────────

function TradeRow({ trade }: { trade: Trade }) {
  const up = trade.action === 'BUY'
  const statusColor: Record<string, string> = {
    FILLED:    'text-emerald-400',
    PENDING:   'text-terminal-amber',
    CANCELLED: 'text-terminal-dim',
    ERROR:     'text-red-400',
  }

  return (
    <tr className="border-b border-white/[0.06] hover:bg-white/[0.03] transition-colors">
      <td className="py-2 px-3 font-mono text-[11px] text-terminal-dim tabular-nums">
        {new Date(trade.timestamp).toLocaleTimeString()}
      </td>
      <td className="py-2 px-3 font-mono text-xs font-semibold text-terminal-text">{trade.symbol}</td>
      <td className={clsx('py-2 px-3 font-mono text-xs font-semibold', up ? 'text-emerald-400' : 'text-red-400')}>
        {trade.action}
      </td>
      <td className="py-2 px-3 font-mono text-[11px] text-terminal-dim tabular-nums text-right">{trade.quantity}</td>
      <td className="py-2 px-3 font-mono text-[11px] text-terminal-dim tabular-nums text-right">
        {trade.fill_price != null ? fmtUSD(trade.fill_price) : '—'}
      </td>
      <td className={clsx('py-2 px-3 font-mono text-[11px] text-right', statusColor[trade.status])}>
        {trade.status}
      </td>
    </tr>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

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
      }
      setInitialLoad(false)
    }
    load()
    const t = setInterval(load, 15_000)
    return () => clearInterval(t)
  }, [simMode, setTrades, setSimAccount, setSimPositions])

  return (
    <div className="flex flex-col gap-5">
      {/* ── KPI header ─────────────────────────────────────────────── */}
      <section>
        <h2 className="text-xs font-sans font-medium text-terminal-dim tracking-wide mb-3">
          Account KPIs {simMode ? '(Simulation)' : ''}
        </h2>
        {initialLoad && !displayAccount ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <SkeletonCard /><SkeletonCard /><SkeletonCard /><SkeletonCard />
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <KPICard
              label="Net Liquidation"
              value={netLiq != null ? fmtUSD(netLiq) : '—'}
              highlight
            />
            <KPICard
              label="Cash"
              value={cash != null ? fmtUSD(cash) : '—'}
            />
            <KPICard
              label="Unrealized P&L"
              value={unrealPnl != null ? fmtUSD(unrealPnl) : '—'}
              positive={unrealPnl != null ? unrealPnl >= 0 : undefined}
            />
            <KPICard
              label="Realized P&L"
              value={realPnl != null ? fmtUSD(realPnl) : '—'}
              positive={realPnl != null ? realPnl >= 0 : undefined}
            />
          </div>
        )}
      </section>

      {/* ── Master toggle ───────────────────────────────────────────── */}
      <section>
        <BotToggle />
      </section>

      {/* ── Quick order ─────────────────────────────────────────────── */}
      <section className="glass rounded-2xl shadow-glass p-5">
        <h2 className="text-xs font-sans font-medium text-terminal-dim tracking-wide mb-4">
          Quick Order
        </h2>
        <QuickOrderForm />
      </section>

      {/* ── Positions table ─────────────────────────────────────────── */}
      <section className="glass rounded-2xl shadow-glass p-5">
        <h2 className="text-xs font-sans font-medium text-terminal-dim tracking-wide mb-4">
          Open Positions
        </h2>
        <PositionsTable />
      </section>

      {/* ── Trade log ────────────────────────────────────────────────── */}
      <section className="glass rounded-2xl shadow-glass p-5">
        <h2 className="text-xs font-sans font-medium text-terminal-dim tracking-wide mb-4">
          Recent Trades
        </h2>
        {initialLoad ? (
          <SkeletonTable rows={4} cols={6} />
        ) : trades.length === 0 ? (
          <p className="text-sm font-sans text-terminal-ghost py-4 text-center">No trades yet</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[500px]">
              <thead>
                <tr className="border-b border-white/[0.06]">
                  {['Time', 'Symbol', 'Side', 'Qty', 'Fill Price', 'Status'].map((c) => (
                    <th key={c} className="py-2 px-3 text-[10px] font-sans font-medium uppercase tracking-wide text-terminal-ghost text-right first:text-left">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.slice(0, 30).map((t) => <TradeRow key={t.id} trade={t} />)}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
