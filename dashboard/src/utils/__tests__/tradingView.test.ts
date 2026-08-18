import { describe, expect, it } from 'vitest'
import { buildTradingViewUrl, tradingViewInterval } from '../tradingView'

describe('TradingView chart URL contract', () => {
  it.each([
    ['1m', '1'], ['5m', '5'], ['15m', '15'], ['30m', '30'],
    ['1h', '60'], ['1d', 'D'], ['1wk', 'W'], ['1mo', 'M'],
  ])('maps %s to TradingView interval %s', (input, expected) => {
    expect(tradingViewInterval(input)).toBe(expected)
  })

  it('encodes symbols and keeps the widget origin trusted', () => {
    const url = new URL(buildTradingViewUrl('nasdaq:aapl', '1h'))
    expect(url.origin).toBe('https://www.tradingview.com')
    expect(url.searchParams.get('symbol')).toBe('NASDAQ:AAPL')
    expect(url.searchParams.get('interval')).toBe('60')
  })
})
