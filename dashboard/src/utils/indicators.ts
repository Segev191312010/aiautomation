/**
 * Technical indicator calculations — pure TypeScript, no external deps.
 *
 * All functions accept OHLCVBar[] and return time-stamped data points
 * compatible with lightweight-charts setData().
 */
import type { OHLCVBar } from '@/types'

// ── Indicator registry ────────────────────────────────────────────────────────

export type IndicatorId = 'sma20' | 'sma50' | 'ema12' | 'ema26' | 'bb' | 'vwap' | 'rsi' | 'macd'

export interface IndicatorDef {
  id:    IndicatorId
  label: string
  type:  'overlay' | 'oscillator'
  color: string
}

export const INDICATOR_DEFS: IndicatorDef[] = [
  { id: 'sma20', label: 'SMA 20',  type: 'overlay',    color: '#60a5fa' },
  { id: 'sma50', label: 'SMA 50',  type: 'overlay',    color: '#c084fc' },
  { id: 'ema12', label: 'EMA 12',  type: 'overlay',    color: '#34d399' },
  { id: 'ema26', label: 'EMA 26',  type: 'overlay',    color: '#fbbf24' },
  { id: 'bb',    label: 'BB (20)', type: 'overlay',    color: '#94a3b8' },
  { id: 'vwap',  label: 'VWAP',   type: 'overlay',    color: '#fb923c' },
  { id: 'rsi',   label: 'RSI 14', type: 'oscillator', color: '#f472b6' },
  { id: 'macd',  label: 'MACD',   type: 'oscillator', color: '#38bdf8' },
]

// ── Result types ──────────────────────────────────────────────────────────────

export interface LinePoint   { time: number; value: number }
export interface BandsResult { upper: LinePoint[]; middle: LinePoint[]; lower: LinePoint[] }
export interface MACDResult  { macd: LinePoint[]; signal: LinePoint[]; histogram: LinePoint[] }

// ── SMA (rolling sum, O(n)) ───────────────────────────────────────────────────

export function calcSMA(bars: OHLCVBar[], period: number): LinePoint[] {
  const n = bars.length
  if (n < period || period <= 0) return []
  const result: LinePoint[] = []
  let sum = 0
  for (let i = 0; i < period; i++) sum += bars[i].close
  result.push({ time: bars[period - 1].time, value: +(sum / period).toFixed(4) })
  for (let i = period; i < n; i++) {
    sum += bars[i].close - bars[i - period].close
    result.push({ time: bars[i].time, value: +(sum / period).toFixed(4) })
  }
  return result
}

// ── EMA ───────────────────────────────────────────────────────────────────────

export function calcEMA(bars: OHLCVBar[], period: number): LinePoint[] {
  if (bars.length < period) return []
  const k = 2 / (period + 1)
  let ema = bars.slice(0, period).reduce((s, b) => s + b.close, 0) / period
  const result: LinePoint[] = [{ time: bars[period - 1].time, value: +ema.toFixed(4) }]
  for (let i = period; i < bars.length; i++) {
    ema = bars[i].close * k + ema * (1 - k)
    result.push({ time: bars[i].time, value: +ema.toFixed(4) })
  }
  return result
}

// ── Bollinger Bands (rolling, O(n)) ───────────────────────────────────────────
//
// Both the rolling mean and rolling variance update in O(1) per bar, so the
// whole pass is O(n) (the previous version rescanned the window → O(n·period)).
//
// Variance is computed from deviations against a fixed reference K (the first
// close), not against the raw price. Tracking Σ(x−K) and Σ(x−K)² and forming
//   var = mean(dev²) − mean(dev)²
// keeps the magnitudes small even for high-priced, tight-range symbols
// (e.g. BRK.A ~$600k ±0.1%), which would catastrophically cancel in the naive
// E[X²] − E[X]² form. The result matches the two-pass slice scan to well under
// 1e-5, i.e. exactly equal after toFixed(4) rounding.

