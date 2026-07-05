import { describe, expect, it } from 'vitest'
import { getQuotePollDelay } from '../useMarketData'

const FAST = 5_000
const SLOW = 30_000
const CLOSED = 5 * 60_000

function quote(market_state: 'open' | 'extended' | 'closed' | 'unknown') {
  return { market_state }
}

describe('getQuotePollDelay', () => {
  it('polls fast when the market-data WebSocket is disconnected', () => {
    expect(getQuotePollDelay({
      wsConnected: false,
      quotes: { AAPL: quote('closed') },
      symbols: ['AAPL'],
    })).toBe(FAST)
  })

  it('polls slowly when WebSocket is connected and equities are open or extended', () => {
    expect(getQuotePollDelay({
      wsConnected: true,
      quotes: { AAPL: quote('open'), MSFT: quote('extended') },
      symbols: ['AAPL', 'MSFT'],
    })).toBe(SLOW)
  })

  it('uses the closed-market cadence when all stock symbols are known closed', () => {
    expect(getQuotePollDelay({
      wsConnected: true,
      quotes: { AAPL: quote('closed'), MSFT: quote('closed') },
      symbols: ['AAPL', 'MSFT'],
    })).toBe(CLOSED)
  })

  it('ignores crypto symbols when deciding whether the equity market is closed', () => {
    expect(getQuotePollDelay({
      wsConnected: true,
      quotes: { AAPL: quote('closed'), 'BTC-USD': quote('open') },
      symbols: ['BTC-USD', 'AAPL'],
    })).toBe(CLOSED)
  })

  it('keeps the connected cadence when stock market state is missing or unknown', () => {
    expect(getQuotePollDelay({
      wsConnected: true,
      quotes: { AAPL: quote('unknown') },
      symbols: ['AAPL', 'MSFT'],
    })).toBe(SLOW)
  })
})
