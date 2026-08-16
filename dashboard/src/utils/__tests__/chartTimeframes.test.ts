import { describe, expect, it } from 'vitest'
import {
  CHART_TIMEFRAMES,
  chartBarTimeForTimestamp,
  chartBarsKey,
  createLatestRequestGate,
  getChartTimeframe,
  mergeRealtimeBar,
} from '@/utils/chartTimeframes'

describe('chart timeframe contract', () => {
  it.each([
    ['1m', '1 min', '1 D', 60],
    ['5m', '5 mins', '5 D', 300],
    ['15m', '15 mins', '5 D', 900],
    ['30m', '30 mins', '5 D', 1800],
    ['1h', '1 hour', '30 D', 3600],
    ['1d', '1 day', '1 Y', 86400],
    ['1wk', '1 week', '2 Y', 604800],
    ['1mo', '1 month', '5 Y', 2592000],
  ] as const)('maps %s consistently to broker history and bar width', (resolution, barSize, duration, seconds) => {
    expect(getChartTimeframe(resolution)).toMatchObject({
      interval: resolution,
      ibkrBarSize: barSize,
      ibkrDuration: duration,
      seconds,
    })
  })

  it('has one unique cache key per symbol and resolution', () => {
    const keys = CHART_TIMEFRAMES.map(({ interval }) => chartBarsKey('aapl', interval))
    expect(new Set(keys).size).toBe(CHART_TIMEFRAMES.length)
    expect(keys).toContain('AAPL:1m')
    expect(keys).toContain('AAPL:1d')
  })

  it('rejects stale request completions after a newer request starts', () => {
    const gate = createLatestRequestGate()
    const slowRequest = gate.issue()
    const fastRequest = gate.issue()

    expect(gate.isCurrent(slowRequest)).toBe(false)
    expect(gate.isCurrent(fastRequest)).toBe(true)
  })

  it('jumps directly to the correct anchored bucket after a market gap', () => {
    expect(chartBarTimeForTimestamp(100, 500, '1m')).toBe(460)
  })

  it('advances monthly buckets without overflowing short months', () => {
    const january31 = Date.UTC(2026, 0, 31, 5) / 1000
    const marchTick = Date.UTC(2026, 2, 15, 5) / 1000

    expect(chartBarTimeForTimestamp(january31, marchTick, '1mo')).toBe(
      Date.UTC(2026, 2, 1, 5) / 1000,
    )
  })

  it('uses broker open when a quote-created zero-volume bucket is replaced', () => {
    const merged = mergeRealtimeBar(
      [{ time: 100, open: 100, high: 103, low: 99, close: 102, volume: 0 }],
      { time: 110, open: 101, high: 104, low: 100, close: 103, volume: 7 },
      '1m',
    )

    expect(merged[0]).toEqual({
      time: 100,
      open: 101,
      high: 104,
      low: 99,
      close: 103,
      volume: 7,
    })
  })
})
