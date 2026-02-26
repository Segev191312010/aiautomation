import { describe, it, expect } from 'vitest'
import {
  pointToLineDistance,
  pointToInfiniteLineDistance,
  hitTestHorizontalLine,
  hitTestTrendline,
  calcFibLevels,
  snapToCandle,
  measureTrendline,
  FIB_LEVELS,
  HIT_TOLERANCE,
} from '../drawingEngine'
import type { OHLCVBar } from '@/types'

// ── pointToLineDistance ──────────────────────────────────────────────────────

describe('pointToLineDistance', () => {
  it('returns 0 for a point on the segment', () => {
    expect(pointToLineDistance(5, 5, 0, 0, 10, 10)).toBeCloseTo(0, 4)
  })

  it('returns perpendicular distance', () => {
    // Point (5, 1) to segment (0,0)-(10,0) → distance = 1
    expect(pointToLineDistance(5, 1, 0, 0, 10, 0)).toBeCloseTo(1, 4)
  })

  it('clamps to segment start endpoint', () => {
    // Point (-5, 0) to segment (0,0)-(10,0) → distance = 5
    expect(pointToLineDistance(-5, 0, 0, 0, 10, 0)).toBeCloseTo(5, 4)
  })

  it('clamps to segment end endpoint', () => {
    // Point (15, 0) to segment (0,0)-(10,0) → distance = 5
    expect(pointToLineDistance(15, 0, 0, 0, 10, 0)).toBeCloseTo(5, 4)
  })

  it('handles degenerate segment (p1 === p2)', () => {
    // Point (3, 4) to degenerate segment at origin → distance = 5
    expect(pointToLineDistance(3, 4, 0, 0, 0, 0)).toBeCloseTo(5, 4)
  })
})

// ── pointToInfiniteLineDistance ──────────────────────────────────────────────

describe('pointToInfiniteLineDistance', () => {
  it('does not clamp to endpoints', () => {
    // Point (15, 0) to line through (0,0)-(10,0) → distance = 0 (on the infinite line)
    expect(pointToInfiniteLineDistance(15, 0, 0, 0, 10, 0)).toBeCloseTo(0, 4)
  })

  it('returns perpendicular distance', () => {
    expect(pointToInfiniteLineDistance(5, 3, 0, 0, 10, 0)).toBeCloseTo(3, 4)
  })
})

// ── hitTestHorizontalLine ───────────────────────────────────────────────────

describe('hitTestHorizontalLine', () => {
  it('returns hit for mouse within tolerance', () => {
    const res = hitTestHorizontalLine(100, 103, 100, 800)
    expect(res.hit).toBe(true)
    expect(res.part).toBe('line')
  })

  it('returns no hit outside tolerance', () => {
    const res = hitTestHorizontalLine(100, 120, 100, 800)
    expect(res.hit).toBe(false)
  })

  it('detects label hit on right edge', () => {
    const res = hitTestHorizontalLine(750, 100, 100, 800)
    expect(res.hit).toBe(true)
    expect(res.part).toBe('label')
  })

  it('detects handle hit when handle position given', () => {
    const res = hitTestHorizontalLine(200, 100, 100, 800, 200)
    expect(res.hit).toBe(true)
    expect(res.part).toBe('handle')
  })
})

// ── hitTestTrendline ────────────────────────────────────────────────────────

describe('hitTestTrendline', () => {
  it('returns hit for point near segment midpoint', () => {
    // Point (50, 51) near midpoint of segment (0,0)-(100,100) — far from handles
    const res = hitTestTrendline(50, 51, 0, 0, 100, 100)
    expect(res.hit).toBe(true)
    expect(res.part).toBe('line')
  })

  it('returns no hit for point far from segment', () => {
    const res = hitTestTrendline(5, 50, 0, 0, 10, 10)
    expect(res.hit).toBe(false)
  })

  it('detects handle 0 hit', () => {
    const res = hitTestTrendline(1, 1, 0, 0, 100, 100)
    expect(res.hit).toBe(true)
    expect(res.part).toBe('handle')
    expect(res.handleIndex).toBe(0)
  })

  it('detects handle 1 hit', () => {
    const res = hitTestTrendline(99, 99, 0, 0, 100, 100)
    expect(res.hit).toBe(true)
    expect(res.part).toBe('handle')
    expect(res.handleIndex).toBe(1)
  })
})

