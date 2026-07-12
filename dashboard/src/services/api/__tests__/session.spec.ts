import { beforeEach, describe, expect, it, vi } from 'vitest'


const postMock = vi.fn()

vi.mock('@/services/api/client', () => ({
  post: postMock,
}))


describe('bootstrapSession runtime launch boundary', () => {
  beforeEach(() => {
    vi.resetModules()
    postMock.mockReset()
    window.__TRADEBOT_SESSION_BOOTSTRAP__ = undefined
    window.history.replaceState({}, '', '/')
  })

  it('calls the public session endpoint and deduplicates concurrent startup', async () => {
    postMock.mockResolvedValue({
      access_token: 'short-token',
      token_type: 'bearer',
      expires_at: new Date(Date.now() + 60_000).toISOString(),
      expires_in_seconds: 60,
    })
    const { bootstrapSession } = await import('@/services/api/session')

    const first = bootstrapSession()
    const second = bootstrapSession()

    expect(first).toBe(second)
    await expect(first).resolves.toMatchObject({ access_token: 'short-token' })
    expect(postMock).toHaveBeenCalledTimes(1)
    expect(postMock).toHaveBeenCalledWith('/api/session/bootstrap', {})
  })

  it('consumes a launcher fragment at runtime and scrubs it from the URL', async () => {
    window.history.replaceState({}, '', '/#session-bootstrap=launch-capability-123456&view=desk')
    postMock.mockResolvedValue({
      access_token: 'desktop-token',
      token_type: 'bearer',
      expires_at: new Date(Date.now() + 60_000).toISOString(),
      expires_in_seconds: 60,
    })
    const { bootstrapSession } = await import('@/services/api/session')

    await bootstrapSession()

    expect(postMock).toHaveBeenCalledWith('/api/session/bootstrap', {
      launch_token: 'launch-capability-123456',
    })
    expect(window.location.hash).toBe('#view=desk')
    expect(window.location.href).not.toContain('launch-capability-123456')
  })

  it('rejects malformed bootstrap responses', async () => {
    postMock.mockResolvedValue({ access_token: '', token_type: 'bearer' })
    const { bootstrapSession } = await import('@/services/api/session')

    await expect(bootstrapSession()).rejects.toThrow(/invalid response/)
  })

  it('rejects a response whose advertised session is already expired', async () => {
    postMock.mockResolvedValue({
      access_token: 'expired-token',
      token_type: 'bearer',
      expires_at: new Date(Date.now() - 1).toISOString(),
      expires_in_seconds: 60,
    })
    const { bootstrapSession } = await import('@/services/api/session')

    await expect(bootstrapSession()).rejects.toThrow(/invalid response/)
  })
})
