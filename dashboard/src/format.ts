/** Pure presentation helpers. Every formatter tolerates missing or malformed values. */
import type { DecimalValue, ScreenerRow } from './types'

export const NY_TIMEZONE = 'America/New_York'
export const SNAPSHOT_AGING_SECONDS = 120
export const SNAPSHOT_STALE_SECONDS = 300
const DASH = '—'

export function toNumber(value: DecimalValue | unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

const priceFormat = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})
const compactFormat = new Intl.NumberFormat('en-US', {
  notation: 'compact',
  maximumFractionDigits: 1,
})

export function formatPrice(value: DecimalValue): string {
  const amount = toNumber(value)
  return amount === null ? DASH : priceFormat.format(amount)
}

/** Values are already percentages ("2.88" means 2.88 %). */
export function formatPercent(value: DecimalValue): string {
  const amount = toNumber(value)
  if (amount === null) return DASH
  const sign = amount > 0 ? '+' : amount < 0 ? '-' : ''
  return `${sign}${Math.abs(amount).toFixed(2)}%`
}

export function formatRatio(value: DecimalValue): string {
  const amount = toNumber(value)
  return amount === null ? DASH : `${amount.toFixed(2)}×`
}

export function formatCompact(value: DecimalValue): string {
  const amount = toNumber(value)
  return amount === null ? DASH : compactFormat.format(amount)
}

const currencyFormat = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  signDisplay: 'exceptZero',
})

/** "+$1,790.44", "-$85.25", "$0.00". */
export function formatSignedCurrency(value: DecimalValue): string {
  const amount = toNumber(value)
  return amount === null ? DASH : currencyFormat.format(amount)
}

export function formatScore(value: DecimalValue): string {
  const amount = toNumber(value)
  return amount === null ? DASH : amount.toFixed(3)
}

export function directionClass(value: DecimalValue): 'up' | 'down' | 'flat' {
  const amount = toNumber(value)
  if (amount === null || amount === 0) return 'flat'
  return amount > 0 ? 'up' : 'down'
}

export function formatAge(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return DASH
  const whole = Math.max(0, Math.round(seconds))
  if (whole < 60) return `${whole}s`
  if (whole < 3600) return `${Math.floor(whole / 60)}m ${String(whole % 60).padStart(2, '0')}s`
  if (whole < 86400) {
    return `${Math.floor(whole / 3600)}h ${String(Math.floor((whole % 3600) / 60)).padStart(2, '0')}m`
  }
  return `${Math.floor(whole / 86400)}d ${Math.floor((whole % 86400) / 3600)}h`
}

export type AgeTone = 'fresh' | 'aging' | 'stale' | 'missing'

export function snapshotAgeTone(seconds: number | null): AgeTone {
  if (seconds === null || !Number.isFinite(seconds)) return 'missing'
  if (seconds > SNAPSHOT_STALE_SECONDS) return 'stale'
  if (seconds > SNAPSHOT_AGING_SECONDS) return 'aging'
  return 'fresh'
}

// ECMAScript only guarantees millisecond fractions; the screener writes microseconds.
const SUB_MILLISECOND_FRACTION = /(\.\d{3})\d+/

export function parseTime(value: string | null | undefined): Date | null {
  if (!value) return null
  const parsed = new Date(value.replace(SUB_MILLISECOND_FRACTION, '$1'))
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

const nyTimeFormat = new Intl.DateTimeFormat('en-US', {
  timeZone: NY_TIMEZONE,
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})
const nyClockFormat = new Intl.DateTimeFormat('en-US', {
  timeZone: NY_TIMEZONE,
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})
const nyDayClockFormat = new Intl.DateTimeFormat('en-US', {
  timeZone: NY_TIMEZONE,
  weekday: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})
const nyDateFormat = new Intl.DateTimeFormat('en-US', {
  timeZone: NY_TIMEZONE,
  weekday: 'short',
  month: 'short',
  day: 'numeric',
})

function formatWith(format: Intl.DateTimeFormat, value: Date | string | null | undefined): string {
  const date = value instanceof Date ? value : parseTime(value)
  return date === null || Number.isNaN(date.getTime()) ? DASH : format.format(date)
}

/** "10:05:12" in New York time. */
export const formatNyTime = (value: Date | string | null | undefined): string =>
  formatWith(nyTimeFormat, value)
/** "09:30" in New York time. */
export const formatNyClock = (value: Date | string | null | undefined): string =>
  formatWith(nyClockFormat, value)
/** "Mon 09:30" in New York time. */
export const formatNyDayClock = (value: Date | string | null | undefined): string =>
  formatWith(nyDayClockFormat, value)
/** "Mon, Oct 12" in New York time. */
export const formatNyDate = (value: Date | string | null | undefined): string =>
  formatWith(nyDateFormat, value)

// IBKR primary-exchange codes mapped to TradingView prefixes. NYSE Arca listings
// live under AMEX on TradingView; unknown venues fall back to the bare ticker.
const TRADINGVIEW_PREFIXES: Record<string, string> = {
  NASDAQ: 'NASDAQ',
  NYSE: 'NYSE',
  AMEX: 'AMEX',
  ARCA: 'AMEX',
  NYSEARCA: 'AMEX',
  BATS: 'BATS',
}

export function tradingViewSymbol(symbol: string, exchange: string | null | undefined): string {
  const ticker = symbol.trim().toUpperCase().replace(/\s+/g, '.')
  const prefix = exchange ? TRADINGVIEW_PREFIXES[exchange.trim().toUpperCase()] : undefined
  return prefix ? `${prefix}:${ticker}` : ticker
}

export type SortKey = 'score' | 'change_pct' | 'gap_pct' | 'rvol' | 'volume_shares' | 'pct_from_high'
export type SortDirection = 'asc' | 'desc'

/** Numeric sort with missing values always last and the symbol as a stable tiebreak. */
export function sortScreenerRows(
  rows: readonly ScreenerRow[],
  key: SortKey,
  direction: SortDirection,
): ScreenerRow[] {
  return [...rows].sort((a, b) => {
    const left = toNumber(a[key])
    const right = toNumber(b[key])
    const bySymbol = String(a.symbol ?? '').localeCompare(String(b.symbol ?? ''))
    if (left === null || right === null) {
      if (left === right) return bySymbol
      return left === null ? 1 : -1
    }
    const delta = direction === 'asc' ? left - right : right - left
    return delta !== 0 ? delta : bySymbol
  })
}

export type OutcomeTone = 'take' | 'ai' | 'risk' | 'rules' | 'data'

const OUTCOMES: Record<string, { label: string; tone: OutcomeTone }> = {
  shadow_accepted: { label: 'Would buy', tone: 'take' },
  approved: { label: 'Approved', tone: 'take' },
  submitted: { label: 'Submitted', tone: 'take' },
  ai_abstained: { label: 'Claude abstained', tone: 'ai' },
  ai_blocked: { label: 'AI unavailable', tone: 'ai' },
  risk_blocked: { label: 'Risk blocked', tone: 'risk' },
  rule_rejected: { label: 'Rules', tone: 'rules' },
  data_blocked: { label: 'No data', tone: 'data' },
}

export function describeOutcome(outcome: string): { label: string; tone: OutcomeTone } {
  return OUTCOMES[outcome] ?? { label: outcome.replace(/_/g, ' '), tone: 'data' }
}
