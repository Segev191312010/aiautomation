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
  const [sym,   setSym]   = useState('')
  const [qty,   setQty]   = useState(1)
  const [action, setAction] = useState<'BUY' | 'SELL'>('BUY')
  const [status, setStatus] = useState('')
  const [busy,  setBusy]  = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!sym || qty <= 0) return
    setBusy(true)
    setStatus('')
    try {
      const r = await placeManualOrder({ symbol: sym.toUpperCase(), action, quantity: qty })
      setStatus(r.message ?? 'Order placed')
    } catch (e: unknown) {
      setStatus(e instanceof Error ? e.message : 'Error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-2">
      <div className="flex flex-col gap-1">
        <label className="text-[10px] font-mono text-terminal-ghost uppercase">Symbol</label>
        <input
          value={sym}
          onChange={(e) => setSym(e.target.value)}
          placeholder="AAPL"
          className="w-24 text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none uppercase"
        />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[10px] font-mono text-terminal-ghost uppercase">Qty</label>
        <input
          type="number"
          min={1}
          value={qty}
          onChange={(e) => setQty(Number(e.target.value))}
          className="w-20 text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
        />
      </div>
      <div className="flex gap-1">
        {(['BUY', 'SELL'] as const).map((a) => (
          <button
            key={a}
            type="button"
            onClick={() => setAction(a)}
            className={clsx(
              'text-xs font-mono px-3 py-1.5 rounded border transition-colors',
              action === a && a === 'BUY'
                ? 'border-terminal-green/50 bg-terminal-green/10 text-terminal-green'
                : action === a && a === 'SELL'
                ? 'border-terminal-red/50 bg-terminal-red/10 text-terminal-red'
                : 'border-terminal-border text-terminal-ghost hover:text-terminal-dim',
            )}
          >
            {a}
          </button>
        ))}
      </div>
      <button
        type="submit"
        disabled={busy}
        className="text-xs font-mono px-4 py-1.5 rounded bg-terminal-blue/20 border border-terminal-blue/40 text-terminal-blue hover:bg-terminal-blue/30 disabled:opacity-40 transition-colors"
      >
        {busy ? 'Placing…' : 'Place Order'}
      </button>
      {status && (
        <span className="text-[11px] font-mono text-terminal-dim">{status}</span>
      )}
    </form>
  )
}

// ── Trade log row ─────────────────────────────────────────────────────────────

function TradeRow({ trade }: { trade: Trade }) {
  const up = trade.action === 'BUY'
  const statusColor: Record<string, string> = {
    FILLED:    'text-terminal-green',
    PENDING:   'text-terminal-amber',
    CANCELLED: 'text-terminal-dim',
    ERROR:     'text-terminal-red',
  }

  return (
    <tr className="border-b border-terminal-border hover:bg-terminal-muted/20 transition-colors">
      <td className="py-1.5 px-3 font-mono text-[11px] text-terminal-dim tabular-nums">
        {new Date(trade.timestamp).toLocaleTimeString()}
      </td>
      <td className="py-1.5 px-3 font-mono text-xs font-semibold text-terminal-text">{trade.symbol}</td>
      <td className={clsx('py-1.5 px-3 font-mono text-xs font-semibold', up ? 'text-terminal-green' : 'text-terminal-red')}>
        {trade.action}
      </td>
      <td className="py-1.5 px-3 font-mono text-[11px] text-terminal-dim tabular-nums text-right">{trade.quantity}</td>
      <td className="py-1.5 px-3 font-mono text-[11px] text-terminal-dim tabular-nums text-right">
        {trade.fill_price != null ? fmtUSD(trade.fill_price) : '—'}
      </td>
      <td className={clsx('py-1.5 px-3 font-mono text-[11px] text-right', statusColor[trade.status])}>
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
    }
    load()
    const t = setInterval(load, 15_000)
    return () => clearInterval(t)
  }, [simMode, setTrades, setSimAccount, setSimPositions])

  return (
    <div className="flex flex-col gap-5">
      {/* ── KPI header ─────────────────────────────────────────────── */}
      <section>
        <h2 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest mb-3">
          Account KPIs {simMode ? '(Simulation)' : ''}
        </h2>
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
      </section>

      {/* ── Master toggle ───────────────────────────────────────────── */}
      <section>
        <BotToggle />
      </section>

      {/* ── Quick order ─────────────────────────────────────────────── */}
      <section className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
        <h2 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest mb-3">
          Quick Order
        </h2>
        <QuickOrderForm />
      </section>

      {/* ── Positions table ─────────────────────────────────────────── */}
      <section className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
        <h2 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest mb-3">
          Open Positions
        </h2>
        <PositionsTable />
      </section>

      {/* ── Trade log ────────────────────────────────────────────────── */}
      <section className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
        <h2 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest mb-3">
          Recent Trades
        </h2>
        {trades.length === 0 ? (
          <p className="text-sm font-mono text-terminal-ghost py-4 text-center">No trades yet</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[500px]">
              <thead>
                <tr className="border-b border-terminal-border">
                  {['Time', 'Symbol', 'Side', 'Qty', 'Fill Price', 'Status'].map((c) => (
                    <th key={c} className="py-1.5 px-3 text-[10px] font-mono uppercase tracking-widest text-terminal-ghost font-normal text-right first:text-left">
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