// ── calcFibLevels ───────────────────────────────────────────────────────────

describe('calcFibLevels', () => {
  it('returns correct number of levels', () => {
    const levels = calcFibLevels(200, 100)
    expect(levels).toHaveLength(FIB_LEVELS.length)
  })

  it('level 0 = high, level 1 = low', () => {
    const levels = calcFibLevels(200, 100)
    expect(levels[0].price).toBe(200)
    expect(levels[0].label).toBe('0%')
    expect(levels[levels.length - 1].price).toBe(100)
    expect(levels[levels.length - 1].label).toBe('100%')
  })

  it('50% level is midpoint', () => {
    const levels = calcFibLevels(200, 100)
    const mid = levels.find((l) => l.level === 0.5)
    expect(mid?.price).toBe(150)
  })

  it('marks golden zone correctly', () => {
    const levels = calcFibLevels(200, 100)
    const golden = levels.filter((l) => l.isGoldenZone)
    expect(golden).toHaveLength(3) // 0.382, 0.5, 0.618
    expect(golden.map((g) => g.level)).toEqual([0.382, 0.5, 0.618])
  })

  it('handles high < low (inverted) — still returns correct prices', () => {
    // When high and low are inverted, calcFibLevels takes abs
    const levels = calcFibLevels(100, 200)
    // high=100, low=200 → spread is -100
    // level 0: 100 - (100-200)*0 = 100
    // level 1: 100 - (100-200)*1 = 200
    expect(levels[0].price).toBe(100)
    expect(levels[levels.length - 1].price).toBe(200)
  })
})

// ── snapToCandle ────────────────────────────────────────────────────────────

describe('snapToCandle', () => {
  const candles: OHLCVBar[] = [
    { time: 1000, open: 10, high: 15, low: 8, close: 12, volume: 100 },
    { time: 2000, open: 12, high: 18, low: 11, close: 16, volume: 200 },
    { time: 3000, open: 16, high: 20, low: 14, close: 19, volume: 150 },
  ]

  it('snaps to nearest candle open', () => {
    const result = snapToCandle(9.5, 1000, candles)
    expect(result.price).toBe(10)
    expect(result.time).toBe(1000)
  })

  it('snaps to nearest candle high', () => {
    const result = snapToCandle(14.5, 1000, candles)
    expect(result.price).toBe(15)
  })

  it('snaps to nearest candle by time', () => {
    const result = snapToCandle(17, 2500, candles)
    // Nearest candle is at time 3000 (diff=500 vs diff=500 for time 2000, picks first found)
    // But 2500 is equidistant — picks the one found first (index 1, time=2000)
    expect(result.time).toBe(2000)
    expect(result.price).toBe(18) // closest to 17 among [12,18,11,16]
  })

  it('returns original when no candles', () => {
    const result = snapToCandle(42, 5000, [])
    expect(result.price).toBe(42)
    expect(result.time).toBe(5000)
  })

  it('tie-breaks deterministically (first match wins)', () => {
    // Price 13 is equidistant from 12 and 14 in candle at time 2000
    // Open=12 (diff=1), close=16 (diff=3), high=18 (diff=5), low=11 (diff=2)
    // Closest is open=12
    const result = snapToCandle(13, 2000, candles)
    expect(result.price).toBe(12)
  })
})

// ── measureTrendline ────────────────────────────────────────────────────────

describe('measureTrendline', () => {
  it('computes price delta and percentage', () => {
    const m = measureTrendline(
      { time: 1000, price: 100 },
      { time: 2000, price: 110 },
      1000,
    )
    expect(m.priceDelta).toBe(10)
    expect(m.priceDeltaPct).toBeCloseTo(10, 1)
    expect(m.barCount).toBe(1)
  })

  it('handles zero starting price', () => {
    const m = measureTrendline(
      { time: 0, price: 0 },
      { time: 1000, price: 50 },
      1000,
    )
    expect(m.priceDeltaPct).toBe(0)
  })

  it('counts bars correctly', () => {
    const m = measureTrendline(
      { time: 0, price: 100 },
      { time: 5000, price: 200 },
      1000,
    )
    expect(m.barCount).toBe(5)
  })
})
