import React from 'react'
import clsx from 'clsx'
import { useScreenerStore } from '@/store'
import type { ScanFilter, ScreenerIndicator, ScreenerOperator } from '@/types'

const INDICATORS: { value: ScreenerIndicator; label: string; defaultParams: Record<string, number> }[] = [
  { value: 'RSI', label: 'RSI', defaultParams: { length: 14 } },
  { value: 'SMA', label: 'SMA', defaultParams: { length: 50 } },
  { value: 'EMA', label: 'EMA', defaultParams: { length: 20 } },
  { value: 'MACD', label: 'MACD', defaultParams: { fast: 12, slow: 26, signal: 9 } },
  { value: 'BBANDS', label: 'Bollinger', defaultParams: { length: 20 } },
  { value: 'ATR', label: 'ATR', defaultParams: { length: 14 } },
  { value: 'STOCH', label: 'Stochastic', defaultParams: { k: 14, d: 3 } },
  { value: 'PRICE', label: 'Price', defaultParams: {} },
  { value: 'VOLUME', label: 'Volume', defaultParams: {} },
  { value: 'CHANGE_PCT', label: 'Change %', defaultParams: {} },
]

const OPERATORS: { value: ScreenerOperator; label: string; tone: string }[] = [
  { value: 'GT', label: '>', tone: 'text-emerald-300 bg-emerald-500/10 border-emerald-200' },
  { value: 'GTE', label: '>=', tone: 'text-emerald-300 bg-emerald-500/10 border-emerald-200' },
  { value: 'LT', label: '<', tone: 'text-red-700 bg-red-500/10 border-red-200' },
  { value: 'LTE', label: '<=', tone: 'text-red-700 bg-red-500/10 border-red-200' },
  { value: 'CROSSES_ABOVE', label: 'Crosses Up', tone: 'text-sky-700 bg-sky-50 border-sky-200' },
  { value: 'CROSSES_BELOW', label: 'Crosses Down', tone: 'text-amber-700 bg-amber-50 border-amber-200' },
]

const isCrossOperator = (op: string) => op === 'CROSSES_ABOVE' || op === 'CROSSES_BELOW'

const selectClass =
  'rounded-lg border border-zinc-800 bg-zinc-900 px-2.5 py-2 text-xs font-sans text-zinc-100 ' +
  'focus:border-zinc-700 focus:outline-none transition-colors'

const inputClass =
  'rounded-lg border border-zinc-800 bg-zinc-900 px-2.5 py-2 text-xs font-mono text-zinc-100 text-center ' +
  'focus:border-zinc-700 focus:outline-none transition-colors'

interface FilterRowProps {
  filter: ScanFilter
  index: number
  canRemove: boolean
}

