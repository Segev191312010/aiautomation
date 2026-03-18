/**
 * RulesPage — Stage 6 Rule Builder.
 *
 * Layout: left panel (rule list) + right panel (rule editor).
 * Rules are persisted via REST API and cached in useBotStore.
 */
import { useCallback, useEffect, useState } from 'react'
import { useBotStore } from '@/store'
import * as api from '@/services/api'
import type { Condition, Indicator, OrderAction, OrderType, AssetType, Rule, RuleCreate, RuleUniverse, TradeAction } from '@/types'
import { INDICATORS, OPERATORS, INDICATOR_PARAMS, defaultCondition, defaultParams } from '@/utils/conditionHelpers'

// ── SVG icons ────────────────────────────────────────────────────────────────

function IconRules({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2" />
      <rect x="9" y="3" width="6" height="4" rx="1" />
      <path d="M9 12h6M9 16h4" />
    </svg>
  )
}

function IconPlus({ className = 'w-3.5 h-3.5' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
      <path d="M12 4v16m8-8H4" />
    </svg>
  )
}

function IconTrash({ className = 'w-3.5 h-3.5' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6" />
      <path d="M10 11v6M14 11v6" />
      <path d="M9 6V4a1 1 0 011-1h4a1 1 0 011 1v2" />
    </svg>
  )
}

function IconEdit({ className = 'w-3.5 h-3.5' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7" />
      <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  )
}

function IconSave({ className = 'w-3.5 h-3.5' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z" />
      <polyline points="17 21 17 13 7 13 7 21" />
      <polyline points="7 3 7 8 15 8" />
    </svg>
  )
}

function IconWarning({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  )
}

// ── Toggle switch ─────────────────────────────────────────────────────────────

interface ToggleProps {
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
  size?: 'sm' | 'md'
}

function Toggle({ checked, onChange, disabled = false, size = 'md' }: ToggleProps) {
  const trackW = size === 'sm' ? 'w-8' : 'w-9'
  const trackH = size === 'sm' ? 'h-4' : 'h-5'
  const thumbW = size === 'sm' ? 'w-3 h-3' : 'w-3.5 h-3.5'
  const translateX = size === 'sm' ? 'translate-x-4' : 'translate-x-4'

  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => !disabled && onChange(!checked)}
      className={[
        `relative inline-flex items-center flex-shrink-0 ${trackW} ${trackH} rounded-full transition-colors duration-200`,
        checked ? 'bg-indigo-500' : 'bg-zinc-800',
        disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer',
      ].join(' ')}
    >
      <span
        className={[
          `absolute left-0.5 ${thumbW} bg-zinc-900 rounded-full shadow transition-transform duration-200`,
          checked ? translateX : 'translate-x-0',
        ].join(' ')}
      />
    </button>
  )
}

// ── Condition row (reused from StrategyBuilder pattern) ───────────────────────

interface ConditionRowProps {
  cond: Condition
  onChange: (c: Condition) => void
  onRemove: () => void
  accent: 'green' | 'red'
}

