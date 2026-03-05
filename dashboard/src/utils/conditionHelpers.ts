import type { Condition, Indicator } from '@/types'

export const INDICATORS: Indicator[] = ['RSI', 'SMA', 'EMA', 'MACD', 'BBANDS', 'ATR', 'STOCH', 'PRICE']
export const OPERATORS = ['>', '<', '>=', '<=', '==', 'crosses_above', 'crosses_below'] as const

export const INDICATOR_PARAMS: Record<Indicator, { key: string; label: string; def: number }[]> = {
  RSI:    [{ key: 'length', label: 'Length', def: 14 }],
  SMA:    [{ key: 'length', label: 'Length', def: 20 }],
  EMA:    [{ key: 'length', label: 'Length', def: 20 }],
  MACD:   [{ key: 'fast', label: 'Fast', def: 12 }, { key: 'slow', label: 'Slow', def: 26 }, { key: 'signal', label: 'Signal', def: 9 }],
  BBANDS: [{ key: 'length', label: 'Length', def: 20 }, { key: 'std', label: 'Std', def: 2 }, { key: 'band', label: 'Band', def: 0 }],
  ATR:    [{ key: 'length', label: 'Length', def: 14 }],
  STOCH:  [{ key: 'k', label: 'K', def: 14 }, { key: 'smooth_k', label: 'Smooth', def: 3 }, { key: 'd', label: 'D', def: 3 }],
  PRICE:  [],
}

export function defaultParams(ind: Indicator): Record<string, number | string> {
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

export function defaultCondition(): Condition {
  return { indicator: 'RSI', params: { length: 14 }, operator: '<', value: 30 }
}

export function formatConditionSummary(cond: Condition): string {
  const params = Object.values(cond.params || {})
  const paramsStr = params.length > 0 ? `(${params.join(', ')})` : ''
  return `${cond.indicator}${paramsStr} ${cond.operator} ${cond.value}`
}