function FilterRow({ filter, index, canRemove }: FilterRowProps) {
  const { updateFilter, removeFilter } = useScreenerStore()

  const handleIndicatorChange = (indicator: ScreenerIndicator) => {
    const meta = INDICATORS.find((item) => item.value === indicator)
    updateFilter(index, {
      ...filter,
      indicator,
      params: meta?.defaultParams ?? {},
    })
  }

  const handleOperatorChange = (operator: ScreenerOperator) => {
    const newFilter = { ...filter, operator }
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
      return
    }

    updateFilter(index, {
      ...filter,
      value: { type: 'indicator', indicator: 'SMA', params: { length: 200 } },
    })
  }

  const handleValueNumberChange = (value: number) => {
    updateFilter(index, {
      ...filter,
      value: { ...filter.value, number: value },
    })
  }

  const handleValueIndicatorChange = (indicator: ScreenerIndicator) => {
    const meta = INDICATORS.find((item) => item.value === indicator)
    updateFilter(index, {
      ...filter,
      value: { ...filter.value, indicator, params: meta?.defaultParams ?? {} },
    })
  }

  const handleParamChange = (key: string, value: number) => {
    updateFilter(index, {
      ...filter,
      params: { ...filter.params, [key]: value },
    })
  }

  const handleValueParamChange = (key: string, value: number) => {
    updateFilter(index, {
      ...filter,
      value: { ...filter.value, params: { ...(filter.value.params ?? {}), [key]: value } },
    })
  }

  const handleMultiplierChange = (multiplier: number) => {
    updateFilter(index, {
      ...filter,
      value: { ...filter.value, multiplier },
    })
  }

  const forcedIndicator = isCrossOperator(filter.operator)
  const operatorMeta = OPERATORS.find((item) => item.value === filter.operator)

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-4">
      <div className="flex items-center justify-between gap-3">
        <div className="inline-flex items-center gap-2">
          <span className="rounded-full bg-zinc-900 px-2 py-1 text-[10px] font-mono text-zinc-400">
            Rule {index + 1}
          </span>
          <span className={clsx('rounded-full border px-2 py-1 text-[10px] font-mono', operatorMeta?.tone ?? 'text-zinc-400 bg-zinc-900 border-zinc-800')}>
            {operatorMeta?.label ?? filter.operator}
          </span>
        </div>

        {canRemove && (
          <button
            type="button"
            onClick={() => removeFilter(index)}
            className="rounded-lg border border-transparent px-2 py-1 text-[11px] font-sans text-zinc-500 transition-colors hover:border-red-200 hover:bg-red-500/10 hover:text-red-400"
          >
            Remove
          </button>
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <select
          value={filter.indicator}
          onChange={(e) => handleIndicatorChange(e.target.value as ScreenerIndicator)}
          className={selectClass}
        >
          {INDICATORS.map((item) => (
            <option key={item.value} value={item.value}>{item.label}</option>
          ))}
        </select>

        {Object.entries(filter.params).map(([key, value]) => (
          <label key={key} className="inline-flex items-center gap-1.5">
            <span className="text-[10px] font-sans uppercase tracking-[0.12em] text-zinc-500">{key}</span>
            <input
              type="number"
              value={value}
              onChange={(e) => handleParamChange(key, Number(e.target.value))}
              className={`w-16 ${inputClass}`}
            />
          </label>
        ))}

        <select
          value={filter.operator}
          onChange={(e) => handleOperatorChange(e.target.value as ScreenerOperator)}
          className={clsx(selectClass, operatorMeta?.tone)}
        >
          {OPERATORS.map((item) => (
            <option key={item.value} value={item.value}>{item.label}</option>
          ))}
        </select>

        {!forcedIndicator && (
          <select
            value={filter.value.type}
            onChange={(e) => handleValueTypeChange(e.target.value as 'number' | 'indicator')}
            className={selectClass}
          >
            <option value="number">Value</option>
            <option value="indicator">Indicator</option>
          </select>
        )}

        {filter.value.type === 'number' && !forcedIndicator ? (
          <input
            type="number"
            value={filter.value.number ?? 0}
            onChange={(e) => handleValueNumberChange(Number(e.target.value))}
            step="any"
            className={`w-24 ${inputClass}`}
          />
        ) : (
          <>
            <select
              value={filter.value.indicator ?? 'SMA'}
              onChange={(e) => handleValueIndicatorChange(e.target.value as ScreenerIndicator)}
              className={selectClass}
            >
              {INDICATORS.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>

            {Object.entries(filter.value.params ?? {}).map(([key, value]) => (
              <label key={key} className="inline-flex items-center gap-1.5">
                <span className="text-[10px] font-sans uppercase tracking-[0.12em] text-zinc-500">{key}</span>
                <input
                  type="number"
                  value={value}
                  onChange={(e) => handleValueParamChange(key, Number(e.target.value))}
                  className={`w-16 ${inputClass}`}
                />
              </label>
            ))}

            <label className="inline-flex items-center gap-1.5">
              <span className="text-[10px] font-sans uppercase tracking-[0.12em] text-zinc-500">x</span>
              <input
                type="number"
                value={filter.value.multiplier ?? 1}
                onChange={(e) => handleMultiplierChange(Number(e.target.value))}
                step="0.1"
                min="0.1"
                className={`w-16 ${inputClass}`}
              />
            </label>
          </>
        )}
      </div>
    </div>
  )
}

export default function FilterBuilder() {
  const { filters, addFilter } = useScreenerStore()

  return (
    <div className="space-y-3">
      {filters.map((filter, index) => (
        <FilterRow key={index} filter={filter} index={index} canRemove={filters.length > 1} />
      ))}

      <button
        type="button"
        onClick={addFilter}
        disabled={filters.length >= 15}
        className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-[11px] font-sans font-medium text-zinc-200 transition-colors hover:border-zinc-700 hover:text-zinc-50 disabled:cursor-not-allowed disabled:opacity-40"
      >
        Add Filter {filters.length >= 15 ? '(max 15)' : ''}
      </button>
    </div>
  )
}
