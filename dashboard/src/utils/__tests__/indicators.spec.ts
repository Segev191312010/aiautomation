/**
 * Phase E1 — comprehensive unit tests for src/utils/indicators.ts.
 *
 * Uses hand-computed golden values for deterministic fixtures; the 500-bar
 * regression suite in indicators.perf.test.ts covers SMA/BB against a
 * reference implementation. Together they cover every exported function,
 * each numeric branch, and all documented edge cases.
 */
import { describe, expect, it } from 'vitest'
import type { OHLCVBar } from '@/types'
import {
  calcSMA, calcEMA, calcBB, calcVWAP, calcRSI, calcMACD,
  intervalToSeconds, INDICATOR_DEFS,
} from '@/utils/indicators'

// ── Helpers ───────────────────────────────────────────────────────────────────

function closesToBars(closes: number[]): OHLCVBar[] {
  return closes.map((c, i) => ({
    time: 1_700_000_000 + i * 86_400,
    open: c, high: c, low: c, close: c, volume: 100,
  }))
}

function makeOHLCVBars(data: Array<{ h: number; l: number; c: number; v: number }>): OHLCVBar[] {
  return data.map((d, i) => ({
    time: 1_700_000_000 + i * 86_400,
    open: d.c, high: d.h, low: d.l, close: d.c, volume: d.v,
  }))
}

function approx(actual: number, expected: number, tol = 1e-4) {
  expect(Math.abs(actual - expected)).toBeLessThan(tol)
}

// ── INDICATOR_DEFS registry ───────────────────────────────────────────────────

