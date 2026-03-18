import { useBacktestStore } from '@/store'
import type { Condition, Indicator } from '@/types'
import { INDICATORS, OPERATORS, INDICATOR_PARAMS, defaultCondition, defaultParams } from '@/utils/conditionHelpers'

interface ConditionRowProps {
  cond: Condition
  onChange: (c: Condition) => void
  onRemove: () => void
  accent: 'green' | 'red'
}

function ConditionRow({ cond, onChange, onRemove, accent }: ConditionRowProps) {
  const paramDefs = INDICATOR_PARAMS[cond.indicator] || []

  const borderColor = accent === 'green' ? 'border-l-green-600' : 'border-l-red-600'

  return (
    <div className={`flex items-center gap-2 bg-gray-50 rounded-xl border border-gray-200 border-l-2 ${borderColor} px-3 py-2.5 transition-colors hover:bg-gray-100/60`}>
      {/* Indicator select */}
      <select
        className="bg-white border border-gray-200 rounded-lg px-2 py-1 text-xs font-mono text-gray-800 w-24 focus:outline-none focus:border-indigo-600/50 focus:ring-1 focus:ring-indigo-600/20 transition-all cursor-pointer appearance-none"
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
          <span className="text-[10px] font-sans text-gray-400 tracking-wide">{p.label}</span>
          {p.key === 'band' ? (
            <select
              className="bg-white border border-gray-200 rounded-lg px-1 py-1 text-xs font-mono text-gray-800 w-16 focus:outline-none focus:border-indigo-600/50 focus:ring-1 focus:ring-indigo-600/20 transition-all cursor-pointer appearance-none"
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
              className="bg-white border border-gray-200 rounded-lg px-1 py-1 text-xs font-mono text-gray-800 w-14 focus:outline-none focus:border-indigo-600/50 focus:ring-1 focus:ring-indigo-600/20 transition-all"
              value={cond.params[p.key] ?? p.def}
              onChange={(e) => onChange({ ...cond, params: { ...cond.params, [p.key]: Number(e.target.value) } })}
            />
          )}
        </div>
      ))}

      {/* Operator */}
      <select
        className="bg-white border border-gray-200 rounded-lg px-2 py-1 text-xs font-mono text-gray-800 w-32 focus:outline-none focus:border-indigo-600/50 focus:ring-1 focus:ring-indigo-600/20 transition-all cursor-pointer appearance-none"
        value={cond.operator}
        onChange={(e) => onChange({ ...cond, operator: e.target.value })}
      >
        {OPERATORS.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>

      {/* Value */}
      <input
        className="bg-white border border-gray-200 rounded-lg px-2 py-1 text-xs font-mono text-gray-800 w-20 focus:outline-none focus:border-indigo-600/50 focus:ring-1 focus:ring-indigo-600/20 transition-all"
        value={cond.value}
        onChange={(e) => {
          const v = e.target.value
          const num = Number(v)
          onChange({ ...cond, value: isNaN(num) || v === '' ? v : num })
        }}
      />

      {/* Remove button */}
      <button
        onClick={onRemove}
        className="ml-auto flex-shrink-0 w-5 h-5 flex items-center justify-center rounded-md text-gray-400 hover:text-red-600 hover:bg-red-600/10 transition-all text-sm leading-none"
        title="Remove condition"
      >
        &times;
      </button>
    </div>
  )
}

interface ConditionSectionProps {
  label: string
  icon: string
  accent: 'green' | 'red'
  conditions: Condition[]
  onChange: (conditions: Condition[]) => void
}

function ConditionSection({ label, icon, accent, conditions, onChange }: ConditionSectionProps) {
  const updateAt = (idx: number, c: Condition) => {
    const next = [...conditions]
    next[idx] = c
    onChange(next)
  }
  const removeAt = (idx: number) => {
    onChange(conditions.filter((_, i) => i !== idx))
  }
  const add = () => {
    if (conditions.length < 10) onChange([...conditions, defaultCondition()])
  }

  const headerColor = accent === 'green' ? 'text-green-600' : 'text-red-600'
  const dotColor    = accent === 'green' ? 'bg-green-600' : 'bg-red-600'
  const addColor    = accent === 'green'
    ? 'text-green-600/70 hover:text-green-600 hover:bg-green-600/10'
    : 'text-red-600/70 hover:text-red-600 hover:bg-red-600/10'

  return (
    <div className="space-y-2">
      {/* Section header */}
      <div className="flex items-center gap-2">
        <span className={`w-1.5 h-1.5 rounded-full ${dotColor} flex-shrink-0`} />
        <span className={`text-[11px] font-sans font-semibold uppercase tracking-widest ${headerColor}`}>
          {icon} {label}
        </span>
        <span className="text-[10px] font-mono text-gray-400 ml-1">
          ({conditions.length}/10)
        </span>
      </div>

      {/* Condition cards */}
      <div className="space-y-1.5">
        {conditions.length === 0 && (
          <div className="text-xs font-sans text-gray-400 italic px-3 py-2 bg-gray-50/30 rounded-lg border border-dashed border-gray-200">
            No conditions — all bars will trigger
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

      {/* Add button */}
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

export function StrategyBuilder() {
  const {
    entryConditions, exitConditions, conditionLogic,
    setEntryConditions, setExitConditions, setConditionLogic, reset,
  } = useBacktestStore()

  return (
    <div className="card rounded-2xl shadow-card p-5 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-indigo-50 flex items-center justify-center">
            <svg className="w-3.5 h-3.5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
              />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
          </div>
          <h3 className="text-sm font-sans font-semibold text-gray-800">Strategy Builder</h3>
        </div>

        <div className="flex items-center gap-2">
          {/* AND / OR toggle */}
          <div className="flex bg-white rounded-lg overflow-hidden text-[11px] border border-gray-200">
            <button
              onClick={() => setConditionLogic('AND')}
              className={`px-3 py-1.5 font-sans font-semibold tracking-wide transition-all ${
                conditionLogic === 'AND'
                  ? 'bg-indigo-500 text-white shadow-glow-blue'
                  : 'text-gray-400 hover:text-gray-500'
              }`}
            >
              AND
            </button>
            <button
              onClick={() => setConditionLogic('OR')}
              className={`px-3 py-1.5 font-sans font-semibold tracking-wide transition-all ${
                conditionLogic === 'OR'
                  ? 'bg-indigo-500 text-white shadow-glow-blue'
                  : 'text-gray-400 hover:text-gray-500'
              }`}
            >
              OR
            </button>
          </div>

          {/* Reset */}
          <button
            onClick={reset}
            className="flex items-center gap-1 text-[11px] font-sans text-gray-400 hover:text-gray-500 hover:bg-gray-50/60 px-2 py-1.5 rounded-lg transition-all"
            title="Reset to defaults"
          >
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              />
            </svg>
            Reset
          </button>
        </div>
      </div>

      {/* Divider */}
      <div className="border-t border-gray-200" />

      <ConditionSection
        label="Entry Conditions"
        icon=""
        accent="green"
        conditions={entryConditions}
        onChange={setEntryConditions}
      />

      <div className="border-t border-gray-200" />

      <ConditionSection
        label="Exit Conditions"
        icon=""
        accent="red"
        conditions={exitConditions}
        onChange={setExitConditions}
      />
    </div>
  )
}
