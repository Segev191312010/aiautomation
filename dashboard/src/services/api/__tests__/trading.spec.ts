import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useSessionStore } from '@/store/sessionStore'

describe('trading API order lifecycle', () => {
  beforeEach(() => {
    useSessionStore.setState(useSessionStore.getInitialState(), true)
    useSessionStore.getState().setSession({
      accessToken: 'test-token',
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useSessionStore.setState(useSessionStore.getInitialState(), true)
  })

  it('places a manual order and cancels it through the expected endpoints', async () => {
    const calls: Array<{ url: string; method?: string; body?: string; authorization?: string }> = []
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const headers = (init?.headers ?? {}) as Record<string, string>
      calls.push({
        url,
        method: init?.method,
        body: init?.body as string | undefined,
        authorization: headers.Authorization,
      })

      const payload = url.endsWith('/api/orders/manual')
        ? { success: true, message: 'Order accepted' }
        : { cancelled: true }

      return new Response(JSON.stringify(payload), { status: 200 })
    })
    vi.stubGlobal('fetch', fetchMock)

    const { cancelOrder, placeManualOrder } = await import('@/services/api/trading')

    await expect(placeManualOrder({ symbol: 'AAPL', action: 'BUY', quantity: 3 })).resolves.toEqual({
      success: true,
      message: 'Order accepted',
    })
    await expect(cancelOrder(42)).resolves.toEqual({ cancelled: true })

    expect(calls).toHaveLength(2)
    expect(calls[0]).toMatchObject({
      url: '/api/orders/manual',
      method: 'POST',
      authorization: 'Bearer test-token',
    })
    expect(JSON.parse(calls[0].body ?? '{}')).toEqual({
      symbol: 'AAPL',
      action: 'BUY',
      quantity: 3,
    })
    expect(calls[1]).toMatchObject({
      url: '/api/orders/42',
      method: 'DELETE',
      authorization: 'Bearer test-token',
    })
  })
})
