import { describe, expect, it } from 'vitest'
import { buildIbMultiChartUrl, buildTradingViewUrl, tradingViewInterval } from '../tradingView'

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

  it('encodes sidecar symbols and caps the legacy grid at nine entries', () => {
    const url = new URL(buildIbMultiChartUrl(['aapl', ' msft ', 'A&B', 'TSLA', 'NVDA', 'META', 'AMD', 'INTC', 'ORCL', 'EXTRA'], 'D'))
    expect(url.origin).toBe('http://127.0.0.1:5001')
    expect(url.pathname).toBe('/ib_multichart.html')
    expect(url.searchParams.get('symbols')).toBe('AAPL,MSFT,A&B,TSLA,NVDA,META,AMD,INTC,ORCL')
    expect(url.searchParams.get('tf')).toBe('D')
  })

  it('rejects a non-http sidecar origin', () => {
    const previous = import.meta.env.VITE_IB_CHART_BASE
    import.meta.env.VITE_IB_CHART_BASE = 'javascript:alert(1)'
    expect(() => buildIbMultiChartUrl(['AAPL'], 'D')).toThrow(/http or https/)
    import.meta.env.VITE_IB_CHART_BASE = previous
  })
})
