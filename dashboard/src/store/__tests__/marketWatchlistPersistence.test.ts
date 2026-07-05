import { beforeEach, describe, expect, it, vi } from 'vitest'

const STORAGE_KEY = 'market:watchlists:v1'

describe('useMarketStore watchlist persistence', () => {
  beforeEach(() => {
    vi.resetModules()
    localStorage.clear()
  })

  it('hydrates saved watchlists and active tab on a fresh store load', async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        watchlists: [
          { id: 'swing', name: 'Swing Setups', symbols: ['aapl', 'msft'] },
          { id: 'crypto', name: 'Crypto', symbols: ['btc-usd'] },
        ],
        activeWatchlist: 'swing',
      }),
    )

    const { useMarketStore } = await import('@/store/marketStore')
    const state = useMarketStore.getState()

    expect(state.activeWatchlist).toBe('swing')
    expect(state.watchlists).toEqual([
      { id: 'swing', name: 'Swing Setups', symbols: ['AAPL', 'MSFT'] },
      { id: 'crypto', name: 'Crypto', symbols: ['BTC-USD'] },
    ])
  })

  it('persists symbol edits through existing watchlist actions', async () => {
    const { MARKET_WATCHLIST_STORAGE_KEY, useMarketStore } = await import('@/store/marketStore')
    useMarketStore.setState({
      watchlists: [{ id: 'default', name: 'Watchlist', symbols: ['AAPL'] }],
      activeWatchlist: 'default',
    })

    useMarketStore.getState().addToWatchlist('default', 'msft')
    useMarketStore.getState().removeFromWatchlist('default', 'aapl')

    const saved = JSON.parse(localStorage.getItem(MARKET_WATCHLIST_STORAGE_KEY) ?? '{}')
    expect(saved).toEqual({
      watchlists: [{ id: 'default', name: 'Watchlist', symbols: ['MSFT'] }],
      activeWatchlist: 'default',
    })
  })

  it('persists active-list fallback when the active watchlist is removed', async () => {
    const { MARKET_WATCHLIST_STORAGE_KEY, useMarketStore } = await import('@/store/marketStore')
    useMarketStore.setState({
      watchlists: [
        { id: 'default', name: 'Watchlist', symbols: ['AAPL'] },
        { id: 'swing', name: 'Swing Setups', symbols: ['MSFT'] },
      ],
      activeWatchlist: 'swing',
    })

    useMarketStore.getState().removeWatchlist('swing')

    expect(useMarketStore.getState().activeWatchlist).toBe('default')
    const saved = JSON.parse(localStorage.getItem(MARKET_WATCHLIST_STORAGE_KEY) ?? '{}')
    expect(saved.activeWatchlist).toBe('default')
    expect(saved.watchlists).toEqual([{ id: 'default', name: 'Watchlist', symbols: ['AAPL'] }])
  })

  it('falls back to defaults when persisted data is corrupt', async () => {
    localStorage.setItem(STORAGE_KEY, '{not-json')

    const { useMarketStore } = await import('@/store/marketStore')
    const state = useMarketStore.getState()

    expect(state.activeWatchlist).toBe('default')
    expect(state.watchlists[0]).toMatchObject({ id: 'default', name: 'Watchlist' })
  })
})
