/**
 * Heikin-Ashi bar conversion — pure function, no side effects.
 *
 * HA Close = (O + H + L + C) / 4
 * HA Open  = (prev HA Open + prev HA Close) / 2  (first bar: (O + C) / 2)
 * HA High  = max(H, HA Open, HA Close)
 * HA Low   = min(L, HA Open, HA Close)
 * Volume passes through unchanged.
 */
import type { OHLCVBar } from '@/types'

export function toHeikinAshi(bars: OHLCVBar[]): OHLCVBar[] {
  if (!bars.length) return []

  const result: OHLCVBar[] = []

  for (let i = 0; i < bars.length; i++) {
    const b = bars[i]
    const haClose = (b.open + b.high + b.low + b.close) / 4
    const haOpen =
      i === 0
        ? (b.open + b.close) / 2
        : (result[i - 1].open + result[i - 1].close) / 2
    const haHigh = Math.max(b.high, haOpen, haClose)
    const haLow  = Math.min(b.low, haOpen, haClose)

    result.push({
      time:   b.time,
      open:   +haOpen.toFixed(4),
      high:   +haHigh.toFixed(4),
      low:    +haLow.toFixed(4),
      close:  +haClose.toFixed(4),
      volume: b.volume,
    })
  }

  return result
}
