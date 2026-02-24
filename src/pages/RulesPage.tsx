/**
 * RulesPage — Automation Rules engine.
 *
 * Two views:
 *  1. List view   — shows all rules with enable/disable toggle, edit, delete
 *  2. Builder view — create or edit a rule (conditions, logic, action, cooldown)
 */
import React, { useEffect, useState } from 'react'
import clsx from 'clsx'
import { useBotStore } from '@/store'
import {
  fetchRules,
  createRule,
  updateRule as apiUpdateRule,
  deleteRule,
  toggleRule,
} from '@/services/api'
import type { Rule, Condition, RuleCreate } from '@/types'

// ── Constants ─────────────────────────────────────────────────────────────────

const INDICATORS = ['RSI', 'SMA', 'EMA', 'MACD', 'BBANDS', 'ATR', 'STOCH', 'PRICE'] as const
const OPERATORS  = ['crosses_above', 'crosses_below', '>', '<', '>=', '<=', '=='] as const

// ── Helpers ───────────────────────────────────────────────────────────────────

function defaultCondition(): Condition {
  return { indicator: 'RSI', params: { length: 14 }, operator: 'crosses_below', value: 30 }
}

// ── Condition Row (used inside RuleBuilder) ───────────────────────────────────

interface CondRowProps {
  cond:     Condition
  idx:      number
  total:    number
  onChange: (idx: number, c: Condition) => void
  onRemove: (idx: number) => void
}

function ConditionRow({ cond, idx, total, onChange, onRemove }: CondRowProps) {
  const paramVal = Object.values(cond.params)[0] ?? ''

  const update = (patch: Partial<Condition>) => onChange(idx, { ...cond, ...patch })

  return (
    <div className="grid gap-2 items-end" style={{ gridTemplateColumns: '1fr 72px 1fr 1fr 32px' }}>
      {/* Indicator */}
      <div className="flex flex-col gap-1">
        {idx === 0 && (
          <label className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">
            Indicator
          </label>
        )}
        <select
          value={cond.indicator}
          onChange={(e) => update({ indicator: e.target.value as Condition['indicator'] })}
          className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
        >
          {INDICATORS.map((i) => <option key={i}>{i}</option>)}
        </select>
      </div>

      {/* Period / param */}
      <div className="flex flex-col gap-1">
        {idx === 0 && (
          <label className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">
            Period
          </label>
        )}
        <input
          type="number"
          value={String(paramVal)}
          onChange={(e) => {
            const key = ['RSI', 'SMA', 'EMA', 'ATR', 'STOCH'].includes(cond.indicator)
              ? 'length'
              : 'period'
            update({ params: e.target.value ? { [key]: parseInt(e.target.value) } : {} })
          }}
          placeholder="14"
          className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
        />
      </div>

      {/* Operator */}
      <div className="flex flex-col gap-1">
        {idx === 0 && (
          <label className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">
            Operator
          </label>
        )}
        <select
          value={cond.operator}
          onChange={(e) => update({ operator: e.target.value })}
          className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
        >
          {OPERATORS.map((o) => <option key={o}>{o}</option>)}
        </select>
      </div>

      {/* Value */}
      <div className="flex flex-col gap-1">
        {idx === 0 && (
          <label className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">
            Value
          </label>
        )}
        <input
          type="text"
          value={String(cond.value)}
          onChange={(e) => {
            const v = e.target.value
            update({ value: v !== '' && !isNaN(Number(v)) ? Number(v) : v })
          }}
          placeholder="30"
          className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
        />
      </div>

      {/* Remove */}
      <div className={idx === 0 ? 'mt-5' : ''}>
        <button
          type="button"
          onClick={() => onRemove(idx)}
          disabled={total <= 1}
          title="Remove condition"
          className="w-8 h-8 flex items-center justify-center text-xl leading-none text-terminal-dim hover:text-terminal-red disabled:opacity-30 disabled:cursor-not-allowed transition-colors rounded"
        >
          ×
        </button>
      </div>
    </div>
  )
}

// ── Rule Builder ──────────────────────────────────────────────────────────────

interface BuilderProps {
  existing: Rule | null
  onSaved:  (rule: Rule) => void
  onCancel: () => void
}

