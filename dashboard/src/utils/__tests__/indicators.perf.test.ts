/**
 * Phase C1 — regression tests for the sliding-window rewrite of SMA and BB.
 *
 * Each new implementation is compared to a reference implementation that
 * re-sums the window every bar (the behavior before the refactor). All outputs
 * must match within 1e-9 on raw computed values after rounding (toFixed(4))
 * the difference is essentially zero.
 *
 * E1 adds broader indicator unit tests; this file only guards the refactor.
 */
import { describe, expect, it } from 'vitest'
import type { OHLCVBar } from '@/types'
import { calcSMA, calcBB } from '@/utils/indicators'

// ── Reference implementations (pre-refactor behavior) ─────────────────────────

function refSMA(bars: OHLCVBar[], period: number) {
  const out: Array<{ time: number; value: number }> = []
  for (let i = period - 1; i < bars.length; i++) {
    let sum = 0
    for (let j = i - period + 1; j <= i; j++) sum += bars[j].close
    out.push({ time: bars[i].time, value: +(sum / period).toFixed(4) })
  }
  return out
}

function refBB(bars: OHLCVBar[], period = 20, mult = 2) {
  const upper: Array<{ time: number; value: number }> = []
  const middle: Array<{ time: number; value: number }> = []
  const lower: Array<{ time: number; value: number }> = []
  for (let i = period - 1; i < bars.length; i++) {
    const slice = bars.slice(i - period + 1, i + 1).map((b) => b.close)
    const avg = slice.reduce((s, v) => s + v, 0) / period
    const std = Math.sqrt(slice.reduce((s, v) => s + (v - avg) ** 2, 0) / period)
    middle.push({ time: bars[i].time, value: +avg.toFixed(4) })
    upper.push({  time: bars[i].time, value: +(avg + mult * std).toFixed(4) })
    lower.push({  time: bars[i].time, value: +(avg - mult * std).toFixed(4) })
  }
  return { upper, middle, lower }
}

// ── Deterministic fixture (GBM-like 500-bar series) ───────────────────────────

function makeBars(n: number, seed = 42): OHLCVBar[] {
  // Mulberry32 PRNG for deterministic output
  let s = seed >>> 0
  const rand = () => {
    s = (s + 0x6D2B79F5) | 0
    let t = s
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }

  const bars: OHLCVBar[] = []
  let price = 150
  const baseTime = 1_700_000_000
  for (let i = 0; i < n; i++) {
    const ret = (rand() - 0.5) * 0.04 // ±2%
    price = Math.max(1, price * (1 + ret))
    const open = price * (1 - (rand() - 0.5) * 0.005)
    const high = Math.max(open, price) * (1 + rand() * 0.01)
    const low  = Math.min(open, price) * (1 - rand() * 0.01)
    bars.push({
      time:   baseTime + i * 86_400,
      open,
      high,
      low,
      close:  price,
      volume: 1_000_000 + Math.floor(rand() * 500_000),
    })
  }
  return bars
}

describe('C1 rolling-window regression', () => {
  const bars = makeBars(500)

  it.each([10, 20, 50, 100])('calcSMA(%i) matches reference impl', (period) => {
    const a = calcSMA(bars, period)
    const b = refSMA(bars, period)
    expect(a).toHaveLength(b.length)
    for (let i = 0; i < a.length; i++) {
      expect(a[i].time).toBe(b[i].time)
      expect(Math.abs(a[i].value - b[i].value)).toBeLessThan(1e-9)
    }
  })

  it.each([
    [20, 2],
    [14, 2.5],
    [50, 1.5],
  ])('calcBB(period=%i, mult=%f) matches reference impl', (period, mult) => {
    const a = calcBB(bars, period, mult)
    const b = refBB(bars, period, mult)
    for (const field of ['upper', 'middle', 'lower'] as const) {
      expect(a[field]).toHaveLength(b[field].length)
      for (let i = 0; i < a[field].length; i++) {
        expect(a[field][i].time).toBe(b[field][i].time)
        // Rolling sum-of-squares can differ from slice-reduce by tiny float
        // drift; toFixed(4) rounds away anything <1e-5. Assert the rounded
        // values match exactly.
        expect(a[field][i].value).toBe(b[field][i].value)
      }
    }
  })

  it('calcSMA returns empty when bars < period', () => {
    expect(calcSMA(bars.slice(0, 5), 10)).toEqual([])
  })

  it('calcBB returns empty bands when bars < period', () => {
    const { upper, middle, lower } = calcBB(bars.slice(0, 5), 20, 2)
    expect(upper).toEqual([])
    expect(middle).toEqual([])
    expect(lower).toEqual([])
  })
})
