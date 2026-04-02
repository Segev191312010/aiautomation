/**
 * Unit tests for src/utils/formatters.ts
 *
 * All functions are pure (except fmtDate / fmtTimestamp which call new Date()
 * internally).  Those two are tested by constructing timestamps that are
 * guaranteed to be "today" or "yesterday" relative to the current wall-clock
 * time, so no fake-timer setup is required.
 */

import { describe, it, expect } from 'vitest'
import {
  fmtUSD,
  fmtUSDCompact,
  fmtPct,
  fmtPrice,
  fmtDate,
  fmtTimestamp,
  pctColor,
  heatmapCellColor,
} from '../formatters'

// ── fmtUSD ────────────────────────────────────────────────────────────────────

describe('fmtUSD', () => {
  it('formats a positive value with $ sign and two decimal places', () => {
    expect(fmtUSD(1234.5)).toBe('$1,234.50')
  })

  it('formats a negative value', () => {
    expect(fmtUSD(-99.9)).toBe('-$99.90')
  })

  it('formats zero', () => {
    expect(fmtUSD(0)).toBe('$0.00')
  })

  it('formats a large value with comma separators', () => {
    expect(fmtUSD(1_000_000)).toBe('$1,000,000.00')
  })

  it('rounds to two decimal places', () => {
    expect(fmtUSD(1.005)).toBe('$1.01')
  })
})

// ── fmtUSDCompact ─────────────────────────────────────────────────────────────

describe('fmtUSDCompact', () => {
  it('formats values >= 1 000 000 with M suffix', () => {
    expect(fmtUSDCompact(2_500_000)).toBe('$2.50M')
  })

  it('formats negative values >= 1 000 000 with M suffix and leading minus', () => {
    expect(fmtUSDCompact(-3_000_000)).toBe('-$3.00M')
  })

  it('formats values >= 1 000 with K suffix', () => {
    expect(fmtUSDCompact(12_345)).toBe('$12.3K')
  })

  it('formats negative values >= 1 000 with K suffix and leading minus', () => {
    expect(fmtUSDCompact(-5_500)).toBe('-$5.5K')
  })

  it('falls through to fmtUSD for small positive values', () => {
    expect(fmtUSDCompact(42.5)).toBe('$42.50')
  })

  it('falls through to fmtUSD for small negative values', () => {
    expect(fmtUSDCompact(-7.25)).toBe('-$7.25')
  })

  it('falls through to fmtUSD for zero', () => {
    expect(fmtUSDCompact(0)).toBe('$0.00')
  })

  it('formats exactly 1 000 000 as M not K', () => {
    expect(fmtUSDCompact(1_000_000)).toBe('$1.00M')
  })

  it('formats exactly 1 000 as K not plain USD', () => {
    expect(fmtUSDCompact(1_000)).toBe('$1.0K')
  })
})

// ── fmtPct ────────────────────────────────────────────────────────────────────

describe('fmtPct', () => {
  it('prefixes positive values with +', () => {
    expect(fmtPct(3.14)).toBe('+3.14%')
  })

  it('formats negative values without extra minus', () => {
    expect(fmtPct(-2.5)).toBe('-2.50%')
  })

  it('prefixes zero with +', () => {
    expect(fmtPct(0)).toBe('+0.00%')
  })

  it('returns -- for null', () => {
    expect(fmtPct(null)).toBe('--')
  })

  it('returns -- for undefined', () => {
    expect(fmtPct(undefined)).toBe('--')
  })

  it('respects custom decimal places for positive value', () => {
    expect(fmtPct(1.23456, 1)).toBe('+1.2%')
  })

  it('respects custom decimal places for negative value', () => {
    // (-1.7).toFixed(0) === "-2" in all JS engines
    expect(fmtPct(-1.7, 0)).toBe('-2%')
  })

  it('defaults to 2 decimal places', () => {
    expect(fmtPct(5)).toBe('+5.00%')
  })
})

// ── fmtPrice ──────────────────────────────────────────────────────────────────

describe('fmtPrice', () => {
  it('formats a positive price with $ prefix and two decimals', () => {
    expect(fmtPrice(123.4)).toBe('$123.40')
  })

  it('returns -- for null', () => {
    expect(fmtPrice(null)).toBe('--')
  })

  it('returns -- for undefined', () => {
    expect(fmtPrice(undefined)).toBe('--')
  })

  it('formats zero', () => {
    expect(fmtPrice(0)).toBe('$0.00')
  })
})

// ── fmtDate ───────────────────────────────────────────────────────────────────

