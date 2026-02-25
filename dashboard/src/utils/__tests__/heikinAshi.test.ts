import { describe, it, expect } from 'vitest'
import { toHeikinAshi } from '../heikinAshi'
import type { OHLCVBar } from '@/types'

describe('toHeikinAshi', () => {
  it('returns empty array for empty input', () => {
    expect(toHeikinAshi([])).toEqual([])
  })

  it('handles a single bar', () => {
    const bars: OHLCVBar[] = [
      { time: 1000, open: 100, high: 110, low: 90, close: 105, volume: 500 },
    ]
    const ha = toHeikinAshi(bars)
    expect(ha).toHaveLength(1)

    // HA Close = (100+110+90+105)/4 = 101.25
    expect(ha[0].close).toBeCloseTo(101.25, 4)
    // HA Open = (100+105)/2 = 102.5 (first bar)
    expect(ha[0].open).toBeCloseTo(102.5, 4)
    // HA High = max(110, 102.5, 101.25) = 110
    expect(ha[0].high).toBeCloseTo(110, 4)
    // HA Low = min(90, 102.5, 101.25) = 90
    expect(ha[0].low).toBeCloseTo(90, 4)
    // Time preserved
    expect(ha[0].time).toBe(1000)
  })

  it('preserves volume unchanged', () => {
    const bars: OHLCVBar[] = [
      { time: 1, open: 10, high: 15, low: 8, close: 12, volume: 999 },
      { time: 2, open: 12, high: 18, low: 10, close: 16, volume: 1234 },
    ]
    const ha = toHeikinAshi(bars)
    expect(ha[0].volume).toBe(999)
    expect(ha[1].volume).toBe(1234)
  })

  it('preserves time values', () => {
    const bars: OHLCVBar[] = [
      { time: 1700000000, open: 50, high: 55, low: 48, close: 52, volume: 100 },
      { time: 1700086400, open: 52, high: 58, low: 50, close: 56, volume: 200 },
    ]
    const ha = toHeikinAshi(bars)
    expect(ha[0].time).toBe(1700000000)
    expect(ha[1].time).toBe(1700086400)
  })

  it('calculates multi-bar Heikin-Ashi correctly', () => {
    const bars: OHLCVBar[] = [
      { time: 1, open: 100, high: 110, low: 90, close: 105, volume: 100 },
      { time: 2, open: 106, high: 115, low: 100, close: 112, volume: 200 },
      { time: 3, open: 113, high: 120, low: 108, close: 118, volume: 300 },
    ]
    const ha = toHeikinAshi(bars)
    expect(ha).toHaveLength(3)

    // Bar 0: HA_O = (100+105)/2=102.5, HA_C = (100+110+90+105)/4=101.25
    expect(ha[0].open).toBeCloseTo(102.5, 2)
    expect(ha[0].close).toBeCloseTo(101.25, 2)

    // Bar 1: HA_O = (102.5+101.25)/2=101.875, HA_C = (106+115+100+112)/4=108.25
    expect(ha[1].open).toBeCloseTo(101.875, 2)
    expect(ha[1].close).toBeCloseTo(108.25, 2)
    // HA_H = max(115, 101.875, 108.25) = 115
    expect(ha[1].high).toBeCloseTo(115, 2)
    // HA_L = min(100, 101.875, 108.25) = 100
    expect(ha[1].low).toBeCloseTo(100, 2)

    // Bar 2: HA_O = (101.875+108.25)/2=105.0625, HA_C = (113+120+108+118)/4=114.75
    expect(ha[2].open).toBeCloseTo(105.0625, 2)
    expect(ha[2].close).toBeCloseTo(114.75, 2)
  })

  it('HA High >= max(HA Open, HA Close) and HA Low <= min(HA Open, HA Close)', () => {
    const bars: OHLCVBar[] = [
      { time: 1, open: 50, high: 60, low: 40, close: 55, volume: 100 },
      { time: 2, open: 56, high: 65, low: 48, close: 60, volume: 200 },
      { time: 3, open: 61, high: 70, low: 55, close: 68, volume: 300 },
      { time: 4, open: 67, high: 72, low: 58, close: 62, volume: 400 },
    ]
    const ha = toHeikinAshi(bars)
    for (const bar of ha) {
      expect(bar.high).toBeGreaterThanOrEqual(Math.max(bar.open, bar.close))
      expect(bar.low).toBeLessThanOrEqual(Math.min(bar.open, bar.close))
    }
  })
})
