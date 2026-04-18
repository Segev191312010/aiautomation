/**
 * Phase E2 — auth-token revocation flow for the API transport layer.
 *
 * Asserts: on a 401 response, the stored auth token is cleared from
 * localStorage AND the 'api:unauthorized' event is dispatched exactly once.
 * On 2xx, nothing is cleared and no event fires. Also verifies the token
 * is sent as a Bearer header, and the auth token endpoint itself skips
 * the bootstrap gate.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const TOKEN_KEY = 'auth_token'

describe('api client — token revocation + bearer handling', () => {
  beforeEach(() => {
    vi.resetModules()
    localStorage.clear()
    vi.restoreAllMocks()
  })

  afterEach(() => {
    localStorage.clear()
  })

  it('clears token from localStorage on 401 response', async () => {
    localStorage.setItem(TOKEN_KEY, 'stale-jwt')
    const fetchMock = vi.fn(async () => new Response('Unauthorized', { status: 401 }))
    vi.stubGlobal('fetch', fetchMock)

    const { get } = await import('@/services/api/client')

    await expect(get('/api/positions')).rejects.toThrow(/401/)
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull()
  })

  it('dispatches api:unauthorized event on 401', async () => {
    localStorage.setItem(TOKEN_KEY, 'stale-jwt')
    vi.stubGlobal('fetch', vi.fn(async () => new Response('Unauthorized', { status: 401 })))

    const { get } = await import('@/services/api/client')
    const listener = vi.fn()
    window.addEventListener('api:unauthorized', listener)

    await expect(get('/api/positions')).rejects.toThrow(/401/)
    expect(listener).toHaveBeenCalledTimes(1)
    window.removeEventListener('api:unauthorized', listener)
  })

  it('does NOT clear token on 2xx response', async () => {
    localStorage.setItem(TOKEN_KEY, 'valid-jwt')
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })),
    )

    const { get } = await import('@/services/api/client')
    const listener = vi.fn()
    window.addEventListener('api:unauthorized', listener)

    const result = await get<{ ok: boolean }>('/api/positions')
    expect(result).toEqual({ ok: true })
    expect(localStorage.getItem(TOKEN_KEY)).toBe('valid-jwt')
    expect(listener).not.toHaveBeenCalled()
    window.removeEventListener('api:unauthorized', listener)
  })

  it('does NOT clear token on other non-200 statuses (e.g., 500)', async () => {
    localStorage.setItem(TOKEN_KEY, 'valid-jwt')
    vi.stubGlobal('fetch', vi.fn(async () => new Response('Internal error', { status: 500 })))

    const { get } = await import('@/services/api/client')
    const listener = vi.fn()
    window.addEventListener('api:unauthorized', listener)

    await expect(get('/api/positions')).rejects.toThrow(/500/)
    expect(localStorage.getItem(TOKEN_KEY)).toBe('valid-jwt')
    expect(listener).not.toHaveBeenCalled()
    window.removeEventListener('api:unauthorized', listener)
  })

  it('sends Bearer token on authenticated requests', async () => {
    localStorage.setItem(TOKEN_KEY, 'the-token')
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const { get } = await import('@/services/api/client')
    await get('/api/positions')

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const headers = init.headers as Record<string, string>
    expect(headers['Authorization']).toBe('Bearer the-token')
  })

  it('skips the bootstrap gate and omits Authorization for /api/auth/token', async () => {
    // No token in storage; without the special-case, the request would hang
    // on _waitForToken for 5s. We only wait a short window and assert it
    // resolves immediately.
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      new Response(JSON.stringify({ access_token: 'new' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const { post } = await import('@/services/api/client')
    const result = await Promise.race([
      post<{ access_token: string }>('/api/auth/token'),
      new Promise<null>((resolve) => setTimeout(() => resolve(null), 500)),
    ])
    expect(result).toEqual({ access_token: 'new' })

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const headers = (init.headers ?? {}) as Record<string, string>
    expect(headers['Authorization']).toBeUndefined()
  })

  it('does NOT resend a stale bearer token after a 401 has cleared it', async () => {
    // Seed a token, make a successful request (resolves _bootstrapPromise
    // under the hood for any future awaits), trigger a 401 that clears
    // storage, then make another request and confirm no Authorization header.
    localStorage.setItem(TOKEN_KEY, 'stale-jwt')

    let nthCall = 0
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => {
      nthCall += 1
      if (nthCall === 1) return new Response('Unauthorized', { status: 401 })
      return new Response('{}', { status: 200 })
    })
    vi.stubGlobal('fetch', fetchMock)

    const { get } = await import('@/services/api/client')

    await expect(get('/api/positions')).rejects.toThrow(/401/)
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull()

    // Second request — token is gone. Race a short timeout so the test does
    // not stall on the 5s bootstrap fallback.
    const second = Promise.race([
      get<unknown>('/api/positions').catch(() => 'errored'),
      new Promise<string>((resolve) => setTimeout(() => resolve('timeout'), 500)),
    ])
    // The key assertion: whichever branch wins, the second fetch call (if it
    // happened) must NOT carry an Authorization header with the stale token.
    await second
    if (fetchMock.mock.calls.length >= 2) {
      const [, init] = fetchMock.mock.calls[1] as [string, RequestInit]
      const headers = (init.headers ?? {}) as Record<string, string>
      expect(headers['Authorization']).toBeUndefined()
    }
  })

  it('setAuthToken persists to localStorage; passing null removes it', async () => {
    const { setAuthToken, getAuthToken } = await import('@/services/api/client')

    setAuthToken('abc')
    expect(getAuthToken()).toBe('abc')
    expect(localStorage.getItem(TOKEN_KEY)).toBe('abc')

    setAuthToken(null)
    expect(getAuthToken()).toBeNull()
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull()
  })
})
