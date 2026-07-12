import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useSessionStore } from '@/store/sessionStore'
import { get, getAuthToken, post } from '@/services/api/client'


function establishSession(token = 'valid-jwt') {
  useSessionStore.getState().setSession({
    accessToken: token,
    expiresAt: new Date(Date.now() + 60_000).toISOString(),
  })
}


describe('api client in-memory session handling', () => {
  beforeEach(() => {
    useSessionStore.setState(useSessionStore.getInitialState(), true)
    localStorage.clear()
    window.history.replaceState({}, '', '/')
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it.each([401, 403])('resets the session and routes away on %s', async (status) => {
    establishSession('stale-jwt')
    const listener = vi.fn()
    window.addEventListener('api:unauthorized', listener)
    vi.stubGlobal('fetch', vi.fn(async () => new Response('Rejected', { status })))

    await expect(get('/api/positions')).rejects.toThrow(new RegExp(String(status)))

    expect(useSessionStore.getState()).toMatchObject({
      token: null,
      expiresAt: null,
      status: 'expired',
    })
    expect(window.location.pathname).toBe('/session-expired')
    expect(listener).toHaveBeenCalledTimes(1)
    window.removeEventListener('api:unauthorized', listener)
  })

  it('attaches the current bearer token without writing browser storage', async () => {
    establishSession('memory-token')
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await get('/api/positions')

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    const headers = init.headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer memory-token')
    expect(localStorage.getItem('auth_token')).toBeNull()
  })

  it('fails closed before fetching a protected route without a session', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    await expect(post('/api/orders/manual', { symbol: 'AAPL' })).rejects.toThrow(
      /blocked: no active session/,
    )

    expect(fetchMock).not.toHaveBeenCalled()
    expect(useSessionStore.getState().status).toBe('expired')
  })

  it('allows the public bootstrap endpoint without a bearer token', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await post('/api/session/bootstrap', {})

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    const headers = init.headers as Record<string, string>
    expect(headers.Authorization).toBeUndefined()
  })

  it('does not reset a valid session for a non-auth server error', async () => {
    establishSession()
    vi.stubGlobal('fetch', vi.fn(async () => new Response('Internal error', { status: 500 })))

    await expect(get('/api/positions')).rejects.toThrow(/500/)

    expect(useSessionStore.getState().status).toBe('authenticated')
    expect(useSessionStore.getState().token).toBe('valid-jwt')
  })

  it.each([200, 401])('discards a stale %s response without clearing a replacement session', async (status) => {
    establishSession('first-token')
    let releaseResponse: ((response: Response) => void) | undefined
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => {
      releaseResponse = resolve
    })))

    const pending = get<{ stale: boolean }>('/api/positions')
    establishSession('replacement-token')
    releaseResponse?.(new Response(JSON.stringify({ stale: true }), { status }))

    await expect(pending).rejects.toThrow(/session changed during request/)
    expect(useSessionStore.getState()).toMatchObject({
      token: 'replacement-token',
      status: 'authenticated',
    })
  })

  it('discards a success whose body finishes after the session changes', async () => {
    establishSession('first-token')
    let releaseBody: ((body: string) => void) | undefined
    const response = {
      ok: true,
      status: 200,
      text: () => new Promise<string>((resolve) => { releaseBody = resolve }),
    } as Response
    vi.stubGlobal('fetch', vi.fn(async () => response))

    const pending = get<{ stale: boolean }>('/api/positions')
    await Promise.resolve()
    establishSession('replacement-token')
    releaseBody?.('{"stale":true}')

    await expect(pending).rejects.toThrow(/session changed while reading response/)
    expect(useSessionStore.getState()).toMatchObject({
      token: 'replacement-token',
      status: 'authenticated',
    })
  })

  it('does not expose an expired token to WebSocket transports', () => {
    useSessionStore.setState({
      token: 'expired-ws-token',
      expiresAt: new Date(Date.now() - 1).toISOString(),
      status: 'authenticated',
      error: null,
    })

    expect(getAuthToken()).toBeNull()
    expect(useSessionStore.getState().status).toBe('expired')
    expect(window.location.pathname).toBe('/session-expired')
  })
})
