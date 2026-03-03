import { useBacktestStore } from '@/store'
import type { Condition, Indicator } from '@/types'

const INDICATORS: Indicator[] = ['RSI', 'SMA', 'EMA', 'MACD', 'BBANDS', 'ATR', 'STOCH', 'PRICE']
const OPERATORS = ['>', '<', '>=', '<=', '==', 'crosses_above', 'crosses_below']

const INDICATOR_PARAMS: Record<Indicator, { key: string; label: string; def: number }[]> = {
  RSI:    [{ key: 'length', label: 'Length', def: 14 }],
  SMA:    [{ key: 'length', label: 'Length', def: 20 }],
  EMA:    [{ key: 'length', label: 'Length', def: 20 }],
  MACD:   [{ key: 'fast', label: 'Fast', def: 12 }, { key: 'slow', label: 'Slow', def: 26 }, { key: 'signal', label: 'Signal', def: 9 }],
  BBANDS: [{ key: 'length', label: 'Length', def: 20 }, { key: 'std', label: 'Std', def: 2 }, { key: 'band', label: 'Band', def: 0 }],
  ATR:    [{ key: 'length', label: 'Length', def: 14 }],
  STOCH:  [{ key: 'k', label: 'K', def: 14 }, { key: 'smooth_k', label: 'Smooth', def: 3 }, { key: 'd', label: 'D', def: 3 }],
  PRICE:  [],
}

function defaultCondition(): Condition {
  return { indicator: 'RSI', params: { length: 14 }, operator: '<', value: 30 }
}

function defaultParams(ind: Indicator): Record<string, number | string> {
  const result: Record<string, number | string> = {}
  for (const p of INDICATOR_PARAMS[ind]) {
    if (p.key === 'band') {
      result[p.key] = 'mid'
    } else {
      result[p.key] = p.def
    }
  }
  return result
}

interface ConditionRowProps {
  cond: Condition
  onChange: (c: Condition) => void
  onRemove: () => void
}

function ConditionRow({ cond, onChange, onRemove }: ConditionRowProps) {
  const paramDefs = INDICATOR_PARAMS[cond.indicator] || []

  return (
    <div className="flex items-center gap-2 bg-gray-800/50 rounded px-2 py-1.5">
      {/* Indicator */}
      <select
        className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 w-24"
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
          <span className="text-xs text-gray-500">{p.label}</span>
          {p.key === 'band' ? (
            <select
              className="bg-gray-900 border border-gray-700 rounded px-1 py-1 text-sm text-gray-100 w-16"
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
              className="bg-gray-900 border border-gray-700 rounded px-1 py-1 text-sm text-gray-100 w-14"
              value={cond.params[p.key] ?? p.def}
              onChange={(e) => onChange({ ...cond, params: { ...cond.params, [p.key]: Number(e.target.value) } })}
            />
          )}
        </div>
      ))}

      {/* Operator */}
      <select
        className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 w-32"
        value={cond.operator}
        onChange={(e) => onChange({ ...cond, operator: e.target.value })}
      >
        {OPERATORS.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>

      {/* Value */}
      <input
        className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 w-20"
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
        className="text-red-400 hover:text-red-300 px-1 text-sm font-bold"
        title="Remove condition"
      >
        ×
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
      <h4 className="text-xs uppercase tracking-wider text-gray-400 mb-2">{label}</h4>
      <div className="space-y-1.5">
        {conditions.map((c, i) => (
          <ConditionRow key={i} cond={c} onChange={(v) => updateAt(i, v)} onRemove={() => removeAt(i)} />
        ))}
      </div>
      <button
        onClick={add}
        className="mt-2 text-xs text-blue-400 hover:text-blue-300"
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
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-200">Strategy</h3>
        <div className="flex items-center gap-2">
          <div className="flex bg-gray-800 rounded overflow-hidden text-xs">
            <button
              onClick={() => setConditionLogic('AND')}
              className={`px-2 py-1 ${conditionLogic === 'AND' ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-gray-200'}`}
            >
              AND
            </button>
            <button
              onClick={() => setConditionLogic('OR')}
              className={`px-2 py-1 ${conditionLogic === 'OR' ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-gray-200'}`}
            >
              OR
            </button>
          </div>
          <button
            onClick={reset}
            className="text-xs text-gray-500 hover:text-gray-300"
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
