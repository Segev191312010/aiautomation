/**
 * RulesPage — Automation Rules engine.
 *
 * Two views:
 *  1. List view   — all rules, enable/disable toggle, edit, delete
 *  2. Builder view — create or edit a rule
 *
 * Design decisions:
 *  - UICondition keeps rawValue: string so inputs are predictable; coercion
 *    and validation happen once, at submit time.
 *  - Each indicator has an explicit param-key mapping and an allowed operator
 *    set; changing indicator auto-resets incompatible operator/source/period.
 *  - Multi-output indicators (MACD, BBANDS, STOCH) expose a source selector.
 *  - Toggle uses an optimistic update with rollback on API failure.
 *  - Delete button is disabled while the DELETE request is in-flight; HTTP 404
 *    is treated as success (rule was already gone).
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
import type { Rule, Condition, RuleCreate, Indicator, IndicatorSource } from '@/types'

// ─────────────────────────────────────────────────────────────────────────────
// Constants — indicator metadata
// ─────────────────────────────────────────────────────────────────────────────

const ALL_INDICATORS: Indicator[] = [
  'RSI', 'SMA', 'EMA', 'MACD', 'BBANDS', 'ATR', 'STOCH', 'PRICE',
]

const ALL_OPERATORS = [
  'crosses_above', 'crosses_below', '>', '<', '>=', '<=', '==',
] as const

/**
 * Subset of operators that are meaningful per indicator.
 * ATR is always positive and directional — crossing semantics don't apply.
 * Everything else supports the full operator set.
 */
const INDICATOR_OPERATORS: Record<Indicator, readonly string[]> = {
  RSI:    ALL_OPERATORS,
  SMA:    ALL_OPERATORS,
  EMA:    ALL_OPERATORS,
  MACD:   ALL_OPERATORS,
  BBANDS: ALL_OPERATORS,
  STOCH:  ALL_OPERATORS,
  PRICE:  ALL_OPERATORS,
  ATR:    ['>', '<', '>=', '<=', '=='],
}

/**
 * Available source series for multi-output indicators.
 * Undefined means the indicator is single-output and needs no source.
 */
const INDICATOR_SOURCES: Partial<Record<Indicator, readonly IndicatorSource[]>> = {
  MACD:   ['LINE', 'SIGNAL', 'HISTOGRAM'],
  BBANDS: ['UPPER', 'MIDDLE', 'LOWER'],
  STOCH:  ['K', 'D'],
}

const DEFAULT_SOURCE: Partial<Record<Indicator, IndicatorSource>> = {
  MACD:   'LINE',
  BBANDS: 'UPPER',
  STOCH:  'K',
}

/**
 * Backend param key for each indicator.
 * MACD exposes the fast period; slow/signal use backend defaults.
 * PRICE needs no period.
 *
 * Kept explicit here so any mismatch with the backend is immediately visible.
 */
const PARAM_KEY: Record<Indicator, string | null> = {
  RSI:    'length',
  SMA:    'length',
  EMA:    'length',
  ATR:    'length',
  STOCH:  'length',
  MACD:   'fast',    // exposes fast period only; slow=26/signal=9 are backend defaults
  BBANDS: 'length',
  PRICE:  null,      // no period parameter
}

/** Input placeholder hint per indicator, shown in the Value field. */
const VALUE_HINT: Partial<Record<Indicator, string>> = {
  RSI:    '0 – 100',
  STOCH:  '0 – 100',
  SMA:    'price',
  EMA:    'price',
  ATR:    'e.g. 1.5',
  MACD:   'e.g. 0',
  BBANDS: 'e.g. 0',
  PRICE:  'price or AAPL',
}

// ─────────────────────────────────────────────────────────────────────────────
// UICondition — local state type with string value for predictable input
// ─────────────────────────────────────────────────────────────────────────────

/**
 * While editing, we keep the value as a raw string so the input is never
 * disrupted by mid-type coercions (e.g. "30." losing the dot).
 * toCondition() coerces and validates only at submit time.
 */
interface UICondition {
  indicator: Indicator
  source?:   IndicatorSource
  params:    Record<string, number>
  operator:  string
  rawValue:  string
}

