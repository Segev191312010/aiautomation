import React from 'react'
import { useScreenerStore } from '@/store'
import type { ScanFilter, ScreenerIndicator, ScreenerOperator, FilterValue } from '@/types'

const INDICATORS: { value: ScreenerIndicator; label: string; defaultParams: Record<string, number> }[] = [
  { value: 'RSI',        label: 'RSI',         defaultParams: { length: 14 } },
  { value: 'SMA',        label: 'SMA',         defaultParams: { length: 50 } },
  { value: 'EMA',        label: 'EMA',         defaultParams: { length: 20 } },
  { value: 'MACD',       label: 'MACD',        defaultParams: { fast: 12, slow: 26, signal: 9 } },
  { value: 'BBANDS',     label: 'Bollinger',   defaultParams: { length: 20 } },
  { value: 'ATR',        label: 'ATR',         defaultParams: { length: 14 } },
  { value: 'STOCH',      label: 'Stochastic',  defaultParams: { k: 14, d: 3 } },
  { value: 'PRICE',      label: 'Price',       defaultParams: {} },
  { value: 'VOLUME',     label: 'Volume',      defaultParams: {} },
  { value: 'CHANGE_PCT', label: 'Change %',    defaultParams: {} },
]

const OPERATORS: { value: ScreenerOperator; label: string }[] = [
  { value: 'GT',             label: '>' },
  { value: 'GTE',            label: '>=' },
  { value: 'LT',             label: '<' },
  { value: 'LTE',            label: '<=' },
  { value: 'CROSSES_ABOVE',  label: 'Crosses Above' },
  { value: 'CROSSES_BELOW',  label: 'Crosses Below' },
]

const isCrossOperator = (op: string) => op === 'CROSSES_ABOVE' || op === 'CROSSES_BELOW'

const selectClass = 'px-2.5 py-1.5 bg-terminal-input border border-white/[0.06] rounded-xl text-xs font-sans text-terminal-text focus:border-indigo-500/50 focus:outline-none transition-colors'
const inputClass  = 'px-2 py-1.5 bg-terminal-input border border-white/[0.06] rounded-xl text-xs font-mono text-terminal-text text-center focus:border-indigo-500/50 focus:outline-none transition-colors'

interface FilterRowProps {
  filter: ScanFilter
  index: number
  canRemove: boolean
}

