/**
 * RulesPage — CRUD UI for automated trading rules.
 *
 * A rule pairs a list of *conditions* (e.g. RSI < 30, price > SMA50) with an
 * *action* (BUY/SELL of a specific quantity). The rules engine on the backend
 * is responsible for evaluating them on each bot tick. In mock mode the rules
 * are stored locally and the bot toggle simulates evaluation.
 */
import React, { useEffect, useMemo, useState } from 'react'
import clsx from 'clsx'
import { useBotStore } from '@/store'
import {
  fetchRules,
  createRule,
  updateRule,
  deleteRule,
  toggleRule,
} from '@/services/api'
import type {
  Condition,
  Indicator,
  Rule,
  TradeAction,
} from '@/types'

// ── Constants ─────────────────────────────────────────────────────────────────

const INDICATORS: { value: Indicator; label: string; defaults: Record<string, number | string> }[] = [
  { value: 'PRICE',  label: 'Price',        defaults: {} },
  { value: 'RSI',    label: 'RSI',          defaults: { period: 14 } },
  { value: 'SMA',    label: 'SMA',          defaults: { period: 50 } },
  { value: 'EMA',    label: 'EMA',          defaults: { period: 20 } },
  { value: 'MACD',   label: 'MACD',         defaults: { fast: 12, slow: 26, signal: 9 } },
  { value: 'BBANDS', label: 'Bollinger',    defaults: { period: 20, mult: 2 } },
  { value: 'ATR',    label: 'ATR',          defaults: { period: 14 } },
  { value: 'STOCH',  label: 'Stochastic',   defaults: { k: 14, d: 3 } },
]

const OPERATORS = ['>', '>=', '<', '<=', '==', 'crosses_above', 'crosses_below'] as const

const EMPTY_CONDITION = (): Condition => ({
  indicator: 'RSI',
  params:    { period: 14 },
  operator:  '<',
  value:     30,
})

const EMPTY_RULE = (): Omit<Rule, 'id'> => ({
  name:             'New Rule',
  symbol:           'AAPL',
  enabled:          true,
  conditions:       [EMPTY_CONDITION()],
  logic:            'AND',
  action:           { type: 'BUY', asset_type: 'STK', quantity: 1, order_type: 'MKT' },
  cooldown_minutes: 30,
})

// ── Component ─────────────────────────────────────────────────────────────────