function RuleBuilder({ existing, onSaved, onCancel }: BuilderProps) {
  const [name,        setName]        = useState(existing?.name ?? '')
  const [symbol,      setSymbol]      = useState(existing?.symbol ?? 'AAPL')
  const [logic,       setLogic]       = useState<'AND' | 'OR'>(existing?.logic ?? 'AND')
  const [conditions,  setConditions]  = useState<Condition[]>(
    existing?.conditions.length ? existing.conditions : [defaultCondition()],
  )
  const [actionType,  setActionType]  = useState<'BUY' | 'SELL'>(existing?.action.type ?? 'BUY')
  const [assetType,   setAssetType]   = useState<'STK' | 'OPT' | 'FUT'>(existing?.action.asset_type ?? 'STK')
  const [quantity,    setQuantity]    = useState(existing?.action.quantity ?? 100)
  const [orderType,   setOrderType]   = useState<'MKT' | 'LMT'>(existing?.action.order_type ?? 'MKT')
  const [limitPrice,  setLimitPrice]  = useState(String(existing?.action.limit_price ?? ''))
  const [cooldown,    setCooldown]    = useState(existing?.cooldown_minutes ?? 60)
  const [busy,        setBusy]        = useState(false)
  const [error,       setError]       = useState('')

  const updateCondition = (idx: number, c: Condition) =>
    setConditions((prev) => prev.map((x, i) => (i === idx ? c : x)))

  const removeCondition = (idx: number) =>
    setConditions((prev) => prev.filter((_, i) => i !== idx))

  const addCondition = () =>
    setConditions((prev) => [...prev, defaultCondition()])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || !symbol.trim()) return
    setBusy(true)
    setError('')
    try {
      const payload: RuleCreate = {
        name:    name.trim(),
        symbol:  symbol.trim().toUpperCase(),
        enabled: existing?.enabled ?? false,
        conditions,
        logic,
        action: {
          type:        actionType,
          asset_type:  assetType,
          quantity,
          order_type:  orderType,
          limit_price: orderType === 'LMT' && limitPrice ? parseFloat(limitPrice) : undefined,
        },
        cooldown_minutes: cooldown,
      }
      const saved = existing
        ? await apiUpdateRule(existing.id, payload)
        : await createRule(payload)
      onSaved(saved)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Error saving rule')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="bg-terminal-surface border border-terminal-border rounded-lg p-5 max-w-2xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-sm font-mono font-semibold text-terminal-text">
          {existing ? 'Edit Rule' : 'New Rule'}
        </h2>
        <button
          onClick={onCancel}
          className="text-xs font-mono text-terminal-dim hover:text-terminal-text transition-colors"
        >
          ← Back
        </button>
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-5">
        {/* Name + Symbol */}
        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">
              Rule Name
            </label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="RSI Oversold Buy"
              required
              className="text-sm font-mono bg-terminal-input border border-terminal-border rounded px-3 py-2 text-terminal-text focus:border-terminal-blue focus:outline-none"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">
              Symbol
            </label>
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              placeholder="AAPL"
              required
              className="text-sm font-mono bg-terminal-input border border-terminal-border rounded px-3 py-2 text-terminal-text focus:border-terminal-blue focus:outline-none"
            />
          </div>
        </div>

        {/* Conditions */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <span className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">
              Conditions
            </span>
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-mono text-terminal-ghost">Logic:</span>
              <div className="flex">
                {(['AND', 'OR'] as const).map((l) => (
                  <button
                    key={l}
                    type="button"
                    onClick={() => setLogic(l)}
                    className={clsx(
                      'text-xs font-mono px-2.5 py-1 border transition-colors first:rounded-l last:rounded-r',
                      logic === l
                        ? 'bg-terminal-blue/20 border-terminal-blue/40 text-terminal-blue'
                        : 'border-terminal-border text-terminal-ghost hover:text-terminal-dim',
                    )}
                  >
                    {l}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="flex flex-col gap-2 bg-terminal-elevated rounded-lg p-3">
            {conditions.map((c, i) => (
              <React.Fragment key={i}>
                {i > 0 && (
                  <div className="text-[10px] font-mono text-terminal-ghost text-center py-0.5">
                    {logic}
                  </div>
                )}
                <ConditionRow
                  cond={c}
                  idx={i}
                  total={conditions.length}
                  onChange={updateCondition}
                  onRemove={removeCondition}
                />
              </React.Fragment>
            ))}
          </div>
          <button
            type="button"
            onClick={addCondition}
            className="mt-2 text-xs font-mono px-3 py-1.5 w-full rounded border border-dashed border-terminal-border text-terminal-ghost hover:text-terminal-dim hover:border-terminal-muted transition-colors"
          >
            + Add Condition
          </button>
        </div>

        {/* Action */}
        <div>
          <span className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest block mb-2">
            Action
          </span>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 bg-terminal-elevated rounded-lg p-3">
            {/* Side */}
            <div className="flex flex-col gap-1">
              <label className="text-[10px] font-mono text-terminal-ghost uppercase">Side</label>
              <div className="flex">
                {(['BUY', 'SELL'] as const).map((a) => (
                  <button
                    key={a}
                    type="button"
                    onClick={() => setActionType(a)}
                    className={clsx(
                      'flex-1 text-xs font-mono py-1.5 border transition-colors first:rounded-l last:rounded-r',
                      actionType === a && a === 'BUY'
                        ? 'bg-terminal-green/10 border-terminal-green/40 text-terminal-green'
                        : actionType === a && a === 'SELL'
                        ? 'bg-terminal-red/10 border-terminal-red/40 text-terminal-red'
                        : 'border-terminal-border text-terminal-ghost hover:text-terminal-dim',
                    )}
                  >
                    {a}
                  </button>
                ))}
              </div>
            </div>

            {/* Asset type */}
            <div className="flex flex-col gap-1">
              <label className="text-[10px] font-mono text-terminal-ghost uppercase">Asset</label>
              <select
                value={assetType}
                onChange={(e) => setAssetType(e.target.value as 'STK' | 'OPT' | 'FUT')}
                className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
              >
                {(['STK', 'OPT', 'FUT'] as const).map((t) => <option key={t}>{t}</option>)}
              </select>
            </div>

            {/* Quantity */}
            <div className="flex flex-col gap-1">
              <label className="text-[10px] font-mono text-terminal-ghost uppercase">Qty</label>
              <input
                type="number"
                min={1}
                value={quantity}
                onChange={(e) => setQuantity(parseInt(e.target.value) || 1)}
                className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
              />
            </div>

            {/* Order type */}
            <div className="flex flex-col gap-1">
              <label className="text-[10px] font-mono text-terminal-ghost uppercase">Order Type</label>
              <select
                value={orderType}
                onChange={(e) => setOrderType(e.target.value as 'MKT' | 'LMT')}
                className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
              >
                <option>MKT</option>
                <option>LMT</option>
              </select>
            </div>

            {/* Limit price (conditional) */}
            {orderType === 'LMT' && (
              <div className="flex flex-col gap-1 col-span-2 md:col-span-4">
                <label className="text-[10px] font-mono text-terminal-ghost uppercase">Limit Price</label>
                <input
                  type="number"
                  step="0.01"
                  value={limitPrice}
                  onChange={(e) => setLimitPrice(e.target.value)}
                  placeholder="0.00"
                  className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none w-36"
                />
              </div>
            )}
          </div>
        </div>

        {/* Cooldown */}
        <div className="flex flex-col gap-1 w-52">
          <label className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">
            Cooldown (minutes)
          </label>
          <input
            type="number"
            min={1}
            value={cooldown}
            onChange={(e) => setCooldown(parseInt(e.target.value) || 1)}
            className="text-sm font-mono bg-terminal-input border border-terminal-border rounded px-3 py-2 text-terminal-text focus:border-terminal-blue focus:outline-none"
          />
          <p className="text-[10px] font-mono text-terminal-ghost">
            Minimum time between consecutive triggers
          </p>
        </div>

        {/* Error */}
        {error && (
          <p className="text-xs font-mono text-terminal-red">{error}</p>
        )}

        {/* Submit / Cancel */}
        <div className="flex gap-3 pt-1">
          <button
            type="submit"
            disabled={busy}
            className="flex-1 text-sm font-mono py-2.5 rounded bg-terminal-blue/20 border border-terminal-blue/40 text-terminal-blue hover:bg-terminal-blue/30 disabled:opacity-40 transition-colors"
          >
            {busy ? 'Saving…' : (existing ? 'Save Changes' : 'Create Rule')}
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="px-5 text-sm font-mono py-2.5 rounded border border-terminal-border text-terminal-dim hover:text-terminal-text transition-colors"
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  )
}

// ── Rule Card ─────────────────────────────────────────────────────────────────

interface CardProps {
  rule:     Rule
  onEdit:   (rule: Rule) => void
  onDelete: (rule: Rule) => void
  onToggle: (rule: Rule) => void
}

function RuleCard({ rule, onEdit, onDelete, onToggle }: CardProps) {
  return (
    <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4 flex gap-4 hover:border-terminal-muted transition-colors">
      <div className="flex-1 min-w-0">
        {/* Name + meta */}
        <div className="mb-2">
          <span className="font-mono font-semibold text-sm text-terminal-text">{rule.name}</span>
          <div className="flex items-center flex-wrap gap-1.5 mt-1">
            <span className="text-xs font-mono font-semibold text-terminal-text">{rule.symbol}</span>
            <span className="text-terminal-ghost text-xs">·</span>
            <span
              className={clsx(
                'text-xs font-mono font-semibold',
                rule.action.type === 'BUY' ? 'text-terminal-green' : 'text-terminal-red',
              )}
            >
              {rule.action.type}
            </span>
            <span className="text-terminal-ghost text-xs">·</span>
            <span className="text-xs font-mono text-terminal-dim">
              {rule.action.quantity} {rule.action.order_type}
            </span>
            <span className="text-terminal-ghost text-xs">·</span>
            <span className="text-[10px] font-mono text-terminal-ghost">
              {rule.conditions.length} condition{rule.conditions.length !== 1 ? 's' : ''}
            </span>
            <span className="text-terminal-ghost text-xs">·</span>
            <span className="text-[10px] font-mono text-terminal-ghost">
              cooldown {rule.cooldown_minutes}m
            </span>
          </div>
        </div>

        {/* Condition tags */}
        <div className="flex flex-wrap gap-1 mb-3">
          {rule.conditions.map((c, i) => (
            <React.Fragment key={i}>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-terminal-elevated text-terminal-dim border border-terminal-border">
                {c.indicator}
                {Object.values(c.params)[0] != null ? `(${Object.values(c.params)[0]})` : ''}
                {' '}{c.operator}{' '}{c.value}
              </span>
              {i < rule.conditions.length - 1 && (
                <span className="text-[10px] font-mono text-terminal-ghost self-center">
                  {rule.logic}
                </span>
              )}
            </React.Fragment>
          ))}
        </div>

        {/* Last triggered */}
        {rule.last_triggered && (
          <p className="text-[10px] font-mono text-terminal-ghost mb-2">
            Last triggered: {new Date(rule.last_triggered).toLocaleString()}
          </p>
        )}

        {/* Action buttons */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => onEdit(rule)}
            className="text-[11px] font-mono px-3 py-1 rounded border border-terminal-border text-terminal-dim hover:text-terminal-text hover:border-terminal-muted transition-colors"
          >
            Edit
          </button>
          <button
            onClick={() => onDelete(rule)}
            className="text-[11px] font-mono px-3 py-1 rounded border border-terminal-red/30 text-terminal-red/70 hover:bg-terminal-red/5 hover:text-terminal-red transition-colors"
          >
            Delete
          </button>
          <span
            className={clsx(
              'ml-auto text-[10px] font-mono px-1.5 py-0.5 rounded',
              rule.enabled
                ? 'bg-terminal-green/10 text-terminal-green'
                : 'bg-terminal-muted text-terminal-ghost',
            )}
          >
            {rule.enabled ? 'ENABLED' : 'DISABLED'}
          </span>
        </div>
      </div>

      {/* Enable/disable toggle */}
      <div className="flex items-start pt-0.5 shrink-0">
        <button
          onClick={() => onToggle(rule)}
          aria-label={rule.enabled ? 'Disable rule' : 'Enable rule'}
          className={clsx(
            'relative w-11 h-6 rounded-full border-2 transition-all duration-200 focus:outline-none',
            rule.enabled
              ? 'bg-terminal-green border-terminal-green shadow-glow-green'
              : 'bg-terminal-muted border-terminal-border',
          )}
        >
          <span
            className={clsx(
              'absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform duration-200',
              rule.enabled ? 'translate-x-5' : 'translate-x-0.5',
            )}
          />
        </button>
      </div>
    </div>
  )
}

// ── Delete confirm dialog ─────────────────────────────────────────────────────

interface DeleteConfirmProps {
  rule:      Rule
  onConfirm: () => void
  onCancel:  () => void
}

function DeleteConfirm({ rule, onConfirm, onCancel }: DeleteConfirmProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-terminal-elevated border border-terminal-border rounded-xl p-6 w-full max-w-sm shadow-terminal">
        <h3 className="font-mono font-semibold text-terminal-text mb-2">Delete Rule</h3>
        <p className="text-sm font-mono text-terminal-dim mb-5">
          Delete <strong className="text-terminal-text">{rule.name}</strong>? This cannot be undone.
        </p>
        <div className="flex gap-3">
          <button
            onClick={onCancel}
            className="flex-1 text-sm font-mono py-2 rounded border border-terminal-border text-terminal-dim hover:text-terminal-text transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="flex-1 text-sm font-mono py-2 rounded bg-terminal-red/15 border border-terminal-red/30 text-terminal-red hover:bg-terminal-red/25 transition-colors"
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Toast ─────────────────────────────────────────────────────────────────────

function Toast({ message }: { message: string }) {
  if (!message) return null
  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 bg-terminal-elevated border border-terminal-border rounded-lg px-4 py-2.5 shadow-terminal pointer-events-none">
      <span className="text-xs font-mono text-terminal-text">{message}</span>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

type ViewMode = 'list' | 'builder'

export default function RulesPage() {
  const { rules, setRules, updateRule } = useBotStore()
  const [view,     setView]     = useState<ViewMode>('list')
  const [editing,  setEditing]  = useState<Rule | null>(null)
  const [deleting, setDeleting] = useState<Rule | null>(null)
  const [loading,  setLoading]  = useState(false)
  const [toast,    setToast]    = useState('')

  // ── Toast helper ─────────────────────────────────────────────────────────

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(''), 2500)
  }

  // ── Load rules on mount ───────────────────────────────────────────────────

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try {
        const fetched = await fetchRules()
        setRules(fetched)
      } catch {
        // Backend offline — use whatever is already in store
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [setRules])

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleNewRule = () => {
    setEditing(null)
    setView('builder')
  }

  const handleEdit = (rule: Rule) => {
    setEditing(rule)
    setView('builder')
  }

  const handleSaved = (savedRule: Rule) => {
    if (editing) {
      updateRule(savedRule)
      showToast('Rule updated')
    } else {
      // Use current snapshot from store to avoid stale closure
      const latest = useBotStore.getState().rules
      setRules([...latest, savedRule])
      showToast('Rule created')
    }
    setEditing(null)
    setView('list')
  }

  const handleCancel = () => {
    setEditing(null)
    setView('list')
  }

  const handleToggle = async (rule: Rule) => {
    try {
      const res = await toggleRule(rule.id)
      updateRule({ ...rule, enabled: res.enabled })
      showToast(res.enabled ? 'Rule enabled' : 'Rule disabled')
    } catch (err: unknown) {
      showToast(err instanceof Error ? err.message : 'Error toggling rule')
    }
  }

  const handleDeleteRequest = (rule: Rule) => {
    setDeleting(rule)
  }

  const handleDeleteConfirm = async () => {
    if (!deleting) return
    const target = deleting
    setDeleting(null)
    try {
      await deleteRule(target.id)
      setRules(useBotStore.getState().rules.filter((r) => r.id !== target.id))
      showToast('Rule deleted')
    } catch (err: unknown) {
      showToast(err instanceof Error ? err.message : 'Error deleting rule')
    }
  }

  // ── Builder view ──────────────────────────────────────────────────────────

  if (view === 'builder') {
    return (
      <div className="max-w-2xl">
        <RuleBuilder
          existing={editing}
          onSaved={handleSaved}
          onCancel={handleCancel}
        />
        <Toast message={toast} />
      </div>
    )
  }

  // ── List view ─────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-4 pb-8">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-sm font-mono font-semibold text-terminal-text">
            Automation Rules
          </h1>
          <p className="text-xs font-mono text-terminal-ghost mt-0.5">
            Define conditions that trigger automatic orders
          </p>
        </div>
        <button
          onClick={handleNewRule}
          className="text-xs font-mono px-4 py-2 rounded bg-terminal-blue/20 border border-terminal-blue/40 text-terminal-blue hover:bg-terminal-blue/30 transition-colors"
        >
          + New Rule
        </button>
      </div>

      {/* Rules list / states */}
      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-28 bg-terminal-surface border border-terminal-border rounded-lg animate-pulse"
            />
          ))}
        </div>
      ) : rules.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-terminal-ghost">
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-12 h-12 mb-3 opacity-20">
            <path d="M3 18h6v-2H3v2zM3 6v2h18V6H3zm0 7h12v-2H3v2z" />
          </svg>
          <p className="text-sm font-mono mb-1">No rules yet</p>
          <p className="text-[11px] font-mono text-terminal-ghost/60 mb-4">
            Create a rule to automate your trading
          </p>
          <button
            onClick={handleNewRule}
            className="text-xs font-mono px-4 py-2 rounded bg-terminal-blue/20 border border-terminal-blue/40 text-terminal-blue hover:bg-terminal-blue/30 transition-colors"
          >
            + New Rule
          </button>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {rules.map((rule) => (
            <RuleCard
              key={rule.id}
              rule={rule}
              onEdit={handleEdit}
              onDelete={handleDeleteRequest}
              onToggle={handleToggle}
            />
          ))}
        </div>
      )}

      {/* Delete confirmation */}
      {deleting && (
        <DeleteConfirm
          rule={deleting}
          onConfirm={handleDeleteConfirm}
          onCancel={() => setDeleting(null)}
        />
      )}

      {/* Toast */}
      <Toast message={toast} />
    </div>
  )
}