function FilterRow({ filter, index, canRemove }: FilterRowProps) {
  const { updateFilter, removeFilter } = useScreenerStore()

  const handleIndicatorChange = (indicator: ScreenerIndicator) => {
    const meta = INDICATORS.find((i) => i.value === indicator)
    updateFilter(index, {
      ...filter,
      indicator,
      params: meta?.defaultParams ?? {},
    })
  }

  const handleOperatorChange = (operator: ScreenerOperator) => {
    const newFilter = { ...filter, operator }
    // CROSSES operators force indicator type for value
    if (isCrossOperator(operator) && filter.value.type === 'number') {
      newFilter.value = {
        type: 'indicator',
        indicator: 'SMA',
        params: { length: 200 },
      }
    }
    updateFilter(index, newFilter)
  }

  const handleValueTypeChange = (type: 'number' | 'indicator') => {
    if (type === 'number') {
      updateFilter(index, {
        ...filter,
        value: { type: 'number', number: 0 },
      })
    } else {
      updateFilter(index, {
        ...filter,
        value: { type: 'indicator', indicator: 'SMA', params: { length: 200 } },
      })
    }
  }

  const handleValueNumberChange = (num: number) => {
    updateFilter(index, {
      ...filter,
      value: { ...filter.value, number: num },
    })
  }

  const handleValueIndicatorChange = (indicator: ScreenerIndicator) => {
    const meta = INDICATORS.find((i) => i.value === indicator)
    updateFilter(index, {
      ...filter,
      value: { ...filter.value, indicator, params: meta?.defaultParams ?? {} },
    })
  }

  const handleParamChange = (key: string, val: number) => {
    updateFilter(index, {
      ...filter,
      params: { ...filter.params, [key]: val },
    })
  }

  const handleValueParamChange = (key: string, val: number) => {
    updateFilter(index, {
      ...filter,
      value: { ...filter.value, params: { ...(filter.value.params ?? {}), [key]: val } },
    })
  }

  const handleMultiplierChange = (multiplier: number) => {
    updateFilter(index, {
      ...filter,
      value: { ...filter.value, multiplier },
    })
  }

  const forcedIndicator = isCrossOperator(filter.operator)

  return (
    <div className="flex items-center gap-2 flex-wrap p-2 rounded-xl bg-white/[0.02] border border-white/[0.04]">
      {/* Indicator select */}
      <select
        value={filter.indicator}
        onChange={(e) => handleIndicatorChange(e.target.value as ScreenerIndicator)}
        className={selectClass}
      >
        {INDICATORS.map((i) => (
          <option key={i.value} value={i.value}>{i.label}</option>
        ))}
      </select>

      {/* Params */}
      {Object.entries(filter.params).map(([key, val]) => (
        <input
          key={key}
          type="number"
          value={val}
          onChange={(e) => handleParamChange(key, Number(e.target.value))}
          title={key}
          className={`w-14 ${inputClass}`}
        />
      ))}

      {/* Operator select */}
      <select
        value={filter.operator}
        onChange={(e) => handleOperatorChange(e.target.value as ScreenerOperator)}
        className={selectClass}
      >
        {OPERATORS.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>

      {/* Value type toggle (hidden when CROSSES forces indicator) */}
      {!forcedIndicator && (
        <select
          value={filter.value.type}
          onChange={(e) => handleValueTypeChange(e.target.value as 'number' | 'indicator')}
          className={`${selectClass} text-terminal-dim`}
        >
          <option value="number">Value</option>
          <option value="indicator">Indicator</option>
        </select>
      )}

      {/* Value input */}
      {filter.value.type === 'number' && !forcedIndicator ? (
        <input
          type="number"
          value={filter.value.number ?? 0}
          onChange={(e) => handleValueNumberChange(Number(e.target.value))}
          step="any"
          className={`w-20 ${inputClass}`}
        />
      ) : (
        <>
          <select
            value={filter.value.indicator ?? 'SMA'}
            onChange={(e) => handleValueIndicatorChange(e.target.value as ScreenerIndicator)}
            className={selectClass}
          >
            {INDICATORS.map((i) => (
              <option key={i.value} value={i.value}>{i.label}</option>
            ))}
          </select>
          {Object.entries(filter.value.params ?? {}).map(([key, val]) => (
            <input
              key={key}
              type="number"
              value={val}
              onChange={(e) => handleValueParamChange(key, Number(e.target.value))}
              title={key}
              className={`w-14 ${inputClass}`}
            />
          ))}
          {/* Multiplier */}
          {(filter.value.multiplier ?? 1) !== 1 || filter.value.type === 'indicator' ? (
            <div className="flex items-center gap-1">
              <span className="text-xs font-sans text-terminal-ghost">x</span>
              <input
                type="number"
                value={filter.value.multiplier ?? 1}
                onChange={(e) => handleMultiplierChange(Number(e.target.value))}
                step="0.1"
                min="0.1"
                className={`w-14 ${inputClass}`}
              />
            </div>
          ) : null}
        </>
      )}

      {/* Remove button */}
      {canRemove && (
        <button
          onClick={() => removeFilter(index)}
          className="p-1 rounded-lg text-terminal-ghost hover:text-terminal-red hover:bg-terminal-red/10 transition-colors ml-auto"
          title="Remove filter"
        >
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
            <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
          </svg>
        </button>
      )}
    </div>
  )
}

export default function FilterBuilder() {
  const { filters, addFilter } = useScreenerStore()

  return (
    <div className="space-y-2">
      {filters.map((filter, i) => (
        <FilterRow key={i} filter={filter} index={i} canRemove={filters.length > 1} />
      ))}
      <button
        onClick={addFilter}
        disabled={filters.length >= 15}
        className="px-3.5 py-1.5 rounded-xl text-xs font-sans font-medium text-indigo-400 hover:bg-indigo-500/10 disabled:opacity-40 transition-colors"
      >
        + Add Filter
      </button>
    </div>
  )
}
