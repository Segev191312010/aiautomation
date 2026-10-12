import {
  formatAge,
  formatCompact,
  formatNyTime,
  formatPercent,
  formatPrice,
  snapshotAgeTone,
  sortScreenerRows,
  tradingViewSymbol,
} from './format'

describe('format helpers', () => {
  it('maps IBKR symbols and venues to TradingView tickers', () => {
    expect(tradingViewSymbol('BRK B', 'NYSE')).toBe('NYSE:BRK.B')
    expect(tradingViewSymbol('nvda', 'NASDAQ')).toBe('NASDAQ:NVDA')
    expect(tradingViewSymbol('SPY', 'ARCA')).toBe('AMEX:SPY')
    expect(tradingViewSymbol('XYZ', 'PINK')).toBe('XYZ')
    expect(tradingViewSymbol('XYZ', null)).toBe('XYZ')
  })

  it('grades snapshot freshness at 120 s and 300 s', () => {
    expect(snapshotAgeTone(null)).toBe('missing')
    expect(snapshotAgeTone(120)).toBe('fresh')
    expect(snapshotAgeTone(121)).toBe('aging')
    expect(snapshotAgeTone(300)).toBe('aging')
    expect(snapshotAgeTone(301)).toBe('stale')
  })

  it('formats decimal strings and tolerates garbage', () => {
    expect(formatPercent('2.88')).toBe('+2.88%')
    expect(formatPercent('-0.44')).toBe('-0.44%')
    expect(formatPercent('abc')).toBe('—')
    expect(formatCompact('12345600')).toBe('12.3M')
    expect(formatPrice(null)).toBe('—')
    expect(formatPrice('1524.5')).toBe('1,524.50')
    expect(formatAge(65)).toBe('1m 05s')
    expect(formatNyTime('2026-10-12T14:05:09.000Z')).toBe('10:05:09')
    expect(formatNyTime('2026-10-12T14:05:09.123456+00:00')).toBe('10:05:09')
    expect(formatNyTime('not a date')).toBe('—')
  })

  it('sorts numerically with missing values last in both directions', () => {
    const rows = [
      { symbol: 'B', rvol: '1.5' },
      { symbol: 'A', rvol: null },
      { symbol: 'C', rvol: '3' },
    ]
    expect(sortScreenerRows(rows, 'rvol', 'desc').map((row) => row.symbol)).toEqual(['C', 'B', 'A'])
    expect(sortScreenerRows(rows, 'rvol', 'asc').map((row) => row.symbol)).toEqual(['B', 'C', 'A'])
  })
})