export function ConditionRow({ cond, onChange, onRemove, accent }: ConditionRowProps) {
  const paramDefs = INDICATOR_PARAMS[cond.indicator] || []
  const borderColor = accent === 'green' ? 'border-l-green-600' : 'border-l-red-600'

  return (
    <div className={`flex items-center gap-2 bg-zinc-900 rounded-xl border border-zinc-800 border-l-2 ${borderColor} px-3 py-2.5 transition-colors hover:bg-zinc-800/60 flex-wrap`}>
      {/* Indicator */}
      <select
        className="bg-zinc-900 border border-zinc-800 rounded-lg px-2 py-1 text-xs font-mono text-zinc-100 w-24 focus:outline-none focus:border-indigo-600/50 focus:ring-1 focus:ring-indigo-600/20 transition-all cursor-pointer appearance-none"
        value={cond.indicator}
        onChange={(e) => {
          const ind = e.target.value as Indicator
          onChange({ ...cond, indicator: ind, params: defaultParams(ind) })
        }}
      >
        {INDICATORS.map((i) => <option key={i} value={i}>{i}</option>)}
      </select>

      {/* Params */}
      {paramDefs.map((p) => (
        <div key={p.key} className="flex items-center gap-1">
          <span className="text-[10px] font-sans text-zinc-500 tracking-wide">{p.label}</span>
          {p.key === 'band' ? (
            <select
              className="bg-zinc-900 border border-zinc-800 rounded-lg px-1 py-1 text-xs font-mono text-zinc-100 w-16 focus:outline-none focus:border-indigo-600/50 focus:ring-1 focus:ring-indigo-600/20 transition-all cursor-pointer appearance-none"
              value={String(cond.params[p.key] ?? 'mid')}
              onChange={(e) => onChange({ ...cond, params: { ...cond.params, [p.key]: e.target.value } })}
            >
              <option value="upper">Upper</option>
              <option value="mid">Mid</option>
              <option value="lower">Lower</option>
            </select>
          ) : (
            <input
              type="number"
              className="bg-zinc-900 border border-zinc-800 rounded-lg px-1 py-1 text-xs font-mono text-zinc-100 w-14 focus:outline-none focus:border-indigo-600/50 focus:ring-1 focus:ring-indigo-600/20 transition-all"
              value={cond.params[p.key] ?? p.def}
              onChange={(e) => onChange({ ...cond, params: { ...cond.params, [p.key]: Number(e.target.value) } })}
            />
          )}
        </div>
      ))}

      {/* Operator */}
      <select
        className="bg-zinc-900 border border-zinc-800 rounded-lg px-2 py-1 text-xs font-mono text-zinc-100 w-32 focus:outline-none focus:border-indigo-600/50 focus:ring-1 focus:ring-indigo-600/20 transition-all cursor-pointer appearance-none"
        value={cond.operator}
        onChange={(e) => onChange({ ...cond, operator: e.target.value })}
      >
        {OPERATORS.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>

      {/* Value */}
      <input
        className="bg-zinc-900 border border-zinc-800 rounded-lg px-2 py-1 text-xs font-mono text-zinc-100 w-20 focus:outline-none focus:border-indigo-600/50 focus:ring-1 focus:ring-indigo-600/20 transition-all"
        value={cond.value}
        onChange={(e) => {
          const v = e.target.value
          const num = Number(v)
          onChange({ ...cond, value: isNaN(num) || v === '' ? v : num })
        }}
      />

      <button
        onClick={onRemove}
        className="ml-auto flex-shrink-0 w-5 h-5 flex items-center justify-center rounded-md text-zinc-500 hover:text-red-400 hover:bg-red-600/10 transition-all text-sm leading-none"
        title="Remove condition"
      >
        &times;
      </button>
    </div>
  )
}

// ── Condition section (entry only; rules have a single condition block) ───────

interface ConditionSectionProps {
  label: string
  accent: 'green' | 'red'
  conditions: Condition[]
  onChange: (c: Condition[]) => void
}

export function ConditionSection({ label, accent, conditions, onChange }: ConditionSectionProps) {
  const updateAt = (idx: number, c: Condition) => {
    const next = [...conditions]; next[idx] = c; onChange(next)
  }
  const removeAt = (idx: number) => onChange(conditions.filter((_, i) => i !== idx))
  const add = () => { if (conditions.length < 10) onChange([...conditions, defaultCondition()]) }

  const headerColor = accent === 'green' ? 'text-emerald-400' : 'text-red-400'
  const dotColor    = accent === 'green' ? 'bg-emerald-600' : 'bg-red-600'
  const addColor    = accent === 'green'
    ? 'text-emerald-400/70 hover:text-emerald-400 hover:bg-emerald-600/10'
    : 'text-red-400/70 hover:text-red-400 hover:bg-red-600/10'

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className={`w-1.5 h-1.5 rounded-full ${dotColor} flex-shrink-0`} />
        <span className={`text-[11px] font-sans font-semibold uppercase tracking-widest ${headerColor}`}>
          {label}
        </span>
        <span className="text-[10px] font-mono text-zinc-500 ml-1">({conditions.length}/10)</span>
      </div>

      <div className="space-y-1.5">
        {conditions.length === 0 && (
          <div className="text-xs font-sans text-zinc-500 italic px-3 py-2 bg-zinc-900/30 rounded-lg border border-dashed border-zinc-800">
            No conditions — rule will always trigger
          </div>
        )}
        {conditions.map((c, i) => (
          <ConditionRow
            key={i}
            cond={c}
            accent={accent}
            onChange={(v) => updateAt(i, v)}
            onRemove={() => removeAt(i)}
          />
        ))}
      </div>

      <button
        onClick={add}
        disabled={conditions.length >= 10}
        className={`flex items-center gap-1.5 text-xs font-sans px-2.5 py-1.5 rounded-lg transition-all disabled:opacity-30 disabled:cursor-not-allowed ${addColor}`}
      >
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 4v16m8-8H4" />
        </svg>
        Add Condition
      </button>
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins  = Math.floor(diff / 60_000)
  if (mins < 1)   return 'just now'
  if (mins < 60)  return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24)   return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function blankAction(): TradeAction {
  return { type: 'BUY', asset_type: 'STK', quantity: 100, order_type: 'MKT' }
}

// ── Universe helpers ──────────────────────────────────────────────────────────

const UNIVERSE_OPTIONS: { value: RuleUniverse; label: string; count: number }[] = [
  { value: 'sp500',     label: 'S&P 500',     count: 488  },
  { value: 'nasdaq100', label: 'NASDAQ 100',   count: 100  },
  { value: 'etfs',      label: 'ETFs',         count: 50   },
  { value: 'all',       label: 'All Markets',  count: 600  },
]

function universeLabel(u: RuleUniverse): string {
  return UNIVERSE_OPTIONS.find((o) => o.value === u)?.label ?? u
}

function universeCount(u: RuleUniverse): number {
  return UNIVERSE_OPTIONS.find((o) => o.value === u)?.count ?? 0
}

// ── Rule list item ─────────────────────────────────────────────────────────────

interface RuleItemProps {
  rule: Rule
  selected: boolean
  onSelect: () => void
  onToggle: () => void
  onDelete: () => void
}

export function RuleListItem({ rule, selected, onSelect, onToggle, onDelete }: RuleItemProps) {
  const actionColor = rule.action.type === 'BUY' ? 'text-emerald-400 bg-emerald-600/10' : 'text-red-400 bg-red-600/10'
  const isUniverse  = !!rule.universe

  return (
    <div
      onClick={onSelect}
      className={[
        'group relative rounded-xl border px-3 py-2.5 cursor-pointer transition-all select-none',
        selected
          ? 'bg-indigo-50 border-indigo-200 shadow-sm'
          : 'bg-zinc-900 border-zinc-800 hover:border-zinc-800 hover:bg-zinc-900/60',
      ].join(' ')}
    >
      <div className="flex items-start gap-2.5">
        {/* Enabled toggle */}
        <div className="flex-shrink-0 pt-0.5" onClick={(e) => { e.stopPropagation(); onToggle() }}>
          <Toggle checked={rule.enabled} onChange={onToggle} size="sm" />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-xs font-sans font-semibold text-zinc-100 truncate">{rule.name}</span>
            {isUniverse ? (
              <span className="text-[10px] font-sans font-medium text-violet-600 bg-violet-50 border border-violet-200/60 rounded px-1.5 py-0.5 tracking-wide">
                {universeLabel(rule.universe!)}
              </span>
            ) : (
              <span className="text-[10px] font-mono text-blue-600 bg-blue-50 border border-blue-200/60 rounded px-1.5 py-0.5 uppercase tracking-wider">
                {rule.symbol}
              </span>
            )}
            <span className={`text-[10px] font-sans font-semibold rounded px-1.5 py-0.5 uppercase ${actionColor}`}>
              {rule.action.type}
            </span>
          </div>
          <div className="flex items-center gap-2 mt-1">
            <span className="text-[10px] font-sans text-zinc-500">
              {rule.conditions.length} condition{rule.conditions.length !== 1 ? 's' : ''} &bull; {rule.logic}
            </span>
            {rule.last_triggered && (
              <span className="text-[10px] font-mono text-zinc-500">
                triggered {formatRelativeTime(rule.last_triggered)}
              </span>
            )}
          </div>
        </div>

        {/* Delete */}
        <button
          onClick={(e) => { e.stopPropagation(); onDelete() }}
          className="flex-shrink-0 opacity-0 group-hover:opacity-100 w-6 h-6 flex items-center justify-center rounded-md text-zinc-500 hover:text-red-400 hover:bg-red-600/10 transition-all"
          title="Delete rule"
        >
          <IconTrash className="w-3 h-3" />
        </button>
      </div>
    </div>
  )
}

// ── Rule editor ───────────────────────────────────────────────────────────────

interface RuleEditorProps {
  initial: Rule | null         // null = create new
  onSaved: (rule: Rule) => void
  onCancel: () => void
}

export function RuleEditor({ initial, onSaved, onCancel }: RuleEditorProps) {
  // Derive the initial target mode from the rule being edited
  const initMode: 'symbol' | 'universe' = initial?.universe ? 'universe' : 'symbol'

  const [name, setName]                 = useState(initial?.name ?? '')
  const [targetMode, setTargetMode]     = useState<'symbol' | 'universe'>(initMode)
  const [symbol, setSymbol]             = useState(initial?.symbol ?? '')
  const [universe, setUniverse]         = useState<RuleUniverse>(
    (initial?.universe as RuleUniverse | undefined) ?? 'sp500',
  )
  const [conditions, setConditions]     = useState<Condition[]>(
    initial?.conditions.length ? initial.conditions : [defaultCondition()],
  )
  const [logic, setLogic]               = useState<'AND' | 'OR'>(initial?.logic ?? 'AND')
  const [actionType, setActionType]     = useState<OrderAction>(initial?.action.type ?? 'BUY')
  const [assetType, setAssetType]       = useState<AssetType>(initial?.action.asset_type ?? 'STK')
  const [quantity, setQuantity]         = useState(initial?.action.quantity ?? 100)
  const [orderType, setOrderType]       = useState<OrderType>(initial?.action.order_type ?? 'MKT')
  const [limitPrice, setLimitPrice]     = useState<number | ''>(initial?.action.limit_price ?? '')
  const [cooldown, setCooldown]         = useState(initial?.cooldown_minutes ?? 5)
  const [saving, setSaving]             = useState(false)
  const [error, setError]               = useState<string | null>(null)

  const isEdit = initial !== null

  async function handleSave() {
    const trimmedName = name.trim()
    if (!trimmedName) { setError('Name is required.'); return }

    const trimmedSymbol = targetMode === 'symbol' ? symbol.trim().toUpperCase() : ''
    if (targetMode === 'symbol' && !trimmedSymbol) {
      setError('Symbol is required.'); return
    }

    const action: TradeAction = {
      type:        actionType,
      asset_type:  assetType,
      quantity,
      order_type:  orderType,
      ...(orderType === 'LMT' && limitPrice !== '' ? { limit_price: limitPrice } : {}),
    }

    const payload: RuleCreate = {
      name:             trimmedName,
      symbol:           trimmedSymbol,
      universe:         targetMode === 'universe' ? universe : null,
      enabled:          initial?.enabled ?? true,
      conditions,
      logic,
      action,
      cooldown_minutes: cooldown,
    }

    setSaving(true)
    setError(null)
    try {
      const saved = isEdit
        ? await api.updateRule(initial.id, payload)
        : await api.createRule(payload)
      onSaved(saved)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const inputCls = 'bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-1.5 text-sm font-mono text-zinc-100 focus:outline-none focus:border-indigo-300 focus:ring-1 focus:ring-indigo-200 transition-all placeholder:text-zinc-500'
  const selectCls = 'bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-1.5 text-sm font-mono text-zinc-100 focus:outline-none focus:border-indigo-300 focus:ring-1 focus:ring-indigo-200 transition-all cursor-pointer appearance-none'
  const labelCls = 'block text-xs font-sans font-medium text-zinc-400 mb-1 tracking-wide'

  const selectedUniverseInfo = UNIVERSE_OPTIONS.find((o) => o.value === universe)

  return (
    <div className="flex flex-col gap-5">

      {/* Header */}
      <div className="flex items-center gap-2.5 pb-4 border-b border-zinc-800">
        <span className="flex items-center justify-center w-7 h-7 rounded-lg bg-indigo-50 text-indigo-600 flex-shrink-0">
          {isEdit ? <IconEdit className="w-3.5 h-3.5" /> : <IconPlus className="w-3.5 h-3.5" />}
        </span>
        <h2 className="text-sm font-sans font-semibold text-zinc-100">
          {isEdit ? 'Edit Rule' : 'New Rule'}
        </h2>
        <button
          onClick={onCancel}
          className="ml-auto text-xs font-sans text-zinc-500 hover:text-zinc-400 px-2.5 py-1.5 rounded-lg hover:bg-zinc-800/60 transition-all"
        >
          Cancel
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl bg-red-600/[0.07] border border-red-300/25">
          <IconWarning className="w-4 h-4 text-red-400 flex-shrink-0 mt-0.5" />
          <p className="text-xs font-sans text-red-400">{error}</p>
        </div>
      )}

      {/* Name */}
      <div>
        <label className={labelCls}>Rule Name</label>
        <input
          className={`${inputCls} w-full`}
          placeholder="e.g. RSI Oversold BUY"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </div>

      {/* Target: Single Symbol vs Universe */}
      <div>
        <label className={labelCls}>Applies To</label>
        {/* Mode toggle */}
        <div className="flex bg-zinc-800 rounded-xl p-0.5 w-fit mb-3">
          {(['symbol', 'universe'] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => setTargetMode(mode)}
              className={[
                'px-3.5 py-1.5 rounded-[10px] text-xs font-sans font-semibold transition-all',
                targetMode === mode
                  ? 'bg-zinc-900 text-zinc-100 shadow-sm'
                  : 'text-zinc-500 hover:text-zinc-400',
              ].join(' ')}
            >
              {mode === 'symbol' ? 'Single Symbol' : 'Universe'}
            </button>
          ))}
        </div>

        {targetMode === 'symbol' ? (
          <input
            className={`${inputCls} w-full uppercase`}
            placeholder="AAPL"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          />
        ) : (
          <div className="space-y-2">
            <select
              className={`${selectCls} w-full`}
              value={universe}
              onChange={(e) => setUniverse(e.target.value as RuleUniverse)}
            >
              {UNIVERSE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label} ({o.count.toLocaleString()}+ symbols)
                </option>
              ))}
            </select>
            {selectedUniverseInfo && (
              <p className="text-[11px] font-sans text-violet-600 bg-violet-50 border border-violet-200/60 rounded-lg px-3 py-1.5">
                Applies to {selectedUniverseInfo.count.toLocaleString()}+ stocks &mdash; rule fires per matching symbol
              </p>
            )}
          </div>
        )}
      </div>

      {/* Conditions card */}
      <div className="card rounded-2xl  p-4 space-y-4">
        {/* Logic toggle */}
        <div className="flex items-center justify-between">
          <span className="text-xs font-sans font-semibold text-zinc-200 uppercase tracking-widest">Entry Conditions</span>
          <div className="flex bg-zinc-900 rounded-lg overflow-hidden text-[11px] border border-zinc-800">
            {(['AND', 'OR'] as const).map((l) => (
              <button
                key={l}
                onClick={() => setLogic(l)}
                className={`px-3 py-1.5 font-sans font-semibold tracking-wide transition-all ${
                  logic === l
                    ? 'bg-indigo-500 text-white'
                    : 'text-zinc-500 hover:text-zinc-400'
                }`}
              >
                {l}
              </button>
            ))}
          </div>
        </div>

        <ConditionSection
          label="Trigger When"
          accent="green"
          conditions={conditions}
          onChange={setConditions}
        />
      </div>

      {/* Trade action card */}
      <div className="card rounded-2xl  p-4 space-y-4">
        <span className="text-xs font-sans font-semibold text-zinc-200 uppercase tracking-widest">Trade Action</span>

        <div className="grid grid-cols-2 gap-4">
          {/* Action type */}
          <div>
            <label className={labelCls}>Direction</label>
            <div className="flex bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden text-xs">
              {(['BUY', 'SELL'] as OrderAction[]).map((a) => (
                <button
                  key={a}
                  onClick={() => setActionType(a)}
                  className={`flex-1 py-1.5 font-sans font-semibold transition-all ${
                    actionType === a
                      ? a === 'BUY'
                        ? 'bg-emerald-500 text-white'
                        : 'bg-red-500 text-white'
                      : 'text-zinc-500 hover:text-zinc-400 hover:bg-zinc-900'
                  }`}
                >
                  {a}
                </button>
              ))}
            </div>
          </div>

          {/* Asset type */}
          <div>
            <label className={labelCls}>Asset Type</label>
            <select
              className={`${selectCls} w-full`}
              value={assetType}
              onChange={(e) => setAssetType(e.target.value as AssetType)}
            >
              <option value="STK">STK — Stock</option>
              <option value="OPT">OPT — Option</option>
              <option value="FUT">FUT — Future</option>
            </select>
          </div>

          {/* Quantity */}
          <div>
            <label className={labelCls}>Quantity</label>
            <input
              type="number"
              min={1}
              className={`${inputCls} w-full`}
              value={quantity}
              onChange={(e) => setQuantity(Math.max(1, Number(e.target.value)))}
            />
          </div>

          {/* Order type */}
          <div>
            <label className={labelCls}>Order Type</label>
            <div className="flex bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden text-xs">
              {(['MKT', 'LMT'] as OrderType[]).map((o) => (
                <button
                  key={o}
                  onClick={() => setOrderType(o)}
                  className={`flex-1 py-1.5 font-sans font-semibold transition-all ${
                    orderType === o
                      ? 'bg-indigo-500 text-white'
                      : 'text-zinc-500 hover:text-zinc-400 hover:bg-zinc-900'
                  }`}
                >
                  {o}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Limit price (conditional) */}
        {orderType === 'LMT' && (
          <div>
            <label className={labelCls}>Limit Price</label>
            <input
              type="number"
              min={0}
              step="0.01"
              className={`${inputCls} w-40`}
              placeholder="0.00"
              value={limitPrice}
              onChange={(e) => setLimitPrice(e.target.value === '' ? '' : Number(e.target.value))}
            />
          </div>
        )}
      </div>

      {/* Cooldown slider */}
      <div className="card rounded-2xl  p-4 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-sans font-semibold text-zinc-200 uppercase tracking-widest">Cooldown</span>
          <span className="text-xs font-mono text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded-md">{cooldown} min</span>
        </div>
        <input
          type="range"
          min={0}
          max={1440}
          step={1}
          value={cooldown}
          onChange={(e) => setCooldown(Number(e.target.value))}
          className="w-full h-1.5 rounded-full accent-indigo-500 cursor-pointer"
        />
        <div className="flex justify-between text-[10px] font-mono text-zinc-500">
          <span>0 min</span>
          <span>6 h</span>
          <span>12 h</span>
          <span>24 h</span>
        </div>
      </div>

      {/* Save button */}
      <button
        onClick={handleSave}
        disabled={saving}
        className={[
          'w-full flex items-center justify-center gap-2 px-6 py-2.5 rounded-xl text-sm font-sans font-semibold',
          'transition-all duration-200 select-none',
          saving
            ? 'bg-indigo-400 text-white cursor-wait'
            : 'bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-400 hover:to-purple-400 text-white shadow-glow-blue',
        ].join(' ')}
      >
        {saving ? (
          <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
        ) : (
          <IconSave />
        )}
        {saving ? 'Saving...' : isEdit ? 'Update Rule' : 'Create Rule'}
      </button>
    </div>
  )
}

