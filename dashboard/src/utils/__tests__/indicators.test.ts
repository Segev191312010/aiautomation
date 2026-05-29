/**
 * Parity suite for the O(n) sliding-window rewrite of src/utils/indicators.ts.
 *
 * 100 deterministic random 500-bar series are pushed through the production
 * implementations and a set of straightforward reference implementations kept
 * inline below (recompute-the-window / textbook recurrences — no incremental
 * tricks). Every output value must agree within 1e-9.
 *
 * This is the regression guard for the rewrite: if a window update introduces
 * float drift or an off-by-one, the parity assertion catches it. The golden
 * fixtures in indicators.spec.ts cover exact pinned values and edge cases;
 * indicators.perf.test.ts covers SMA/BB at extreme price magnitudes.
 */
import { describe, expect, it } from 'vitest'
import type { OHLCVBar } from '@/types'
import {
  calcSMA, calcEMA, calcBB, calcVWAP, calcRSI, calcMACD,
  type LinePoint,
} from '@/utils/indicators'

const TOL = 1e-9

// ── Deterministic PRNG (Mulberry32) ───────────────────────────────────────────

function mulberry32(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s = (s + 0x6d2b79f5) | 0
    let t = s
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function makeBars(n: number, seed: number): OHLCVBar[] {
  const rand = mulberry32(seed)
  const bars: OHLCVBar[] = []
  let price = 50 + rand() * 400 // varied base price across series
  const baseTime = 1_700_000_000
  for (let i = 0; i < n; i++) {
    const ret = (rand() - 0.5) * 0.05 // ±2.5%
    price = Math.max(1, price * (1 + ret))
    const open = price * (1 - (rand() - 0.5) * 0.006)
    const high = Math.max(open, price) * (1 + rand() * 0.012)
    const low = Math.min(open, price) * (1 - rand() * 0.012)
    bars.push({
      time: baseTime + i * 86_400,
      open,
      high,
      low,
      close: price,
      volume: Math.floor(rand() * 2_000_000), // includes occasional zero-ish vol
    })
  }
  return bars
}

// ── Reference implementations (naive, recompute-the-window) ────────────────────

function refSMA(bars: OHLCVBar[], period: number): LinePoint[] {
  const out: LinePoint[] = []
  if (period <= 0 || bars.length < period) return out
  for (let i = period - 1; i < bars.length; i++) {
    let sum = 0
    for (let j = i - period + 1; j <= i; j++) sum += bars[j].close
    out.push({ time: bars[i].time, value: +(sum / period).toFixed(4) })
  }
  return out
}

function refEMA(bars: OHLCVBar[], period: number): LinePoint[] {
  if (bars.length < period) return []
  const k = 2 / (period + 1)
  let ema = 0
  for (let i = 0; i < period; i++) ema += bars[i].close
  ema /= period
  const out: LinePoint[] = [{ time: bars[period - 1].time, value: +ema.toFixed(4) }]
  for (let i = period; i < bars.length; i++) {
    ema = bars[i].close * k + ema * (1 - k)
    out.push({ time: bars[i].time, value: +ema.toFixed(4) })
  }
  return out
}

function refBB(bars: OHLCVBar[], period = 20, mult = 2) {
  const upper: LinePoint[] = [], middle: LinePoint[] = [], lower: LinePoint[] = []
  if (period <= 0 || bars.length < period) return { upper, middle, lower }
  for (let i = period - 1; i < bars.length; i++) {
    const slice = bars.slice(i - period + 1, i + 1).map((b) => b.close)
    const avg = slice.reduce((s, v) => s + v, 0) / period
    const std = Math.sqrt(slice.reduce((s, v) => s + (v - avg) ** 2, 0) / period)
    middle.push({ time: bars[i].time, value: +avg.toFixed(4) })
    upper.push({ time: bars[i].time, value: +(avg + mult * std).toFixed(4) })
    lower.push({ time: bars[i].time, value: +(avg - mult * std).toFixed(4) })
  }
  return { upper, middle, lower }
}

function refVWAP(bars: OHLCVBar[]): LinePoint[] {
  let cumTPV = 0, cumVol = 0
  return bars.map((b) => {
    const tp = (b.high + b.low + b.close) / 3
    cumTPV += tp * b.volume
    cumVol += b.volume
    return { time: b.time, value: +(cumVol > 0 ? cumTPV / cumVol : b.close).toFixed(4) }
  })
}

function refRSI(bars: OHLCVBar[], period = 14): LinePoint[] {
  if (bars.length < period + 1) return []
  let avgGain = 0, avgLoss = 0
  for (let i = 1; i <= period; i++) {
    const d = bars[i].close - bars[i - 1].close
    if (d >= 0) avgGain += d
    else avgLoss -= d
  }
  avgGain /= period
  avgLoss /= period
  const toRSI = (g: number, l: number) =>
    +(l === 0 ? 100 : 100 - 100 / (1 + g / l)).toFixed(2)
  const out: LinePoint[] = [{ time: bars[period].time, value: toRSI(avgGain, avgLoss) }]
  for (let i = period + 1; i < bars.length; i++) {
    const d = bars[i].close - bars[i - 1].close
    avgGain = (avgGain * (period - 1) + Math.max(d, 0)) / period
    avgLoss = (avgLoss * (period - 1) + Math.max(-d, 0)) / period
    out.push({ time: bars[i].time, value: toRSI(avgGain, avgLoss) })
  }
  return out
}

function refMACD(bars: OHLCVBar[], fast = 12, slow = 26, sig = 9) {
  const eFast = refEMA(bars, fast)
  const eSlow = refEMA(bars, slow)
  const slowMap = new Map(eSlow.map((p) => [p.time, p.value]))
  const macdLine: LinePoint[] = eFast
    .filter((p) => slowMap.has(p.time))
    .map((p) => ({ time: p.time, value: +(p.value - slowMap.get(p.time)!).toFixed(4) }))
  if (macdLine.length < sig) return { macd: macdLine, signal: [], histogram: [] }
  const k = 2 / (sig + 1)
  let sigEMA = 0
  for (let i = 0; i < sig; i++) sigEMA += macdLine[i].value
  sigEMA /= sig
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

// ── Assertion helpers ──────────────────────────────────────────────────────────

function expectLineParity(a: LinePoint[], b: LinePoint[]) {
  expect(a).toHaveLength(b.length)
  for (let i = 0; i < a.length; i++) {
    expect(a[i].time).toBe(b[i].time)
    expect(Math.abs(a[i].value - b[i].value)).toBeLessThan(TOL)
  }
}

// ── Parity suite ───────────────────────────────────────────────────────────────

describe('indicators O(n) rewrite parity (100 × 500-bar random series)', () => {
  const SERIES = 100
  const BARS = 500
  const seriesList: OHLCVBar[][] = []
  for (let s = 0; s < SERIES; s++) seriesList.push(makeBars(BARS, 1000 + s))

  it('calcSMA matches reference across periods', () => {
    for (const bars of seriesList) {
      for (const period of [5, 20, 50, 200]) {
        expectLineParity(calcSMA(bars, period), refSMA(bars, period))
      }
    }
  })

  it('calcEMA matches reference across periods', () => {
    for (const bars of seriesList) {
      for (const period of [9, 12, 26, 50]) {
        expectLineParity(calcEMA(bars, period), refEMA(bars, period))
      }
    }
  })

  it('calcBB matches reference across (period, mult)', () => {
    for (const bars of seriesList) {
      for (const [period, mult] of [[20, 2], [14, 2.5], [50, 1.5]] as const) {
        const a = calcBB(bars, period, mult)
        const b = refBB(bars, period, mult)
        expectLineParity(a.upper, b.upper)
        expectLineParity(a.middle, b.middle)
        expectLineParity(a.lower, b.lower)
      }
    }
  })

  it('calcVWAP matches reference (cumulative)', () => {
    for (const bars of seriesList) {
      expectLineParity(calcVWAP(bars), refVWAP(bars))
    }
  })

  it('calcRSI matches reference across periods', () => {
    for (const bars of seriesList) {
      for (const period of [7, 14, 21]) {
        expectLineParity(calcRSI(bars, period), refRSI(bars, period))
      }
    }
  })

  it('calcMACD matches reference (macd/signal/histogram)', () => {
    for (const bars of seriesList) {
      const a = calcMACD(bars, 12, 26, 9)
      const b = refMACD(bars, 12, 26, 9)
      expectLineParity(a.macd, b.macd)
      expectLineParity(a.signal, b.signal)
      expectLineParity(a.histogram, b.histogram)
    }
  })
})