function fromCondition(c: Condition): UICondition {
  const paramKey = PARAM_KEY[c.indicator]
  const paramVal = paramKey ? Number(c.params[paramKey] ?? 14) : 0
  return {
    indicator: c.indicator,
    source:    c.source,
    params:    paramKey ? { [paramKey]: paramVal } : {},
    operator:  c.operator,
    rawValue:  String(c.value),
  }
}

function toCondition(ui: UICondition): Condition {
  const coerced: number | string =
    ui.rawValue !== '' && !isNaN(Number(ui.rawValue))
      ? Number(ui.rawValue)
      : ui.rawValue
  return {
    indicator: ui.indicator,
    source:    ui.source,
    params:    ui.params,
    operator:  ui.operator,
    value:     coerced,
  }
}

function defaultUICondition(): UICondition {
  return {
    indicator: 'RSI',
    params:    { length: 14 },
    operator:  'crosses_below',
    rawValue:  '30',
  }
}

/**
 * Per-indicator value validation run at submit time (not on each keystroke).
 * Returns an error string, or null if all conditions are valid.
 */
function validateConditions(conditions: UICondition[]): string | null {
  for (let i = 0; i < conditions.length; i++) {
    const c = conditions[i]
    const label = `Condition ${i + 1}`
    const n = Number(c.rawValue)

    // RSI and STOCH are bounded 0–100
    if (c.indicator === 'RSI' || c.indicator === 'STOCH') {
      if (c.rawValue === '' || isNaN(n) || n < 0 || n > 100)
        return `${label}: ${c.indicator} value must be a number between 0 and 100`
    }

    // Purely numeric indicators
    if (['SMA', 'EMA', 'ATR', 'MACD', 'BBANDS'].includes(c.indicator)) {
      if (c.rawValue === '' || isNaN(n))
        return `${label}: ${c.indicator} value must be numeric`
    }

    // PRICE accepts a number or a ticker string — no strict numeric check

    // Source required for multi-output indicators
    if (INDICATOR_SOURCES[c.indicator] && !c.source)
      return `${label}: ${c.indicator} requires a source (${INDICATOR_SOURCES[c.indicator]!.join(' / ')})`

    // Operator must be in the allowed set for this indicator
    if (!INDICATOR_OPERATORS[c.indicator].includes(c.operator))
      return `${label}: operator "${c.operator}" is not valid for ${c.indicator}`
  }
  return null
}

// ─────────────────────────────────────────────────────────────────────────────
// ConditionRow
// ─────────────────────────────────────────────────────────────────────────────

interface CondRowProps {
  cond:     UICondition
  idx:      number
  total:    number
  onChange: (idx: number, c: UICondition) => void
  onRemove: (idx: number) => void
}