describe('INDICATOR_DEFS', () => {
  it('registers 8 indicators with unique IDs', () => {
    const ids = INDICATOR_DEFS.map((d) => d.id)
    expect(new Set(ids).size).toBe(ids.length)
    expect(ids).toEqual(['sma20', 'sma50', 'ema12', 'ema26', 'bb', 'vwap', 'rsi', 'macd'])
  })

  it('each entry has a valid type and hex color', () => {
    for (const def of INDICATOR_DEFS) {
      expect(['overlay', 'oscillator']).toContain(def.type)
      expect(def.color).toMatch(/^#[0-9a-f]{6}$/i)
      expect(def.label.length).toBeGreaterThan(0)
    }
  })
})

// ── calcSMA ───────────────────────────────────────────────────────────────────

describe('calcSMA', () => {
  it('computes rolling mean for [1..5] period=3', () => {
    const bars = closesToBars([1, 2, 3, 4, 5])
    const out = calcSMA(bars, 3)
    expect(out.map((p) => p.value)).toEqual([2, 3, 4])
  })

  it('returns empty for bars.length < period', () => {
    expect(calcSMA(closesToBars([1, 2]), 5)).toEqual([])
  })

  it('returns empty for period <= 0', () => {
    expect(calcSMA(closesToBars([1, 2, 3, 4, 5]), 0)).toEqual([])
    expect(calcSMA(closesToBars([1, 2, 3, 4, 5]), -2)).toEqual([])
  })

  it('returns empty for empty bars', () => {
    expect(calcSMA([], 3)).toEqual([])
  })

  it('period === bars.length yields single point', () => {
    const out = calcSMA(closesToBars([1, 2, 3]), 3)
    expect(out).toHaveLength(1)
    expect(out[0].value).toBe(2)
  })
})

// ── calcEMA ───────────────────────────────────────────────────────────────────

describe('calcEMA', () => {
  it('SMA-seeded EMA for [1..10] period=3 matches hand computation', () => {
    // SMA(1,2,3)=2; k=0.5; recursion with doubling series.
    const bars = closesToBars([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    const out = calcEMA(bars, 3)
    expect(out.map((p) => p.value)).toEqual([2, 3, 4, 5, 6, 7, 8, 9])
  })

  it('returns empty when bars.length < period', () => {
    expect(calcEMA(closesToBars([1, 2]), 5)).toEqual([])
  })

  it('first output time matches bars[period-1].time', () => {
    const bars = closesToBars([10, 11, 12, 13, 14])
    const out = calcEMA(bars, 3)
    expect(out[0].time).toBe(bars[2].time)
  })

  it('converges with k=2/(period+1) recursion', () => {
    // period=5, k=2/6=0.3333…
    const bars = closesToBars([100, 100, 100, 100, 100, 110])
    const out = calcEMA(bars, 5)
    // EMA[0] = SMA = 100; EMA[1] = 110*0.3333 + 100*0.6667 = 103.333…
    expect(out[0].value).toBe(100)
    approx(out[1].value, 103.3333)
  })

  it('pinned values for EMA period=4 across [10,11,12,13,14,15,16]', () => {
    // SMA(10..13)=11.5; k=2/5=0.4
    // EMA[0]=11.5; EMA[1]=14*0.4+11.5*0.6=12.5; EMA[2]=15*0.4+12.5*0.6=13.5;
    // EMA[3]=16*0.4+13.5*0.6=14.5
    const bars = closesToBars([10, 11, 12, 13, 14, 15, 16])
    const out = calcEMA(bars, 4)
    expect(out.map((p) => p.value)).toEqual([11.5, 12.5, 13.5, 14.5])
  })
})

// ── calcBB ────────────────────────────────────────────────────────────────────

describe('calcBB', () => {
  it('constant series has zero-width bands', () => {
    const bars = closesToBars([50, 50, 50, 50, 50])
    const bb = calcBB(bars, 3, 2)
    for (let i = 0; i < bb.middle.length; i++) {
      expect(bb.middle[i].value).toBe(50)
      expect(bb.upper[i].value).toBe(50)
      expect(bb.lower[i].value).toBe(50)
    }
  })

  it('hand-computed bands for simple series period=3 mult=1', () => {
    // Bars: [2, 4, 6]; mean=4, var=((2-4)²+(4-4)²+(6-4)²)/3=8/3, std≈1.6330
    const bb = calcBB(closesToBars([2, 4, 6]), 3, 1)
    expect(bb.middle).toHaveLength(1)
    expect(bb.middle[0].value).toBe(4)
    approx(bb.upper[0].value, 5.6330)
    approx(bb.lower[0].value, 2.3670)
  })

  it('returns empty bands when bars < period', () => {
    const bb = calcBB(closesToBars([1, 2]), 5, 2)
    expect(bb.upper).toEqual([])
    expect(bb.middle).toEqual([])
    expect(bb.lower).toEqual([])
  })

  it('returns empty for period <= 0', () => {
    expect(calcBB(closesToBars([1, 2, 3]), 0, 2).middle).toEqual([])
    expect(calcBB(closesToBars([1, 2, 3]), -5, 2).middle).toEqual([])
  })
})

// ── calcVWAP ──────────────────────────────────────────────────────────────────

describe('calcVWAP', () => {
  it('single-bar VWAP equals typical price', () => {
    const bars = makeOHLCVBars([{ h: 110, l: 90, c: 100, v: 1000 }])
    const out = calcVWAP(bars)
    expect(out[0].value).toBe(100) // tp = (110+90+100)/3 = 100
  })

  it('two-bar cumulative VWAP matches weighted mean', () => {
    const bars = makeOHLCVBars([
      { h: 10, l: 10, c: 10, v: 100 }, // tp=10, tp*v=1000
      { h: 20, l: 20, c: 20, v: 400 }, // tp=20, tp*v=8000; cum tp*v=9000, cum v=500
    ])
    const out = calcVWAP(bars)
    expect(out[0].value).toBe(10)
    expect(out[1].value).toBe(18) // 9000/500
  })

  it('falls back to close when cumulative volume is zero', () => {
    const bars = makeOHLCVBars([
      { h: 100, l: 100, c: 100, v: 0 },
      { h: 105, l: 95, c: 100, v: 0 },
    ])
    const out = calcVWAP(bars)
    expect(out[0].value).toBe(100)
    expect(out[1].value).toBe(100)
  })

  it('returns empty array for empty bars', () => {
    expect(calcVWAP([])).toEqual([])
  })
})

// ── calcRSI ───────────────────────────────────────────────────────────────────

describe('calcRSI', () => {
  it('all-gains series yields RSI 100', () => {
    const bars = closesToBars([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    const out = calcRSI(bars, 5)
    expect(out[out.length - 1].value).toBe(100)
  })

  it('flat series after seeding rsi is undefined territory — returns 100 when loss is zero', () => {
    // With any gains and zero losses, the formula returns 100 (guard clause).
    const bars = closesToBars([1, 2, 3, 4, 5, 5, 5, 5])
    const out = calcRSI(bars, 4)
    expect(out[0].value).toBe(100)
  })

  it('returns empty when bars.length < period + 1', () => {
    expect(calcRSI(closesToBars([1, 2, 3]), 14)).toEqual([])
  })

  it('symmetric mean-reverting series gives RSI ~50 at steady state', () => {
    const closes: number[] = []
    for (let i = 0; i < 100; i++) closes.push(100 + (i % 2 === 0 ? 0.5 : -0.5))
    const out = calcRSI(closesToBars(closes), 14)
    // Wilder smoothing of ±1 alternating diffs oscillates around 50
    // (each step nudges ±1 into one side of the accumulator then the other).
    const last = out[out.length - 1].value
    approx(last, 50, 5)
  })

  it('all-losses series yields RSI ~0', () => {
    const bars = closesToBars([10, 9, 8, 7, 6, 5, 4])
    const out = calcRSI(bars, 5)
    expect(out[out.length - 1].value).toBeLessThan(5)
  })

  it('pinned first RSI value for [44.34, 44.09, 44.15, 43.61, 44.33, 44.83] period=5', () => {
    // Classic Wilder example: diffs -0.25, 0.06, -0.54, 0.72, 0.50
    // avgGain = (0.06+0.72+0.50)/5 = 0.256; avgLoss = (0.25+0.54)/5 = 0.158
    // RS = 1.6202...; RSI = 100 - 100/(1+1.6202) ≈ 61.83
    const bars = closesToBars([44.34, 44.09, 44.15, 43.61, 44.33, 44.83])
    const out = calcRSI(bars, 5)
    expect(out).toHaveLength(1)
    approx(out[0].value, 61.83, 0.1)
  })

  it('first output time matches bars[period].time', () => {
    const bars = closesToBars([1, 2, 3, 4, 5, 6, 7])
    const out = calcRSI(bars, 5)
    expect(out[0].time).toBe(bars[5].time)
  })
})

// ── calcMACD ──────────────────────────────────────────────────────────────────

describe('calcMACD', () => {
  it('returns empty signal when macd < signal period', () => {
    // Need enough bars so MACD line exists but is shorter than sig
    const bars = closesToBars(Array.from({ length: 27 }, (_, i) => 100 + i))
    const out = calcMACD(bars, 12, 26, 9)
    // 27 bars → fast EMA starts at idx 11, slow at idx 25. MACD has 2 points < 9.
    expect(out.signal).toEqual([])
    expect(out.histogram).toEqual([])
    expect(out.macd.length).toBeGreaterThan(0)
  })

  it('histogram equals macd minus signal for each aligned time', () => {
    const bars = closesToBars(Array.from({ length: 60 }, (_, i) => 100 + Math.sin(i / 3) * 5))
    const out = calcMACD(bars, 12, 26, 9)
    const sigMap = new Map(out.signal.map((p) => [p.time, p.value]))
    for (const h of out.histogram) {
      const macd = out.macd.find((p) => p.time === h.time)!.value
      approx(h.value, macd - sigMap.get(h.time)!, 1e-3)
    }
  })

  it('uptrend pushes MACD above zero', () => {
    const bars = closesToBars(Array.from({ length: 60 }, (_, i) => 100 + i * 0.5))
    const out = calcMACD(bars, 12, 26, 9)
    expect(out.macd[out.macd.length - 1].value).toBeGreaterThan(0)
  })

  it('downtrend pushes MACD below zero', () => {
    const bars = closesToBars(Array.from({ length: 60 }, (_, i) => 200 - i * 0.5))
    const out = calcMACD(bars, 12, 26, 9)
    expect(out.macd[out.macd.length - 1].value).toBeLessThan(0)
  })

  it('constant series produces zero MACD and zero histogram', () => {
    const bars = closesToBars(new Array(40).fill(100))
    const out = calcMACD(bars, 12, 26, 9)
    for (const p of out.macd) approx(p.value, 0)
    for (const p of out.histogram) approx(p.value, 0)
  })

  it('first MACD point times match slow-EMA first point', () => {
    const bars = closesToBars(Array.from({ length: 40 }, (_, i) => 100 + i))
    const out = calcMACD(bars, 12, 26, 9)
    // Slow EMA starts at bar index 25. Fast EMA starts at 11. MACD = fast - slow,
    // filtered to overlapping times — so first MACD time == bars[25].time.
    expect(out.macd[0].time).toBe(bars[25].time)
  })
})

// ── intervalToSeconds ─────────────────────────────────────────────────────────

describe('intervalToSeconds', () => {
  it.each([
    ['1m', 60],
    ['5m', 300],
    ['1h', 3600],
    ['4h', 14_400],
    ['1d', 86_400],
    ['3d', 3 * 86_400],
    ['1wk', 7 * 86_400],
    ['1mo', 30 * 86_400],
  ])('parses %s as %i seconds', (interval, seconds) => {
    expect(intervalToSeconds(interval)).toBe(seconds)
  })

  it('falls back to 1 day for unknown suffix', () => {
    expect(intervalToSeconds('unknown')).toBe(86_400)
  })
})