export default function RulesPage() {
  const { botRunning, mockMode } = useBotStore()
  const [rules, setRules]       = useState<Rule[]>([])
  const [loading, setLoading]   = useState(true)
  const [editing, setEditing]   = useState<Rule | (Omit<Rule, 'id'> & { id?: string }) | null>(null)
  const [error, setError]       = useState('')

  const reload = async () => {
    setLoading(true)
    try {
      setRules(await fetchRules())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load rules')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { reload() }, [])

  const handleNew = () => setEditing(EMPTY_RULE())

  const handleSave = async (draft: Omit<Rule, 'id'> & { id?: string }) => {
    setError('')
    try {
      if (draft.id) {
        await updateRule(draft.id, draft as Partial<Rule>)
      } else {
        await createRule(draft)
      }
      setEditing(null)
      reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save rule')
    }
  }

  const handleDelete = async (id: string) => {
    if (!window.confirm('Delete this rule?')) return
    await deleteRule(id)
    reload()
  }

  const handleToggle = async (id: string) => {
    await toggleRule(id)
    reload()
  }

  const enabledCount = useMemo(() => rules.filter((r) => r.enabled).length, [rules])

  return (
    <div className="flex flex-col gap-4">
      {/* ── Header ──────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-terminal-text">Automation Rules</h2>
          <p className="text-xs text-terminal-dim mt-0.5">
            {rules.length === 0
              ? 'No rules yet. Click "+ New Rule" to create your first one.'
              : `${enabledCount} of ${rules.length} rules enabled. Bot ${botRunning ? 'running' : 'stopped'}.`}
            {mockMode && (
              <span className="ml-2 text-[10px] font-mono text-terminal-amber">
                [MOCK · rules persist to localStorage]
              </span>
            )}
          </p>
        </div>
        <button
          onClick={handleNew}
          className="text-xs font-mono px-3 py-1.5 rounded bg-terminal-blue/20 border border-terminal-blue/40 text-terminal-blue hover:bg-terminal-blue/30 transition-colors"
        >
          + New Rule
        </button>
      </div>

      {error && (
        <div className="text-xs font-mono text-terminal-red bg-terminal-red/10 border border-terminal-red/30 rounded px-3 py-2">
          {error}
        </div>
      )}

      {/* ── List ────────────────────────────────────────────────── */}
      {loading ? (
        <div className="text-xs font-mono text-terminal-ghost">Loading…</div>
      ) : rules.length === 0 ? (
        <EmptyState onNew={handleNew} />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {rules.map((r) => (
            <RuleCard
              key={r.id}
              rule={r}
              onEdit={() => setEditing(r)}
              onDelete={() => handleDelete(r.id)}
              onToggle={() => handleToggle(r.id)}
            />
          ))}
        </div>
      )}

      {/* ── Modal ──────────────────────────────────────────────── */}
      {editing && (
        <RuleEditor
          draft={editing}
          onCancel={() => setEditing(null)}
          onSave={handleSave}
        />
      )}
    </div>
  )
}

// ── Empty state ───────────────────────────────────────────────────────────────

function EmptyState({ onNew }: { onNew: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-terminal-ghost">
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-12 h-12 mb-3 opacity-30">
        <path d="M3 18h6v-2H3v2zM3 6v2h18V6H3zm0 7h12v-2H3v2z" />
      </svg>
      <p className="text-sm font-mono mb-3">No automation rules defined yet.</p>
      <button
        onClick={onNew}
        className="text-xs font-mono px-4 py-1.5 rounded bg-terminal-green/20 border border-terminal-green/40 text-terminal-green hover:bg-terminal-green/30 transition-colors"
      >
        Create your first rule
      </button>
    </div>
  )
}

// ── Rule card ─────────────────────────────────────────────────────────────────

function RuleCard({
  rule,
  onEdit,
  onDelete,
  onToggle,
}: {
  rule:     Rule
  onEdit:   () => void
  onDelete: () => void
  onToggle: () => void
}) {
  return (
    <div className={clsx(
      'bg-terminal-surface border rounded-lg p-4 flex flex-col gap-3 transition-colors',
      rule.enabled ? 'border-terminal-green/30' : 'border-terminal-border',
    )}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-terminal-text">{rule.name}</h3>
          <p className="text-[11px] font-mono text-terminal-dim mt-0.5">
            <span className="text-terminal-blue">{rule.symbol}</span>
            <span className="mx-1">·</span>
            <span className={rule.action.type === 'BUY' ? 'text-terminal-green' : 'text-terminal-red'}>
              {rule.action.type} {rule.action.quantity}
            </span>
            <span className="mx-1">·</span>
            <span>{rule.action.order_type}</span>
            {rule.cooldown_minutes > 0 && (
              <>
                <span className="mx-1">·</span>
                <span>cooldown {rule.cooldown_minutes}m</span>
              </>
            )}
          </p>
        </div>
        <button
          onClick={onToggle}
          className={clsx(
            'relative w-9 h-5 rounded-full border transition-all shrink-0',
            rule.enabled
              ? 'bg-terminal-green border-terminal-green'
              : 'bg-terminal-muted border-terminal-border',
          )}
          aria-label={rule.enabled ? 'Disable' : 'Enable'}
        >
          <span className={clsx(
            'absolute top-0.5 w-3.5 h-3.5 rounded-full bg-white transition-transform',
            rule.enabled ? 'translate-x-4' : 'translate-x-0.5',
          )} />
        </button>
      </div>

      <div className="bg-terminal-bg/50 border border-terminal-border rounded p-2 text-[11px] font-mono">
        <p className="text-terminal-ghost mb-1">WHEN ({rule.logic}):</p>
        <ul className="space-y-0.5">
          {rule.conditions.map((c, i) => (
            <li key={i} className="text-terminal-dim">
              <span className="text-terminal-amber">{c.indicator}</span>
              {Object.keys(c.params).length > 0 && (
                <span className="text-terminal-ghost">({Object.values(c.params).join(',')})</span>
              )}
              <span className="text-terminal-text mx-1.5">{c.operator}</span>
              <span className="text-terminal-text">{String(c.value)}</span>
            </li>
          ))}
        </ul>
      </div>

      {rule.last_triggered && (
        <p className="text-[10px] font-mono text-terminal-ghost">
          last triggered {new Date(rule.last_triggered).toLocaleString()}
        </p>
      )}

      <div className="flex gap-2 pt-1">
        <button
          onClick={onEdit}
          className="text-[11px] font-mono px-3 py-1 rounded border border-terminal-border text-terminal-dim hover:text-terminal-text transition-colors"
        >
          Edit
        </button>
        <button
          onClick={onDelete}
          className="text-[11px] font-mono px-3 py-1 rounded border border-terminal-red/30 text-terminal-red hover:bg-terminal-red/10 transition-colors"
        >
          Delete
        </button>
      </div>
    </div>
  )
}

// ── Rule editor (modal) ───────────────────────────────────────────────────────

function RuleEditor({
  draft,
  onCancel,
  onSave,
}: {
  draft:    Omit<Rule, 'id'> & { id?: string }
  onCancel: () => void
  onSave:   (draft: Omit<Rule, 'id'> & { id?: string }) => void
}) {
  const [d, setD] = useState(draft)

  const setAction = (patch: Partial<TradeAction>) =>
    setD((s) => ({ ...s, action: { ...s.action, ...patch } }))

  const addCondition = () =>
    setD((s) => ({ ...s, conditions: [...s.conditions, EMPTY_CONDITION()] }))

  const removeCondition = (i: number) =>
    setD((s) => ({ ...s, conditions: s.conditions.filter((_, idx) => idx !== i) }))

  const updateCondition = (i: number, patch: Partial<Condition>) =>
    setD((s) => ({
      ...s,
      conditions: s.conditions.map((c, idx) => (idx === i ? { ...c, ...patch } : c)),
    }))

  const handleIndicatorChange = (i: number, ind: Indicator) => {
    const def = INDICATORS.find((x) => x.value === ind)
    updateCondition(i, { indicator: ind, params: { ...(def?.defaults ?? {}) } })
  }

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-terminal-elevated border border-terminal-border rounded-lg w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-3 border-b border-terminal-border">
          <h3 className="text-sm font-semibold text-terminal-text">
            {draft.id ? 'Edit Rule' : 'New Rule'}
          </h3>
          <button onClick={onCancel} className="text-terminal-ghost hover:text-terminal-red text-lg">✕</button>
        </div>

        <div className="p-5 flex flex-col gap-4">
          {/* Name + symbol */}
          <div className="grid grid-cols-2 gap-3">
            <Field label="Name">
              <input
                value={d.name}
                onChange={(e) => setD({ ...d, name: e.target.value })}
                className="input"
              />
            </Field>
            <Field label="Symbol">
              <input
                value={d.symbol}
                onChange={(e) => setD({ ...d, symbol: e.target.value.toUpperCase() })}
                className="input uppercase"
              />
            </Field>
          </div>

          {/* Conditions */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">Conditions</span>
              <div className="flex items-center gap-2">
                <select
                  value={d.logic}
                  onChange={(e) => setD({ ...d, logic: e.target.value as 'AND' | 'OR' })}
                  className="select"
                >
                  <option value="AND">ALL (AND)</option>
                  <option value="OR">ANY (OR)</option>
                </select>
                <button onClick={addCondition} className="text-[11px] font-mono px-2 py-0.5 rounded bg-terminal-blue/20 border border-terminal-blue/40 text-terminal-blue">
                  + Add
                </button>
              </div>
            </div>

            <div className="flex flex-col gap-2">
              {d.conditions.map((c, i) => (
                <div key={i} className="bg-terminal-bg/50 border border-terminal-border rounded p-2 flex flex-wrap items-center gap-2">
                  <select
                    value={c.indicator}
                    onChange={(e) => handleIndicatorChange(i, e.target.value as Indicator)}
                    className="select"
                  >
                    {INDICATORS.map((ind) => (
                      <option key={ind.value} value={ind.value}>{ind.label}</option>
                    ))}
                  </select>

                  {Object.entries(c.params).map(([k, v]) => (
                    <input
                      key={k}
                      type="number"
                      value={v as number}
                      onChange={(e) => updateCondition(i, { params: { ...c.params, [k]: Number(e.target.value) } })}
                      title={k}
                      className="input w-16"
                    />
                  ))}

                  <select
                    value={c.operator}
                    onChange={(e) => updateCondition(i, { operator: e.target.value })}
                    className="select"
                  >
                    {OPERATORS.map((op) => <option key={op} value={op}>{op}</option>)}
                  </select>

                  <input
                    type="number"
                    value={c.value as number}
                    onChange={(e) => updateCondition(i, { value: Number(e.target.value) })}
                    className="input w-20"
                  />

                  <button
                    onClick={() => removeCondition(i)}
                    disabled={d.conditions.length <= 1}
                    className="ml-auto text-terminal-ghost hover:text-terminal-red disabled:opacity-30"
                    title="Remove"
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* Action */}
          <div>
            <span className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">Action</span>
            <div className="grid grid-cols-4 gap-2 mt-2">
              <select
                value={d.action.type}
                onChange={(e) => setAction({ type: e.target.value as 'BUY' | 'SELL' })}
                className="select"
              >
                <option value="BUY">BUY</option>
                <option value="SELL">SELL</option>
              </select>
              <input
                type="number"
                value={d.action.quantity}
                min={1}
                onChange={(e) => setAction({ quantity: Number(e.target.value) })}
                className="input"
                placeholder="Qty"
              />
              <select
                value={d.action.order_type}
                onChange={(e) => setAction({ order_type: e.target.value as 'MKT' | 'LMT' })}
                className="select"
              >
                <option value="MKT">MKT</option>
                <option value="LMT">LMT</option>
              </select>
              {d.action.order_type === 'LMT' && (
                <input
                  type="number"
                  value={d.action.limit_price ?? 0}
                  step="0.01"
                  onChange={(e) => setAction({ limit_price: Number(e.target.value) })}
                  className="input"
                  placeholder="Limit"
                />
              )}
            </div>
          </div>

          {/* Cooldown */}
          <Field label="Cooldown (minutes between fires)">
            <input
              type="number"
              min={0}
              value={d.cooldown_minutes}
              onChange={(e) => setD({ ...d, cooldown_minutes: Number(e.target.value) })}
              className="input w-24"
            />
          </Field>
        </div>

        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-terminal-border">
          <button onClick={onCancel} className="text-xs font-mono px-3 py-1.5 rounded border border-terminal-border text-terminal-dim hover:text-terminal-text">
            Cancel
          </button>
          <button
            onClick={() => onSave(d)}
            className="text-xs font-mono px-4 py-1.5 rounded bg-terminal-green/20 border border-terminal-green/40 text-terminal-green hover:bg-terminal-green/30"
          >
            Save Rule
          </button>
        </div>

        <style>{`
          .input { background:#0a1525; border:1px solid #1c2e4a; border-radius:4px; padding:6px 8px;
                   font-family:"JetBrains Mono",monospace; font-size:11px; color:#dce8f5; outline:none; }
          .input:focus { border-color:#4f91ff; }
          .select { background:#0a1525; border:1px solid #1c2e4a; border-radius:4px; padding:5px 8px;
                    font-family:"JetBrains Mono",monospace; font-size:11px; color:#dce8f5; outline:none; }
          .select:focus { border-color:#4f91ff; }
        `}</style>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">{label}</span>
      {children}
    </label>
  )
}
