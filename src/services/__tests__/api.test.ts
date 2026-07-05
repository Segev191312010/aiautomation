import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setAuthToken, getAuthToken, fetchStatus } from '@/services/api'

const mockFetch = vi.fn()

beforeEach(() => {
  localStorage.clear()
  mockFetch.mockReset()
  vi.stubGlobal('fetch', mockFetch)
})
afterEach(() => {
  vi.unstubAllGlobals()
})

function okResponse(body: unknown) {
  return { ok: true, status: 200, text: async () => JSON.stringify(body) }
}

describe('api auth client', () => {
  it('stores and clears the auth token', () => {
    expect(getAuthToken()).toBeNull()
    setAuthToken('tok123')
    expect(getAuthToken()).toBe('tok123')
    setAuthToken(null)
    expect(getAuthToken()).toBeNull()
  })

  it('attaches a Bearer header once a token is set', async () => {
    setAuthToken('tok123')
    mockFetch.mockResolvedValue(okResponse({ ok: true }))

    await fetchStatus()

    expect(mockFetch).toHaveBeenCalledTimes(1)
    const init = mockFetch.mock.calls[0][1] as RequestInit
    const headers = init.headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer tok123')
  })

  it('clears the token and emits api:unauthorized on a 401', async () => {
    setAuthToken('expired')
    mockFetch.mockResolvedValue({
      ok: false,
      status: 401,
      statusText: 'Unauthorized',
      text: async () => 'no',
    })
    const onUnauth = vi.fn()
    window.addEventListener('api:unauthorized', onUnauth)

    await expect(fetchStatus()).rejects.toThrow()

    expect(getAuthToken()).toBeNull()
    expect(onUnauth).toHaveBeenCalledTimes(1)
    window.removeEventListener('api:unauthorized', onUnauth)
  })
})
