/**
 * RuleForm — create / edit a trading rule.
 *
 * Drives the full RuleCreate shape: name, symbol, one-or-more conditions
 * (indicator / operator / value), AND/OR logic, action (side / qty / order
 * type / optional limit), and a cooldown. Used both for new rules and for
 * editing an existing one (seeded via `initial`).
 */
import React, { useState } from 'react'
import clsx from 'clsx'
import type { Condition, Indicator, OrderAction, OrderType, AssetType, Rule, RuleCreate } from '@/types'

const INDICATORS: Indicator[] = ['RSI', 'SMA', 'EMA', 'MACD', 'BBANDS', 'ATR', 'STOCH', 'PRICE']
const OPERATORS = ['>', '>=', '<', '<=', '==', 'crosses_above', 'crosses_below'] as const

// Default `params` per indicator so the backend gets a sane period/length.
function defaultParams(ind: Indicator): Record<string, number | string> {
  switch (ind) {
    case 'RSI':    return { period: 14 }
    case 'SMA':    return { period: 20 }
    case 'EMA':    return { period: 20 }
    case 'MACD':   return { fast: 12, slow: 26, signal: 9 }
    case 'BBANDS': return { period: 20, stddev: 2 }
    case 'ATR':    return { period: 14 }
    case 'STOCH':  return { k: 14, d: 3 }
    case 'PRICE':  return {}
    default:       return {}
  }
}

function blankCondition(): Condition {
  return { indicator: 'RSI', params: defaultParams('RSI'), operator: '<', value: 30 }
}

const inputCls =
  'text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 ' +
  'text-terminal-text focus:border-terminal-blue focus:outline-none'

const labelCls = 'text-[10px] font-mono text-terminal-ghost uppercase tracking-wide'

interface Props {
  initial?: Rule
  busy?: boolean
  onSubmit: (body: RuleCreate) => void | Promise<void>
  onCancel: () => void
}