function ConditionRow({ cond, idx, total, onChange, onRemove }: CondRowProps) {
  const sources    = INDICATOR_SOURCES[cond.indicator]
  const validOps   = INDICATOR_OPERATORS[cond.indicator]
  const paramKey   = PARAM_KEY[cond.indicator]
  const paramVal   = paramKey ? (cond.params[paramKey] ?? 14) : ''
  const showLabels = idx === 0

  const update = (patch: Partial<UICondition>) => onChange(idx, { ...cond, ...patch })

  const handleIndicatorChange = (newInd: Indicator) => {
    const newSources    = INDICATOR_SOURCES[newInd]
    const newSource     = newSources ? DEFAULT_SOURCE[newInd] : undefined
    const newValidOps   = INDICATOR_OPERATORS[newInd]
    const newOperator   = newValidOps.includes(cond.operator) ? cond.operator : newValidOps[0]
    const newParamKey   = PARAM_KEY[newInd]
    const prevParamVal  = paramKey ? (cond.params[paramKey] ?? 14) : 14
    const newParams     = newParamKey ? { [newParamKey]: prevParamVal as number } : {}
    update({ indicator: newInd, source: newSource, operator: newOperator, params: newParams })
  }

  const handlePeriodChange = (raw: string) => {
    if (!paramKey) return
    const v = parseInt(raw)
    update({ params: raw && !isNaN(v) ? { [paramKey]: v } : {} })
  }

  return (
    <div className="grid gap-2 items-end" style={{ gridTemplateColumns: '1fr 72px 1fr 1fr 32px' }}>
      {/* ── Indicator (+ optional source stacked below) ── */}
      <div className="flex flex-col gap-1">
        {showLabels && (
          <label className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">
            Indicator
          </label>
        )}
        <select
          value={cond.indicator}
          onChange={(e) => handleIndicatorChange(e.target.value as Indicator)}
          className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
        >
          {ALL_INDICATORS.map((i) => <option key={i}>{i}</option>)}
        </select>

        {/* Source — only for MACD / BBANDS / STOCH */}
        {sources && (
          <select
            value={cond.source ?? sources[0]}
            onChange={(e) => update({ source: e.target.value as IndicatorSource })}
            className="text-[11px] font-mono bg-terminal-input border border-terminal-blue/30 rounded px-2 py-1 text-terminal-blue focus:outline-none"
            title="Select series source"
          >
            {sources.map((s) => <option key={s}>{s}</option>)}
          </select>
        )}
      </div>

      {/* ── Period ── */}
      <div className="flex flex-col gap-1">
        {showLabels && (
          <label className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">
            Period
          </label>
        )}
        <input
          type="number"
          min={1}
          value={paramKey ? String(paramVal) : ''}
          onChange={(e) => handlePeriodChange(e.target.value)}
          disabled={!paramKey}
          placeholder={paramKey ? '14' : '—'}
          title={cond.indicator === 'MACD' ? 'Fast period (slow/signal use backend defaults)' : undefined}
          className={clsx(
            'text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none',
            !paramKey && 'opacity-30 cursor-not-allowed',
          )}
        />
      </div>

      {/* ── Operator (filtered per indicator) ── */}
      <div className="flex flex-col gap-1">
        {showLabels && (
          <label className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">
            Operator
          </label>
        )}
        <select
          value={cond.operator}
          onChange={(e) => update({ operator: e.target.value })}
          className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
        >
          {validOps.map((o) => <option key={o}>{o}</option>)}
        </select>
      </div>

      {/* ── Value (raw string — coerced at submit) ── */}
      <div className="flex flex-col gap-1">
        {showLabels && (
          <label className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">
            Value
          </label>
        )}
        <input
          type="text"
          inputMode="decimal"
          value={cond.rawValue}
          onChange={(e) => update({ rawValue: e.target.value })}
          placeholder={VALUE_HINT[cond.indicator] ?? '—'}
          className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
        />
      </div>

      {/* ── Remove ── */}
      <div className={showLabels ? 'mt-5' : ''}>
        <button
          type="button"
          onClick={() => onRemove(idx)}
          disabled={total <= 1}
          title={total <= 1 ? 'At least one condition is required' : 'Remove condition'}
          aria-disabled={total <= 1}
          className="w-8 h-8 flex items-center justify-center text-xl leading-none text-terminal-dim hover:text-terminal-red disabled:opacity-30 disabled:cursor-not-allowed transition-colors rounded"
        >
          ×
        </button>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// RuleBuilder
// ─────────────────────────────────────────────────────────────────────────────

interface BuilderProps {
  existing: Rule | null
  onSaved:  (rule: Rule) => void
  onCancel: () => void
}

function RuleBuilder({ existing, onSaved, onCancel }: BuilderProps) {
  const [name,        setName]        = useState(existing?.name ?? '')
  const [symbol,      setSymbol]      = useState(existing?.symbol ?? 'AAPL')
  const [logic,       setLogic]       = useState<'AND' | 'OR'>(existing?.logic ?? 'AND')
  const [conditions,  setConditions]  = useState<UICondition[]>(
    existing?.conditions.length
      ? existing.conditions.map(fromCondition)
      : [defaultUICondition()],
  )
  const [actionType,  setActionType]  = useState<'BUY' | 'SELL'>(existing?.action.type ?? 'BUY')
  const [assetType,   setAssetType]   = useState<'STK' | 'OPT' | 'FUT'>(existing?.action.asset_type ?? 'STK')
  const [quantity,    setQuantity]    = useState(existing?.action.quantity ?? 100)
  const [orderType,   setOrderType]   = useState<'MKT' | 'LMT'>(existing?.action.order_type ?? 'MKT')
  const [limitPrice,  setLimitPrice]  = useState(String(existing?.action.limit_price ?? ''))
  const [cooldown,    setCooldown]    = useState(existing?.cooldown_minutes ?? 60)
  const [busy,        setBusy]        = useState(false)
  const [error,       setError]       = useState('')

  const updateCondition = (idx: number, c: UICondition) =>
    setConditions((prev) => prev.map((x, i) => (i === idx ? c : x)))

  const removeCondition = (idx: number) =>
    setConditions((prev) => prev.filter((_, i) => i !== idx))

  const addCondition = () =>
    setConditions((prev) => [...prev, defaultUICondition()])

  // Clear stale limit price whenever switching back to MKT order type
  const handleOrderTypeChange = (t: 'MKT' | 'LMT') => {
    setOrderType(t)
    if (t === 'MKT') setLimitPrice('')
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || !symbol.trim()) return

    // ── Validate conditions before touching the API ──────────────────────
    const condError = validateConditions(conditions)
    if (condError) { setError(condError); return }

    setBusy(true)
    setError('')
    try {
      const payload: RuleCreate = {
        name:    name.trim(),
        symbol:  symbol.trim().toUpperCase(),
        enabled: existing?.enabled ?? false,
        // Coerce rawValue → number | string here, once
        conditions: conditions.map(toCondition),
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

            <div className="flex flex-col gap-1">
              <label className="text-[10px] font-mono text-terminal-ghost uppercase">
                Order Type
              </label>
              <select
                value={orderType}
                onChange={(e) => handleOrderTypeChange(e.target.value as 'MKT' | 'LMT')}
                className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
              >
                <option>MKT</option>
                <option>LMT</option>
              </select>
            </div>

            {orderType === 'LMT' && (
              <div className="flex flex-col gap-1 col-span-2 md:col-span-4">
                <label className="text-[10px] font-mono text-terminal-ghost uppercase">
                  Limit Price
                </label>
                <input
                  type="number"
                  step="0.01"
                  min="0"
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

        {error && <p className="text-xs font-mono text-terminal-red">{error}</p>}

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

// ─────────────────────────────────────────────────────────────────────────────
// RuleCard
// ─────────────────────────────────────────────────────────────────────────────

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
        <div className="mb-2">
          <span className="font-mono font-semibold text-sm text-terminal-text">{rule.name}</span>
          <div className="flex items-center flex-wrap gap-1.5 mt-1">
            <span className="text-xs font-mono font-semibold text-terminal-text">{rule.symbol}</span>
            <span className="text-terminal-ghost text-xs">·</span>
            <span className={clsx('text-xs font-mono font-semibold',
              rule.action.type === 'BUY' ? 'text-terminal-green' : 'text-terminal-red')}>
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
                {c.source ? `[${c.source}]` : ''}
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

        {rule.last_triggered && (
          <p className="text-[10px] font-mono text-terminal-ghost mb-2">
            Last triggered: {new Date(rule.last_triggered).toLocaleString()}
          </p>
        )}

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
          <span className={clsx(
            'ml-auto text-[10px] font-mono px-1.5 py-0.5 rounded',
            rule.enabled
              ? 'bg-terminal-green/10 text-terminal-green'
              : 'bg-terminal-muted text-terminal-ghost',
          )}>
            {rule.enabled ? 'ENABLED' : 'DISABLED'}
          </span>
        </div>
      </div>

      {/* Toggle */}
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
          <span className={clsx(
            'absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform duration-200',
            rule.enabled ? 'translate-x-5' : 'translate-x-0.5',
          )} />
        </button>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// DeleteConfirm
// ─────────────────────────────────────────────────────────────────────────────

interface DeleteConfirmProps {
  rule:       Rule
  inFlight:   boolean
  onConfirm:  () => void
  onCancel:   () => void
}

function DeleteConfirm({ rule, inFlight, onConfirm, onCancel }: DeleteConfirmProps) {
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
            disabled={inFlight}
            className="flex-1 text-sm font-mono py-2 rounded border border-terminal-border text-terminal-dim hover:text-terminal-text disabled:opacity-40 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={inFlight}
            className="flex-1 text-sm font-mono py-2 rounded bg-terminal-red/15 border border-terminal-red/30 text-terminal-red hover:bg-terminal-red/25 disabled:opacity-40 transition-colors"
          >
            {inFlight ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Toast
// ─────────────────────────────────────────────────────────────────────────────

function Toast({ message }: { message: string }) {
  if (!message) return null
  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 bg-terminal-elevated border border-terminal-border rounded-lg px-4 py-2.5 shadow-terminal pointer-events-none">
      <span className="text-xs font-mono text-terminal-text">{message}</span>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// RulesPage
// ─────────────────────────────────────────────────────────────────────────────

type ViewMode = 'list' | 'builder'

export default function RulesPage() {
  const { rules, setRules, addRule, updateRule, removeRule } = useBotStore()

  const [view,          setView]          = useState<ViewMode>('list')
  const [editing,       setEditing]       = useState<Rule | null>(null)
  const [deleting,      setDeleting]      = useState<Rule | null>(null)
  const [deleteInFlight, setDeleteInFlight] = useState(false)
  const [loading,       setLoading]       = useState(false)
  const [toast,         setToast]         = useState('')

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(''), 2500)
  }

  // ── Load rules on mount ───────────────────────────────────────────────────

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try { setRules(await fetchRules()) }
      catch { /* backend offline — use whatever is already in store */ }
      finally { setLoading(false) }
    }
    load()
  }, [setRules])

  // ── Navigation ────────────────────────────────────────────────────────────

  const handleNewRule = () => { setEditing(null); setView('builder') }
  const handleEdit    = (rule: Rule) => { setEditing(rule); setView('builder') }
  const handleCancel  = () => { setEditing(null); setView('list') }

  const handleSaved = (savedRule: Rule) => {
    if (editing) {
      updateRule(savedRule)       // functional set(s => ...) inside store
      showToast('Rule updated')
    } else {
      addRule(savedRule)          // functional set(s => ...) — no stale closure
      showToast('Rule created')
    }
    setEditing(null)
    setView('list')
  }

  // ── Toggle — optimistic update with rollback on failure ───────────────────

  const handleToggle = async (rule: Rule) => {
    const optimistic = { ...rule, enabled: !rule.enabled }
    updateRule(optimistic)                       // immediate UI feedback
    try {
      const res = await toggleRule(rule.id)
      updateRule({ ...rule, enabled: res.enabled })   // confirm with server value
      showToast(res.enabled ? 'Rule enabled' : 'Rule disabled')
    } catch (err: unknown) {
      updateRule(rule)                           // rollback to original state
      showToast(err instanceof Error ? err.message : 'Error toggling rule')
    }
  }

  // ── Delete — in-flight guard, 404 treated as success ─────────────────────

  const handleDeleteRequest = (rule: Rule) => setDeleting(rule)

  const handleDeleteConfirm = async () => {
    if (!deleting || deleteInFlight) return
    const target = deleting
    setDeleteInFlight(true)
    try {
      await deleteRule(target.id)
      removeRule(target.id)        // functional set(s => ...) inside store
      showToast('Rule deleted')
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : ''
      if (msg.includes('404')) {
        // Already gone on the server — mirror that in the UI
        removeRule(target.id)
        showToast('Rule deleted')
      } else {
        showToast(msg || 'Error deleting rule')
      }
    } finally {
      setDeleteInFlight(false)
      setDeleting(null)
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────

  if (view === 'builder') {
    return (
      <div className="max-w-2xl">
        <RuleBuilder existing={editing} onSaved={handleSaved} onCancel={handleCancel} />
        <Toast message={toast} />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4 pb-8">
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

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-28 bg-terminal-surface border border-terminal-border rounded-lg animate-pulse" />
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

      {deleting && (
        <DeleteConfirm
          rule={deleting}
          inFlight={deleteInFlight}
          onConfirm={handleDeleteConfirm}
          onCancel={() => { if (!deleteInFlight) setDeleting(null) }}
        />
      )}

      <Toast message={toast} />
    </div>
  )
}