// ── Empty state ────────────────────────────────────────────────────────────────

function EmptyEditor() {
  return (
    <div className="flex-1 flex items-center justify-center">
      <div className="flex flex-col items-center gap-4 p-10 rounded-2xl border border-zinc-800 max-w-xs text-center">
        <div className="flex items-center justify-center w-14 h-14 rounded-2xl bg-zinc-900 border border-zinc-800">
          <IconRules className="w-7 h-7 text-zinc-500" />
        </div>
        <div className="space-y-1.5">
          <p className="text-sm font-sans font-medium text-zinc-400">No Rule Selected</p>
          <p className="text-xs font-sans text-zinc-500 leading-relaxed">
            Select a rule from the list to edit it, or click "New Rule" to build one from scratch.
          </p>
        </div>
      </div>
    </div>
  )
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function RuleListSkeleton() {
  return (
    <div className="space-y-2 animate-pulse">
      {[1, 2, 3].map((i) => (
        <div key={i} className="h-14 bg-zinc-900 rounded-xl" />
      ))}
    </div>
  )
}

// ── Status pill ────────────────────────────────────────────────────────────────

function StatusPill({ count, label, color }: { count: number; label: string; color: 'green' | 'ghost' }) {
  const colorMap = {
    green: 'bg-emerald-600/10 text-emerald-400 border-emerald-300/20',
    ghost: 'bg-zinc-800/60 text-zinc-400 border-zinc-800',
  }
  return (
    <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-sans font-medium ${colorMap[color]}`}>
      <span className="tabular-nums font-semibold">{count}</span>
      <span>{label}</span>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function RulesPage() {
  const rules       = useBotStore((s) => s.rules)
  const setRules    = useBotStore((s) => s.setRules)
  const updateRuleInStore = useBotStore((s) => s.updateRule)

  const [loading, setLoading]         = useState(true)
  const [selectedId, setSelectedId]   = useState<string | null>(null)
  const [creatingNew, setCreatingNew] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  // Load on mount
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.fetchRules()
      .then((r) => { if (!cancelled) { setRules(r); setLoading(false) } })
      .catch(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [setRules])

  const selectedRule = rules.find((r) => r.id === selectedId) ?? null

  const handleSelect = useCallback((id: string) => {
    setSelectedId(id)
    setCreatingNew(false)
    setDeleteError(null)
  }, [])

  const handleNewRule = () => {
    setSelectedId(null)
    setCreatingNew(true)
    setDeleteError(null)
  }

  const handleToggle = useCallback(async (rule: Rule) => {
    try {
      const updated = await api.toggleRule(rule.id)
      updateRuleInStore({ ...rule, enabled: updated.enabled })
    } catch { /* silent */ }
  }, [updateRuleInStore])

  const handleDelete = useCallback(async (id: string) => {
    setDeleteError(null)
    try {
      await api.deleteRule(id)
      setRules(rules.filter((r) => r.id !== id))
      if (selectedId === id) { setSelectedId(null); setCreatingNew(false) }
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : 'Delete failed')
    }
  }, [rules, selectedId, setRules])

  const handleSaved = useCallback((saved: Rule) => {
    if (rules.find((r) => r.id === saved.id)) {
      // update existing
      setRules(rules.map((r) => r.id === saved.id ? saved : r))
    } else {
      // new rule
      setRules([...rules, saved])
    }
    setSelectedId(saved.id)
    setCreatingNew(false)
  }, [rules, setRules])

  const handleCancelEditor = () => {
    setCreatingNew(false)
    if (!selectedId && rules.length > 0) setSelectedId(rules[0].id)
  }

  const enabledCount  = rules.filter((r) => r.enabled).length
  const disabledCount = rules.filter((r) => !r.enabled).length
  const showEditor    = creatingNew || selectedRule !== null

  return (
    <div className="flex gap-5 h-full min-h-0 p-5">

      {/* ── Left panel — Rule list ────────────────────────────────────────── */}
      <div className="w-[320px] flex-shrink-0 flex flex-col gap-4">

        {/* Panel header */}
        <div className="card rounded-2xl  p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <span className="flex items-center justify-center w-7 h-7 rounded-lg bg-indigo-50 text-indigo-600 flex-shrink-0">
                <IconRules className="w-3.5 h-3.5" />
              </span>
              <div>
                <h1 className="text-sm font-sans font-semibold text-zinc-100 leading-tight">Rules Engine</h1>
                <p className="text-[10px] font-sans text-zinc-500">Automated trading conditions</p>
              </div>
            </div>

            <button
              onClick={handleNewRule}
              className={[
                'flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-sans font-semibold',
                'bg-indigo-100 text-indigo-600 border border-indigo-100',
                'hover:bg-indigo-50 hover:border-indigo-600/50 hover:shadow-glow-blue',
                'transition-all duration-150',
              ].join(' ')}
            >
              <IconPlus className="w-3 h-3" />
              New
            </button>
          </div>

          {/* Stats */}
          {!loading && (
            <div className="flex items-center gap-2 flex-wrap">
              <StatusPill count={enabledCount} label={enabledCount === 1 ? 'active' : 'active'} color="green" />
              {disabledCount > 0 && (
                <StatusPill count={disabledCount} label="disabled" color="ghost" />
              )}
            </div>
          )}
        </div>

        {/* Delete error */}
        {deleteError && (
          <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl bg-red-600/[0.07] border border-red-300/25">
            <IconWarning className="w-4 h-4 text-red-400 flex-shrink-0 mt-0.5" />
            <p className="text-xs font-sans text-red-400">{deleteError}</p>
          </div>
        )}

        {/* List */}
        <div className="flex-1 overflow-y-auto space-y-2 pr-0.5">
          {loading ? (
            <RuleListSkeleton />
          ) : rules.length === 0 ? (
            <div className="text-xs font-sans text-zinc-500 italic text-center py-8">
              No rules yet. Click "New" to create one.
            </div>
          ) : (
            rules.map((rule) => (
              <RuleListItem
                key={rule.id}
                rule={rule}
                selected={rule.id === selectedId}
                onSelect={() => handleSelect(rule.id)}
                onToggle={() => void handleToggle(rule)}
                onDelete={() => void handleDelete(rule.id)}
              />
            ))
          )}
        </div>
      </div>

      {/* ── Right panel — Editor ─────────────────────────────────────────── */}
      <div className="flex-1 min-w-0 overflow-y-auto">
        {showEditor ? (
          <div className="card rounded-2xl  p-5">
            <RuleEditor
              key={creatingNew ? '__new__' : selectedRule?.id}
              initial={creatingNew ? null : selectedRule}
              onSaved={handleSaved}
              onCancel={handleCancelEditor}
            />
          </div>
        ) : (
          <EmptyEditor />
        )}
      </div>
    </div>
  )
}
