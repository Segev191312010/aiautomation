/**
 * RulesPage — Full CRUD UI for automation rules.
 *
 * Displays existing rules in a table with toggle/edit/delete,
 * and a form to create or edit a rule.
 */
import React, { useEffect, useState, useCallback } from 'react'
import clsx from 'clsx'
import { useBotStore } from '@/store'
import {
  fetchRules,
  createRule,
  updateRule as apiUpdateRule,
  deleteRule as apiDeleteRule,
  toggleRule as apiToggleRule,
} from '@/services/api'
import type { Rule, RuleCreate, Condition, TradeAction, Indicator, OrderAction, OrderType, AssetType } from '@/types'

// ── Constants ────────────────────────────────────────────────────────────────

const INDICATORS: Indicator[] = ['PRICE', 'RSI', 'SMA', 'EMA', 'MACD', 'BBANDS', 'ATR', 'STOCH']

const OPERATORS = ['>', '<', '>=', '<=', '==', 'crosses_above', 'crosses_below']

const INDICATOR_PARAMS: Record<Indicator, { label: string; key: string; default: number }[]> = {
  PRICE:  [],
  RSI:    [{ label: 'Period', key: 'period', default: 14 }],
  SMA:    [{ label: 'Period', key: 'period', default: 20 }],
  EMA:    [{ label: 'Period', key: 'period', default: 12 }],
  MACD:   [{ label: 'Fast', key: 'fast', default: 12 }, { label: 'Slow', key: 'slow', default: 26 }, { label: 'Signal', key: 'signal', default: 9 }],
  BBANDS: [{ label: 'Period', key: 'period', default: 20 }, { label: 'Std Dev', key: 'std', default: 2 }],
  ATR:    [{ label: 'Period', key: 'period', default: 14 }],
  STOCH:  [{ label: 'K Period', key: 'k', default: 14 }, { label: 'D Period', key: 'd', default: 3 }],
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function emptyCondition(): Condition {
  return { indicator: 'RSI', params: { period: 14 }, operator: '<', value: 30 }
}

function emptyAction(): TradeAction {
  return { type: 'BUY', asset_type: 'STK', quantity: 1, order_type: 'MKT' }
}

// ── Condition editor row ─────────────────────────────────────────────────────

function ConditionRow({
  condition,
  onChange,
  onRemove,
}: {
  condition: Condition
  onChange: (c: Condition) => void
  onRemove: () => void
}) {
  const paramDefs = INDICATOR_PARAMS[condition.indicator] ?? []

  return (
    <div className="flex flex-wrap items-end gap-2 bg-terminal-bg/50 rounded p-2">
      <div className="flex flex-col gap-1">
        <label className="text-[10px] font-mono text-terminal-ghost uppercase">Indicator</label>
        <select
          value={condition.indicator}
          onChange={(e) => {
            const ind = e.target.value as Indicator
            const defaults: Record<string, number | string> = {}
            INDICATOR_PARAMS[ind]?.forEach((p) => { defaults[p.key] = p.default })
            onChange({ ...condition, indicator: ind, params: defaults })
          }}
          className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
        >
          {INDICATORS.map((i) => <option key={i} value={i}>{i}</option>)}
        </select>
      </div>

      {paramDefs.map((p) => (
        <div key={p.key} className="flex flex-col gap-1">
          <label className="text-[10px] font-mono text-terminal-ghost uppercase">{p.label}</label>
          <input
            type="number"
            value={Number(condition.params[p.key] ?? p.default)}
            onChange={(e) => onChange({ ...condition, params: { ...condition.params, [p.key]: Number(e.target.value) } })}
            className="w-16 text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
          />
        </div>
      ))}

      <div className="flex flex-col gap-1">
        <label className="text-[10px] font-mono text-terminal-ghost uppercase">Operator</label>
        <select
          value={condition.operator}
          onChange={(e) => onChange({ ...condition, operator: e.target.value })}
          className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
        >
          {OPERATORS.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-[10px] font-mono text-terminal-ghost uppercase">Value</label>
        <input
          type="number"
          step="any"
          value={Number(condition.value)}
          onChange={(e) => onChange({ ...condition, value: Number(e.target.value) })}
          className="w-20 text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
        />
      </div>

      <button
        type="button"
        onClick={onRemove}
        className="text-xs font-mono px-2 py-1.5 rounded border border-terminal-red/30 text-terminal-red hover:bg-terminal-red/10 transition-colors"
        title="Remove condition"
      >
        X
      </button>
    </div>
  )
}

// ── Rule form (create / edit) ────────────────────────────────────────────────

function RuleForm({
  initial,
  onSubmit,
  onCancel,
}: {
  initial?: Rule
  onSubmit: (data: RuleCreate) => Promise<void>
  onCancel: () => void
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [symbol, setSymbol] = useState(initial?.symbol ?? '')
  const [logic, setLogic] = useState<'AND' | 'OR'>(initial?.logic ?? 'AND')
  const [conditions, setConditions] = useState<Condition[]>(initial?.conditions ?? [emptyCondition()])
  const [action, setAction] = useState<TradeAction>(initial?.action ?? emptyAction())
  const [cooldown, setCooldown] = useState(initial?.cooldown_minutes ?? 5)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const updateCondition = (idx: number, c: Condition) => {
    const next = [...conditions]
    next[idx] = c
    setConditions(next)
  }

  const removeCondition = (idx: number) => {
    if (conditions.length <= 1) return
    setConditions(conditions.filter((_, i) => i !== idx))
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || !symbol.trim() || conditions.length === 0) {
      setError('Name, symbol, and at least one condition are required.')
      return
    }
    setBusy(true)
    setError('')
    try {
      await onSubmit({ name, symbol: symbol.toUpperCase(), conditions, logic, action, cooldown_minutes: cooldown })
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to save rule')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="bg-terminal-surface border border-terminal-border rounded-lg p-5 space-y-4">
      <h3 className="text-sm font-mono font-semibold text-terminal-text">
        {initial ? 'Edit Rule' : 'New Rule'}
      </h3>

      {/* Name + Symbol */}
      <div className="flex flex-wrap gap-3">
        <div className="flex flex-col gap-1 flex-1 min-w-[200px]">
          <label className="text-[10px] font-mono text-terminal-ghost uppercase">Rule Name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="RSI Oversold Buy"
            className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
          />
        </div>
        <div className="flex flex-col gap-1 w-32">
          <label className="text-[10px] font-mono text-terminal-ghost uppercase">Symbol</label>
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            placeholder="AAPL"
            className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none uppercase"
          />
        </div>
        <div className="flex flex-col gap-1 w-24">
          <label className="text-[10px] font-mono text-terminal-ghost uppercase">Logic</label>
          <select
            value={logic}
            onChange={(e) => setLogic(e.target.value as 'AND' | 'OR')}
            className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
          >
            <option value="AND">AND</option>
            <option value="OR">OR</option>
          </select>
        </div>
        <div className="flex flex-col gap-1 w-28">
          <label className="text-[10px] font-mono text-terminal-ghost uppercase">Cooldown (min)</label>
          <input
            type="number"
            min={0}
            value={cooldown}
            onChange={(e) => setCooldown(Number(e.target.value))}
            className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
          />
        </div>
      </div>

      {/* Conditions */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">Conditions</span>
          <button
            type="button"
            onClick={() => setConditions([...conditions, emptyCondition()])}
            className="text-[10px] font-mono px-2 py-0.5 rounded border border-terminal-blue/30 text-terminal-blue hover:bg-terminal-blue/10 transition-colors"
          >
            + Add
          </button>
        </div>
        {conditions.map((c, i) => (
          <ConditionRow
            key={i}
            condition={c}
            onChange={(updated) => updateCondition(i, updated)}
            onRemove={() => removeCondition(i)}
          />
        ))}
      </div>

      {/* Action */}
      <div className="space-y-2">
        <span className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">Trade Action</span>
        <div className="flex flex-wrap items-end gap-2 bg-terminal-bg/50 rounded p-2">
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-terminal-ghost uppercase">Side</label>
            <select
              value={action.type}
              onChange={(e) => setAction({ ...action, type: e.target.value as OrderAction })}
              className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
            >
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-terminal-ghost uppercase">Asset</label>
            <select
              value={action.asset_type}
              onChange={(e) => setAction({ ...action, asset_type: e.target.value as AssetType })}
              className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
            >
              <option value="STK">STK</option>
              <option value="OPT">OPT</option>
              <option value="FUT">FUT</option>
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-terminal-ghost uppercase">Qty</label>
            <input
              type="number"
              min={1}
              value={action.quantity}
              onChange={(e) => setAction({ ...action, quantity: Number(e.target.value) })}
              className="w-20 text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-terminal-ghost uppercase">Order Type</label>
            <select
              value={action.order_type}
              onChange={(e) => setAction({ ...action, order_type: e.target.value as OrderType })}
              className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
            >
              <option value="MKT">MKT</option>
              <option value="LMT">LMT</option>
            </select>
          </div>
          {action.order_type === 'LMT' && (
            <div className="flex flex-col gap-1">
              <label className="text-[10px] font-mono text-terminal-ghost uppercase">Limit Price</label>
              <input
                type="number"
                step="0.01"
                value={action.limit_price ?? ''}
                onChange={(e) => setAction({ ...action, limit_price: e.target.value ? Number(e.target.value) : undefined })}
                className="w-24 text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
              />
            </div>
          )}
        </div>
      </div>

      {/* Error + Buttons */}
      {error && <p className="text-xs font-mono text-terminal-red">{error}</p>}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={busy}
          className="text-xs font-mono px-4 py-1.5 rounded bg-terminal-blue/20 border border-terminal-blue/40 text-terminal-blue hover:bg-terminal-blue/30 disabled:opacity-40 transition-colors"
        >
          {busy ? 'Saving...' : initial ? 'Update Rule' : 'Create Rule'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="text-xs font-mono px-4 py-1.5 rounded border border-terminal-border text-terminal-dim hover:text-terminal-text transition-colors"
        >
          Cancel
        </button>
      </div>
    </form>
  )
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function RulesPage() {
  const { rules, setRules, updateRule } = useBotStore()
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<Rule | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)

  const loadRules = useCallback(async () => {
    try {
      const r = await fetchRules()
      setRules(r)
    } catch {
      // backend offline — rules stay empty
    } finally {
      setLoading(false)
    }
  }, [setRules])

  useEffect(() => {
    loadRules()
  }, [loadRules])

  const handleCreate = async (data: RuleCreate) => {
    const r = await createRule(data)
    setRules([...rules, r])
    setShowForm(false)
  }

  const handleUpdate = async (data: RuleCreate) => {
    if (!editing) return
    const r = await apiUpdateRule(editing.id, data)
    updateRule(r)
    setEditing(null)
  }

  const handleToggle = async (id: string) => {
    try {
      const r = await apiToggleRule(id)
      updateRule({ ...rules.find((x) => x.id === id)!, enabled: r.enabled })
    } catch { /* ignore */ }
  }

  const handleDelete = async (id: string) => {
    try {
      await apiDeleteRule(id)
      setRules(rules.filter((r) => r.id !== id))
      setDeleteConfirm(null)
    } catch { /* ignore */ }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-terminal-ghost font-mono text-sm">
        Loading rules...
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">
          Automation Rules ({rules.length})
        </h2>
        {!showForm && !editing && (
          <button
            onClick={() => setShowForm(true)}
            className="text-xs font-mono px-3 py-1.5 rounded bg-terminal-blue/20 border border-terminal-blue/40 text-terminal-blue hover:bg-terminal-blue/30 transition-colors"
          >
            + New Rule
          </button>
        )}
      </div>

      {/* Create/Edit Form */}
      {showForm && (
        <RuleForm onSubmit={handleCreate} onCancel={() => setShowForm(false)} />
      )}
      {editing && (
        <RuleForm initial={editing} onSubmit={handleUpdate} onCancel={() => setEditing(null)} />
      )}

      {/* Rules table */}
      {rules.length === 0 && !showForm ? (
        <div className="bg-terminal-surface border border-terminal-border rounded-lg p-8 text-center">
          <p className="text-sm font-mono text-terminal-ghost mb-3">No rules configured yet</p>
          <button
            onClick={() => setShowForm(true)}
            className="text-xs font-mono px-4 py-2 rounded bg-terminal-blue/20 border border-terminal-blue/40 text-terminal-blue hover:bg-terminal-blue/30 transition-colors"
          >
            Create your first rule
          </button>
        </div>
      ) : (
        <div className="bg-terminal-surface border border-terminal-border rounded-lg overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[700px]">
              <thead>
                <tr className="border-b border-terminal-border">
                  {['Status', 'Name', 'Symbol', 'Conditions', 'Action', 'Cooldown', 'Last Triggered', ''].map((c) => (
                    <th key={c} className="py-2 px-3 text-[10px] font-mono uppercase tracking-widest text-terminal-ghost font-normal text-left">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => (
                  <tr key={rule.id} className="border-b border-terminal-border hover:bg-terminal-muted/20 transition-colors">
                    {/* Toggle */}
                    <td className="py-2 px-3">
                      <button
                        onClick={() => handleToggle(rule.id)}
                        className={clsx(
                          'w-9 h-5 rounded-full relative transition-colors',
                          rule.enabled ? 'bg-terminal-green/30' : 'bg-terminal-muted',
                        )}
                      >
                        <span
                          className={clsx(
                            'absolute top-0.5 w-4 h-4 rounded-full transition-all',
                            rule.enabled ? 'left-[18px] bg-terminal-green' : 'left-0.5 bg-terminal-dim',
                          )}
                        />
                      </button>
                    </td>
                    {/* Name */}
                    <td className="py-2 px-3 font-mono text-xs font-semibold text-terminal-text">{rule.name}</td>
                    {/* Symbol */}
                    <td className="py-2 px-3 font-mono text-xs text-terminal-blue">{rule.symbol}</td>
                    {/* Conditions summary */}
                    <td className="py-2 px-3">
                      <div className="flex flex-wrap gap-1">
                        {rule.conditions.map((c, i) => (
                          <span key={i} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-terminal-muted text-terminal-dim">
                            {c.indicator} {c.operator} {c.value}
                          </span>
                        ))}
                        {rule.conditions.length > 1 && (
                          <span className="text-[10px] font-mono px-1 text-terminal-ghost">({rule.logic})</span>
                        )}
                      </div>
                    </td>
                    {/* Action */}
                    <td className={clsx('py-2 px-3 font-mono text-xs font-semibold', rule.action.type === 'BUY' ? 'text-terminal-green' : 'text-terminal-red')}>
                      {rule.action.type} {rule.action.quantity} {rule.action.order_type}
                    </td>
                    {/* Cooldown */}
                    <td className="py-2 px-3 font-mono text-[11px] text-terminal-dim">{rule.cooldown_minutes}m</td>
                    {/* Last triggered */}
                    <td className="py-2 px-3 font-mono text-[11px] text-terminal-dim">
                      {rule.last_triggered ? new Date(rule.last_triggered).toLocaleString() : '—'}
                    </td>
                    {/* Actions */}
                    <td className="py-2 px-3">
                      <div className="flex gap-1">
                        <button
                          onClick={() => { setEditing(rule); setShowForm(false) }}
                          className="text-[10px] font-mono px-2 py-1 rounded border border-terminal-border text-terminal-dim hover:text-terminal-blue hover:border-terminal-blue/30 transition-colors"
                        >
                          Edit
                        </button>
                        {deleteConfirm === rule.id ? (
                          <div className="flex gap-1">
                            <button
                              onClick={() => handleDelete(rule.id)}
                              className="text-[10px] font-mono px-2 py-1 rounded bg-terminal-red/20 border border-terminal-red/40 text-terminal-red"
                            >
                              Confirm
                            </button>
                            <button
                              onClick={() => setDeleteConfirm(null)}
                              className="text-[10px] font-mono px-2 py-1 rounded border border-terminal-border text-terminal-dim"
                            >
                              No
                            </button>
                          </div>
                        ) : (
                          <button
                            onClick={() => setDeleteConfirm(rule.id)}
                            className="text-[10px] font-mono px-2 py-1 rounded border border-terminal-red/30 text-terminal-red hover:bg-terminal-red/10 transition-colors"
                          >
                            Del
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
