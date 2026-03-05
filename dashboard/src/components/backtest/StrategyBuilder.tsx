import { useBacktestStore } from '@/store'
import type { Condition, Indicator } from '@/types'
import { INDICATORS, OPERATORS, INDICATOR_PARAMS, defaultCondition, defaultParams } from '@/utils/conditionHelpers'

interface ConditionRowProps {
  cond: Condition
  onChange: (c: Condition) => void
  onRemove: () => void
}

function ConditionRow({ cond, onChange, onRemove }: ConditionRowProps) {
  const paramDefs = INDICATOR_PARAMS[cond.indicator] || []

  return (
    <div className="flex items-center gap-2 bg-terminal-elevated/50 rounded-xl px-3 py-2">
      {/* Indicator */}
      <select
        className="bg-terminal-input border border-white/[0.06] rounded-xl px-2 py-1 text-sm font-sans text-terminal-text w-24 focus:outline-none focus:border-terminal-blue/40 transition-colors"
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
          <span className="text-xs font-sans text-terminal-ghost">{p.label}</span>
          {p.key === 'band' ? (
            <select
              className="bg-terminal-input border border-white/[0.06] rounded-xl px-1 py-1 text-sm font-sans text-terminal-text w-16 focus:outline-none focus:border-terminal-blue/40 transition-colors"
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
              className="bg-terminal-input border border-white/[0.06] rounded-xl px-1 py-1 text-sm font-mono text-terminal-text w-14 focus:outline-none focus:border-terminal-blue/40 transition-colors"
              value={cond.params[p.key] ?? p.def}
              onChange={(e) => onChange({ ...cond, params: { ...cond.params, [p.key]: Number(e.target.value) } })}
            />
          )}
        </div>
      ))}

      {/* Operator */}
      <select
        className="bg-terminal-input border border-white/[0.06] rounded-xl px-2 py-1 text-sm font-sans text-terminal-text w-32 focus:outline-none focus:border-terminal-blue/40 transition-colors"
        value={cond.operator}
        onChange={(e) => onChange({ ...cond, operator: e.target.value })}
      >
        {OPERATORS.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>

      {/* Value */}
      <input
        className="bg-terminal-input border border-white/[0.06] rounded-xl px-2 py-1 text-sm font-mono text-terminal-text w-20 focus:outline-none focus:border-terminal-blue/40 transition-colors"
        value={cond.value}
        onChange={(e) => {
          const v = e.target.value
          const num = Number(v)
          onChange({ ...cond, value: isNaN(num) || v === '' ? v : num })
        }}
      />

      {/* Remove */}
      <button
        onClick={onRemove}
        className="text-terminal-red/60 hover:text-terminal-red px-1 text-sm font-bold transition-colors"
        title="Remove condition"
      >
        &times;
      </button>
    </div>
  )
}

interface ConditionSectionProps {
  label: string
  conditions: Condition[]
  onChange: (conditions: Condition[]) => void
}

function ConditionSection({ label, conditions, onChange }: ConditionSectionProps) {
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

  return (
    <div>
      <h4 className="text-xs font-sans font-medium uppercase tracking-wider text-terminal-dim mb-2">{label}</h4>
      <div className="space-y-1.5">
        {conditions.map((c, i) => (
          <ConditionRow key={i} cond={c} onChange={(v) => updateAt(i, v)} onRemove={() => removeAt(i)} />
        ))}
      </div>
      <button
        onClick={add}
        className="mt-2 text-xs font-sans text-terminal-blue/80 hover:text-terminal-blue transition-colors disabled:opacity-40"
        disabled={conditions.length >= 10}
      >
        + Add Condition
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
    <div className="glass rounded-2xl shadow-glass p-5 space-y-5">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-sans font-medium text-terminal-text">Strategy</h3>
        <div className="flex items-center gap-2">
          {/* AND / OR toggle */}
          <div className="flex bg-terminal-elevated rounded-xl overflow-hidden text-xs border border-white/[0.06]">
            <button
              onClick={() => setConditionLogic('AND')}
              className={`px-3 py-1 font-sans font-medium transition-colors ${
                conditionLogic === 'AND'
                  ? 'bg-indigo-500 text-white'
                  : 'text-terminal-dim hover:text-terminal-text'
              }`}
            >
              AND
            </button>
            <button
              onClick={() => setConditionLogic('OR')}
              className={`px-3 py-1 font-sans font-medium transition-colors ${
                conditionLogic === 'OR'
                  ? 'bg-indigo-500 text-white'
                  : 'text-terminal-dim hover:text-terminal-text'
              }`}
            >
              OR
            </button>
          </div>
          <button
            onClick={reset}
            className="text-xs font-sans text-terminal-ghost hover:text-terminal-dim transition-colors"
            title="Reset to defaults"
          >
            Reset
          </button>
        </div>
      </div>

      <ConditionSection
        label="Entry Conditions"
        conditions={entryConditions}
        onChange={setEntryConditions}
      />

      <ConditionSection
        label="Exit Conditions"
        conditions={exitConditions}
        onChange={setExitConditions}
      />
    </div>
  )
}