export default function RuleForm({ initial, busy, onSubmit, onCancel }: Props) {
  const [name, setName]       = useState(initial?.name ?? '')
  const [symbol, setSymbol]   = useState(initial?.symbol ?? '')
  const [logic, setLogic]     = useState<'AND' | 'OR'>(initial?.logic ?? 'AND')
  const [conditions, setConditions] = useState<Condition[]>(
    initial?.conditions?.length ? initial.conditions.map((c) => ({ ...c })) : [blankCondition()],
  )
  const [actionType, setActionType]   = useState<OrderAction>(initial?.action.type ?? 'BUY')
  const [quantity, setQuantity]       = useState<number>(initial?.action.quantity ?? 1)
  const [orderType, setOrderType]     = useState<OrderType>(initial?.action.order_type ?? 'MKT')
  const [limitPrice, setLimitPrice]   = useState<number>(initial?.action.limit_price ?? 0)
  const [assetType] = useState<AssetType>(initial?.action.asset_type ?? 'STK')
  const [cooldown, setCooldown]       = useState<number>(initial?.cooldown_minutes ?? 60)
  const [err, setErr] = useState<string | null>(null)

  const setCond = (i: number, patch: Partial<Condition>) =>
    setConditions((cs) => cs.map((c, idx) => (idx === i ? { ...c, ...patch } : c)))

  const onIndicatorChange = (i: number, ind: Indicator) =>
    setCond(i, { indicator: ind, params: defaultParams(ind) })

  const addCondition    = () => setConditions((cs) => [...cs, blankCondition()])
  const removeCondition = (i: number) => setConditions((cs) => (cs.length > 1 ? cs.filter((_, idx) => idx !== i) : cs))

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr(null)
    if (!name.trim())   { setErr('Name is required'); return }
    if (!symbol.trim()) { setErr('Symbol is required'); return }
    if (quantity <= 0)  { setErr('Quantity must be greater than 0'); return }
    if (orderType === 'LMT' && limitPrice <= 0) { setErr('Limit price is required for LMT orders'); return }

    const body: RuleCreate = {
      name: name.trim(),
      symbol: symbol.trim().toUpperCase(),
      enabled: initial?.enabled ?? true,
      conditions: conditions.map((c) => ({
        indicator: c.indicator,
        params: c.params,
        operator: c.operator,
        // numeric strings → numbers; keep non-numeric (rare) as-is
        value: typeof c.value === 'string' && c.value.trim() !== '' && !Number.isNaN(Number(c.value))
          ? Number(c.value)
          : c.value,
      })),
      logic,
      action: {
        type: actionType,
        asset_type: assetType,
        quantity,
        order_type: orderType,
        ...(orderType === 'LMT' ? { limit_price: limitPrice } : {}),
      },
      cooldown_minutes: cooldown,
    }

    try {
      await onSubmit(body)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Failed to save rule')
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      {/* ── identity ───────────────────────────────────────────── */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="flex flex-col gap-1 sm:col-span-2">
          <label className={labelCls}>Rule Name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="RSI oversold reversal"
            className={inputCls}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className={labelCls}>Symbol</label>
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            placeholder="AAPL"
            className={clsx(inputCls, 'uppercase')}
          />
        </div>
      </div>

      {/* ── conditions ─────────────────────────────────────────── */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <label className={labelCls}>Conditions</label>
          {conditions.length > 1 && (
            <div className="flex gap-1">
              {(['AND', 'OR'] as const).map((l) => (
                <button
                  key={l}
                  type="button"
                  onClick={() => setLogic(l)}
                  className={clsx(
                    'text-[10px] font-mono px-2.5 py-1 rounded border transition-colors',
                    logic === l
                      ? 'border-terminal-blue/50 bg-terminal-blue/10 text-terminal-blue'
                      : 'border-terminal-border text-terminal-ghost hover:text-terminal-dim',
                  )}
                >
                  {l}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-col gap-2">
          {conditions.map((c, i) => (
            <div
              key={i}
              className="flex flex-wrap items-end gap-2 bg-terminal-bg/40 border border-terminal-border rounded p-2.5"
            >
              {i > 0 && (
                <span className="text-[10px] font-mono text-terminal-blue uppercase self-center w-8">{logic}</span>
              )}
              <div className="flex flex-col gap-1">
                <span className={labelCls}>Indicator</span>
                <select
                  value={c.indicator}
                  onChange={(e) => onIndicatorChange(i, e.target.value as Indicator)}
                  className={clsx(inputCls, 'w-28')}
                >
                  {INDICATORS.map((ind) => <option key={ind} value={ind}>{ind}</option>)}
                </select>
              </div>
              <div className="flex flex-col gap-1">
                <span className={labelCls}>Operator</span>
                <select
                  value={c.operator}
                  onChange={(e) => setCond(i, { operator: e.target.value })}
                  className={clsx(inputCls, 'w-36')}
                >
                  {OPERATORS.map((op) => <option key={op} value={op}>{op}</option>)}
                </select>
              </div>
              <div className="flex flex-col gap-1">
                <span className={labelCls}>Value</span>
                <input
                  value={String(c.value)}
                  onChange={(e) => setCond(i, { value: e.target.value })}
                  placeholder="30"
                  className={clsx(inputCls, 'w-24')}
                />
              </div>
              <button
                type="button"
                onClick={() => removeCondition(i)}
                disabled={conditions.length === 1}
                title="Remove condition"
                className="text-xs font-mono px-2 py-1.5 rounded border border-terminal-border text-terminal-ghost hover:text-terminal-red hover:border-terminal-red/40 disabled:opacity-30 transition-colors"
              >
                ✕
              </button>
            </div>
          ))}
        </div>

        <button
          type="button"
          onClick={addCondition}
          className="self-start text-[11px] font-mono px-3 py-1 rounded border border-terminal-border text-terminal-dim hover:text-terminal-blue hover:border-terminal-blue/40 transition-colors"
        >
          + Add condition
        </button>
      </div>

      {/* ── action ─────────────────────────────────────────────── */}
      <div className="flex flex-col gap-2">
        <label className={labelCls}>Action</label>
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex gap-1">
            {(['BUY', 'SELL'] as const).map((a) => (
              <button
                key={a}
                type="button"
                onClick={() => setActionType(a)}
                className={clsx(
                  'text-xs font-mono px-3 py-1.5 rounded border transition-colors',
                  actionType === a && a === 'BUY'
                    ? 'border-terminal-green/50 bg-terminal-green/10 text-terminal-green'
                    : actionType === a && a === 'SELL'
                    ? 'border-terminal-red/50 bg-terminal-red/10 text-terminal-red'
                    : 'border-terminal-border text-terminal-ghost hover:text-terminal-dim',
                )}
              >
                {a}
              </button>
            ))}
          </div>
          <div className="flex flex-col gap-1">
            <span className={labelCls}>Quantity</span>
            <input
              type="number"
              min={1}
              value={quantity}
              onChange={(e) => setQuantity(Number(e.target.value))}
              className={clsx(inputCls, 'w-24')}
            />
          </div>
          <div className="flex flex-col gap-1">
            <span className={labelCls}>Order Type</span>
            <select
              value={orderType}
              onChange={(e) => setOrderType(e.target.value as OrderType)}
              className={clsx(inputCls, 'w-24')}
            >
              <option value="MKT">MKT</option>
              <option value="LMT">LMT</option>
            </select>
          </div>
          {orderType === 'LMT' && (
            <div className="flex flex-col gap-1">
              <span className={labelCls}>Limit Price</span>
              <input
                type="number"
                min={0}
                step="0.01"
                value={limitPrice}
                onChange={(e) => setLimitPrice(Number(e.target.value))}
                className={clsx(inputCls, 'w-28')}
              />
            </div>
          )}
          <div className="flex flex-col gap-1">
            <span className={labelCls}>Cooldown (min)</span>
            <input
              type="number"
              min={0}
              value={cooldown}
              onChange={(e) => setCooldown(Number(e.target.value))}
              className={clsx(inputCls, 'w-28')}
            />
          </div>
        </div>
      </div>

      {err && <p className="text-[11px] font-mono text-terminal-red">{err}</p>}

      {/* ── controls ───────────────────────────────────────────── */}
      <div className="flex items-center gap-2 pt-1">
        <button
          type="submit"
          disabled={busy}
          className="text-xs font-mono px-4 py-1.5 rounded bg-terminal-blue/20 border border-terminal-blue/40 text-terminal-blue hover:bg-terminal-blue/30 disabled:opacity-40 transition-colors"
        >
          {busy ? 'Saving…' : initial ? 'Save changes' : 'Create rule'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="text-xs font-mono px-4 py-1.5 rounded border border-terminal-border text-terminal-dim hover:text-terminal-text disabled:opacity-40 transition-colors"
        >
          Cancel
        </button>
      </div>
    </form>
  )
}
