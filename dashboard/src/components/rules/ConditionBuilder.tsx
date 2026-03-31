/**
 * Visual condition builder for rules — replaces raw JSON textarea (F5-03).
 * Reuses primitives from @/utils/conditionHelpers and the pattern from
 * backtest/StrategyBuilder's ConditionRow.
 */
import type { Condition, Indicator } from '@/types'
import { INDICATORS, OPERATORS, INDICATOR_PARAMS, defaultCondition, defaultParams } from '@/utils/conditionHelpers'

interface ConditionBuilderProps {
  conditions: Condition[]
  onChange: (conditions: Condition[]) => void
}

function ConditionRow({
  cond,
  onUpdate,
  onRemove,
}: {
  cond: Condition
  onUpdate: (c: Condition) => void
  onRemove: () => void
}) {
  const paramDefs = INDICATOR_PARAMS[cond.indicator] || []

  return (
    <div className="flex items-center gap-2 rounded-xl border border-zinc-800 bg-zinc-900 px-3 py-2.5 hover:bg-zinc-800/60 transition-colors">
      <select
        className="bg-zinc-900 border border-zinc-800 rounded-lg px-2 py-1 text-xs font-mono text-zinc-100 w-24 focus:outline-none focus:border-indigo-600/50 cursor-pointer"
        value={cond.indicator}
        onChange={(e) => {
          const ind = e.target.value as Indicator
          onUpdate({ ...cond, indicator: ind, params: defaultParams(ind) })
        }}
      >
        {INDICATORS.map((i) => <option key={i} value={i}>{i}</option>)}
      </select>

      {paramDefs.map((p) => (
        <div key={p.key} className="flex items-center gap-1">
          <span className="text-[10px] font-sans text-zinc-500">{p.label}</span>
          {p.key === 'band' ? (
            <select
              className="bg-zinc-900 border border-zinc-800 rounded-lg px-1 py-1 text-xs font-mono text-zinc-100 w-16 focus:outline-none focus:border-indigo-600/50 cursor-pointer"
              value={String(cond.params[p.key] ?? 'mid')}
              onChange={(e) => onUpdate({ ...cond, params: { ...cond.params, [p.key]: e.target.value } })}
            >
              <option value="upper">Upper</option>
              <option value="mid">Mid</option>
              <option value="lower">Lower</option>
            </select>
          ) : (
            <input
              type="number"
              className="bg-zinc-900 border border-zinc-800 rounded-lg px-1 py-1 text-xs font-mono text-zinc-100 w-14 focus:outline-none focus:border-indigo-600/50"
              value={cond.params[p.key] ?? p.def}
              onChange={(e) => onUpdate({ ...cond, params: { ...cond.params, [p.key]: Number(e.target.value) } })}
            />
          )}
        </div>
      ))}

      <select
        className="bg-zinc-900 border border-zinc-800 rounded-lg px-2 py-1 text-xs font-mono text-zinc-100 w-32 focus:outline-none focus:border-indigo-600/50 cursor-pointer"
        value={cond.operator}
        onChange={(e) => onUpdate({ ...cond, operator: e.target.value })}
      >
        {OPERATORS.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>

      <input
        className="bg-zinc-900 border border-zinc-800 rounded-lg px-2 py-1 text-xs font-mono text-zinc-100 w-20 focus:outline-none focus:border-indigo-600/50"
        value={cond.value}
        onChange={(e) => {
          const v = e.target.value
          const num = Number(v)
          onUpdate({ ...cond, value: isNaN(num) || v === '' ? v : num })
        }}
      />

      <button
        onClick={onRemove}
        className="ml-auto shrink-0 w-5 h-5 flex items-center justify-center rounded-md text-zinc-500 hover:text-red-400 hover:bg-red-600/10 transition-all text-sm"
        title="Remove condition"
      >
        &times;
      </button>
    </div>
  )
}

export function ConditionBuilder({ conditions, onChange }: ConditionBuilderProps) {
  const updateAt = (index: number, updated: Condition) => {
    const next = [...conditions]
    next[index] = updated
    onChange(next)
  }

  const removeAt = (index: number) => {
    onChange(conditions.filter((_, i) => i !== index))
  }

  const addCondition = () => {
    onChange([...conditions, defaultCondition()])
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm text-[var(--text-secondary)]">Conditions</span>
        <button
          type="button"
          onClick={addCondition}
          className="text-xs font-sans text-indigo-400 hover:text-indigo-300 transition-colors"
        >
          + Add condition
        </button>
      </div>

      {conditions.length === 0 ? (
        <p className="text-xs text-zinc-500 italic py-3 text-center border border-dashed border-zinc-800 rounded-xl">
          No conditions — click "Add condition" to start
        </p>
      ) : (
        <div className="space-y-2">
          {conditions.map((cond, i) => (
            <ConditionRow
              key={i}
              cond={cond}
              onUpdate={(c) => updateAt(i, c)}
              onRemove={() => removeAt(i)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