export function calcBB(bars: OHLCVBar[], period = 20, mult = 2): BandsResult {
  const upper: LinePoint[] = [], middle: LinePoint[] = [], lower: LinePoint[] = []
  const n = bars.length
  if (n < period || period <= 0) return { upper, middle, lower }

  const k = bars[0].close // fixed reference offset to suppress float cancellation
  let sumDev = 0   // Σ (close − k)   over the window
  let sumDev2 = 0  // Σ (close − k)²  over the window
  for (let i = 0; i < period; i++) {
    const d = bars[i].close - k
    sumDev += d
    sumDev2 += d * d
  }

  const push = (i: number) => {
    const meanDev = sumDev / period
    const avg = k + meanDev
    // var = E[dev²] − E[dev]²; clamp tiny negatives from float drift.
    const variance = Math.max(0, sumDev2 / period - meanDev * meanDev)
    const std = Math.sqrt(variance)
    middle.push({ time: bars[i].time, value: +avg.toFixed(4) })
    upper.push({  time: bars[i].time, value: +(avg + mult * std).toFixed(4) })
    lower.push({  time: bars[i].time, value: +(avg - mult * std).toFixed(4) })
  }

  push(period - 1)
  for (let i = period; i < n; i++) {
    const dIn = bars[i].close - k
    const dOut = bars[i - period].close - k
    sumDev += dIn - dOut
    sumDev2 += dIn * dIn - dOut * dOut
    push(i)
  }
  return { upper, middle, lower }
}

// ── VWAP (cumulative) ─────────────────────────────────────────────────────────

export function calcVWAP(bars: OHLCVBar[]): LinePoint[] {
  let cumTPV = 0, cumVol = 0
  return bars.map((b) => {
    const tp = (b.high + b.low + b.close) / 3
    cumTPV += tp * b.volume
    cumVol += b.volume
    return { time: b.time, value: +(cumVol > 0 ? cumTPV / cumVol : b.close).toFixed(4) }
  })
}

// ── RSI ───────────────────────────────────────────────────────────────────────

export function calcRSI(bars: OHLCVBar[], period = 14): LinePoint[] {
  if (bars.length < period + 1) return []
  let avgGain = 0, avgLoss = 0
  for (let i = 1; i <= period; i++) {
    const d = bars[i].close - bars[i - 1].close
    if (d >= 0) avgGain += d; else avgLoss -= d
  }
  avgGain /= period
  avgLoss /= period
  const toRSI = (g: number, l: number) =>
    +(l === 0 ? 100 : 100 - 100 / (1 + g / l)).toFixed(2)
  const result: LinePoint[] = [{ time: bars[period].time, value: toRSI(avgGain, avgLoss) }]
  for (let i = period + 1; i < bars.length; i++) {
    const d = bars[i].close - bars[i - 1].close
    avgGain = (avgGain * (period - 1) + Math.max(d, 0)) / period
    avgLoss = (avgLoss * (period - 1) + Math.max(-d, 0)) / period
    result.push({ time: bars[i].time, value: toRSI(avgGain, avgLoss) })
  }
  return result
}

// ── MACD ──────────────────────────────────────────────────────────────────────

export function calcMACD(bars: OHLCVBar[], fast = 12, slow = 26, sig = 9): MACDResult {
  const eFast = calcEMA(bars, fast)
  const eSlow = calcEMA(bars, slow)
  const slowMap = new Map(eSlow.map((p) => [p.time, p.value]))

  const macdLine: LinePoint[] = eFast
    .filter((p) => slowMap.has(p.time))
    .map((p) => ({ time: p.time, value: +(p.value - slowMap.get(p.time)!).toFixed(4) }))

  if (macdLine.length < sig) return { macd: macdLine, signal: [], histogram: [] }

  const k = 2 / (sig + 1)
  let sigEMA = macdLine.slice(0, sig).reduce((s, p) => s + p.value, 0) / sig
  const signalLine: LinePoint[] = [{ time: macdLine[sig - 1].time, value: +sigEMA.toFixed(4) }]
  for (let i = sig; i < macdLine.length; i++) {
    sigEMA = macdLine[i].value * k + sigEMA * (1 - k)
    signalLine.push({ time: macdLine[i].time, value: +sigEMA.toFixed(4) })
  }

  const sigMap = new Map(signalLine.map((p) => [p.time, p.value]))
  const histogram = macdLine
    .filter((p) => sigMap.has(p.time))
    .map((p) => ({ time: p.time, value: +(p.value - sigMap.get(p.time)!).toFixed(4) }))

  return { macd: macdLine, signal: signalLine, histogram }
}

// ── Helper: interval string → seconds ─────────────────────────────────────────

export function intervalToSeconds(interval: string): number {
  if (interval.endsWith('mo')) return parseInt(interval) * 86_400 * 30
  if (interval.endsWith('wk')) return parseInt(interval) * 86_400 * 7
  if (interval.endsWith('d'))  return parseInt(interval) * 86_400
  if (interval.endsWith('h'))  return parseInt(interval) * 3_600
  if (interval.endsWith('m'))  return parseInt(interval) * 60
  return 86_400
}