describe('fmtDate', () => {
  /**
   * Build an ISO string for today at a known time without relying on fake
   * timers.  We take the current local midnight and add hours/minutes.
   */
  function todayAt(hour: number, minute: number): string {
    const d = new Date()
    d.setHours(hour, minute, 0, 0)
    return d.toISOString()
  }

  /**
   * Build an ISO string for a date that is definitely not today.
   * We use 2020-06-15 which is well in the past.
   */
  const PAST_DATE_ISO = '2020-06-15T09:30:00'

  it('returns HH:MM for a timestamp that is today', () => {
    const result = fmtDate(todayAt(14, 5))
    // Expect something like "14:05"
    expect(result).toMatch(/^\d{2}:\d{2}$/)
  })

  it('returns "Mon DD" format for a past date', () => {
    const result = fmtDate(PAST_DATE_ISO)
    // Locale short month + day, e.g. "Jun 15"
    expect(result).toMatch(/^[A-Z][a-z]{2} \d{1,2}$/)
    expect(result).toContain('15')
  })

  it('past-date result does not contain seconds', () => {
    expect(fmtDate(PAST_DATE_ISO)).not.toMatch(/:\d{2}:\d{2}/)
  })
})

// ── fmtTimestamp ──────────────────────────────────────────────────────────────

describe('fmtTimestamp', () => {
  function todayAt(hour: number, minute: number, second = 0): string {
    const d = new Date()
    d.setHours(hour, minute, second, 0)
    return d.toISOString()
  }

  const PAST_DATE_ISO = '2020-06-15T09:30:00'

  it('returns HH:MM:SS for a timestamp that is today', () => {
    const result = fmtTimestamp(todayAt(9, 45, 30))
    expect(result).toMatch(/^\d{2}:\d{2}:\d{2}$/)
  })

  it('returns "Mon DD HH:MM" for a past date', () => {
    const result = fmtTimestamp(PAST_DATE_ISO)
    // e.g. "Jun 15 09:30"
    expect(result).toMatch(/^[A-Z][a-z]{2} \d{1,2} \d{2}:\d{2}$/)
    expect(result).toContain('15')
  })

  it('past-date result contains time portion after the date', () => {
    const result = fmtTimestamp(PAST_DATE_ISO)
    const parts = result.split(' ')
    // Should be ["Jun", "15", "HH:MM"]
    expect(parts).toHaveLength(3)
    expect(parts[2]).toMatch(/^\d{2}:\d{2}$/)
  })
})

// ── pctColor ──────────────────────────────────────────────────────────────────

describe('pctColor', () => {
  it('returns text-emerald-400 for positive values', () => {
    expect(pctColor(5.3)).toBe('text-emerald-400')
  })

  it('returns text-emerald-400 for zero', () => {
    expect(pctColor(0)).toBe('text-emerald-400')
  })

  it('returns text-red-400 for negative values', () => {
    expect(pctColor(-0.01)).toBe('text-red-400')
  })

  it('returns text-red-400 for large negative values', () => {
    expect(pctColor(-100)).toBe('text-red-400')
  })
})

// ── heatmapCellColor ──────────────────────────────────────────────────────────

describe('heatmapCellColor', () => {
  it('returns strong-green band for v >= 10', () => {
    expect(heatmapCellColor(10)).toBe('bg-emerald-500/30 text-emerald-300')
    expect(heatmapCellColor(25)).toBe('bg-emerald-500/30 text-emerald-300')
  })

  it('returns mid-green band for 5 <= v < 10', () => {
    expect(heatmapCellColor(5)).toBe('bg-emerald-500/20 text-emerald-300')
    expect(heatmapCellColor(7.5)).toBe('bg-emerald-500/20 text-emerald-300')
  })

  it('returns light-green band for 2 <= v < 5', () => {
    expect(heatmapCellColor(2)).toBe('bg-emerald-500/10 text-emerald-400')
    expect(heatmapCellColor(3)).toBe('bg-emerald-500/10 text-emerald-400')
  })

  it('returns faint-green band for 0 < v < 2', () => {
    expect(heatmapCellColor(0.1)).toBe('bg-emerald-500/5 text-emerald-400')
    expect(heatmapCellColor(1.99)).toBe('bg-emerald-500/5 text-emerald-400')
  })

  it('returns faint-red band for -2 <= v <= 0', () => {
    expect(heatmapCellColor(0)).toBe('bg-red-500/5 text-red-400')
    expect(heatmapCellColor(-1)).toBe('bg-red-500/5 text-red-400')
    expect(heatmapCellColor(-2)).toBe('bg-red-500/5 text-red-400')
  })

  it('returns light-red band for -5 <= v < -2', () => {
    expect(heatmapCellColor(-3)).toBe('bg-red-500/10 text-red-400')
    expect(heatmapCellColor(-5)).toBe('bg-red-500/10 text-red-400')
  })

  it('returns mid-red band for -10 <= v < -5', () => {
    expect(heatmapCellColor(-6)).toBe('bg-red-500/20 text-red-300')
    expect(heatmapCellColor(-10)).toBe('bg-red-500/20 text-red-300')
  })

  it('returns strong-red band for v < -10', () => {
    expect(heatmapCellColor(-11)).toBe('bg-red-500/30 text-red-300')
    expect(heatmapCellColor(-50)).toBe('bg-red-500/30 text-red-300')
  })
})
