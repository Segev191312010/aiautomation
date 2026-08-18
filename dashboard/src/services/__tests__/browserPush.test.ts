import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  beginPushSessionTransition,
  getPushSessionGeneration,
  runPushOperation,
  verifiedUnsubscribePush,
  waitForPushBrowserMutation,
  waitForPushBrowserRead,
} from '@/services/browserPush'

const pushManager = {
  getSubscription: vi.fn(),
}

function installBrowserPush() {
  vi.stubGlobal('Notification', { permission: 'granted' })
  vi.stubGlobal('PushManager', class PushManager {})
  Object.defineProperty(navigator, 'serviceWorker', {
    configurable: true,
    value: {
      getRegistration: vi.fn(async () => ({ pushManager })),
    },
  })
}

describe('verifiedUnsubscribePush', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    installBrowserPush()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('rejects when the browser returns false and verifies the endpoint remains', async () => {
    const subscription = { unsubscribe: vi.fn(async () => false) } as unknown as PushSubscription
    pushManager.getSubscription.mockResolvedValue(subscription)

    await expect(verifiedUnsubscribePush(subscription)).rejects.toThrow('did not remove')
    expect(pushManager.getSubscription).toHaveBeenCalledOnce()
  })

  it('accepts authoritative removal when unsubscribe throws on a stale object', async () => {
    const failure = new Error('unsubscribe rejected')
    const subscription = { unsubscribe: vi.fn(async () => { throw failure }) } as unknown as PushSubscription
    pushManager.getSubscription.mockResolvedValue(null)

    await expect(verifiedUnsubscribePush(subscription)).resolves.toBeUndefined()
    expect(pushManager.getSubscription).toHaveBeenCalledOnce()
  })

  it('rejects when the browser reports success but the endpoint remains', async () => {
    const subscription = {
      endpoint: 'https://push.example.test/old',
      unsubscribe: vi.fn(async () => true),
    } as unknown as PushSubscription
    pushManager.getSubscription.mockResolvedValue(subscription)

    await expect(verifiedUnsubscribePush(subscription)).rejects.toThrow('still active')
  })

  it('rejects when a different endpoint appears during cleanup', async () => {
    const subscription = {
      endpoint: 'https://push.example.test/old',
      unsubscribe: vi.fn(async () => true),
    } as unknown as PushSubscription
    pushManager.getSubscription.mockResolvedValue({ endpoint: 'https://push.example.test/new' })

    await expect(verifiedUnsubscribePush(subscription)).rejects.toThrow('different')
  })

  it('succeeds only after the browser reports success and rereads null', async () => {
    const subscription = { unsubscribe: vi.fn(async () => true) } as unknown as PushSubscription
    pushManager.getSubscription.mockResolvedValue(null)

    await expect(verifiedUnsubscribePush(subscription)).resolves.toBeUndefined()
  })
})

describe('push operation sessions', () => {
  it('serializes browser endpoint mutations', async () => {
    const generation = getPushSessionGeneration()
    const order: string[] = []
    let releaseFirst!: () => void
    let markStarted!: () => void
    const started = new Promise<void>((resolve) => { markStarted = resolve })
    const first = runPushOperation(generation, async () => {
      order.push('first:start')
      markStarted()
      await new Promise<void>((resolve) => { releaseFirst = resolve })
      order.push('first:end')
    })
    await started

    const second = runPushOperation(generation, async () => {
      order.push('second')
    })
    expect(order).toEqual(['first:start'])

    releaseFirst()
    await Promise.all([first, second])
    expect(order).toEqual(['first:start', 'first:end', 'second'])
  })

  it('invalidates an old operation before its deferred mutation', async () => {
    const oldGeneration = getPushSessionGeneration()
    const mutate = vi.fn()
    let releaseOld!: () => void
    let markStarted!: () => void
    const started = new Promise<void>((resolve) => { markStarted = resolve })
    const oldOperation = runPushOperation(oldGeneration, async ({ assertCurrent }) => {
      markStarted()
      await new Promise<void>((resolve) => { releaseOld = resolve })
      assertCurrent()
      mutate()
    })
    await started

    const newGeneration = beginPushSessionTransition()
    const newOperation = runPushOperation(newGeneration, async () => undefined)
    releaseOld()

    await expect(oldOperation).rejects.toThrow('session changed')
    await newOperation
    expect(mutate).not.toHaveBeenCalled()
  })

  it('aborts a stalled browser read so the next session can run', async () => {
    const oldGeneration = getPushSessionGeneration()
    let markStarted!: () => void
    const started = new Promise<void>((resolve) => { markStarted = resolve })
    const stalledRead = runPushOperation(oldGeneration, async (context) => {
      markStarted()
      await waitForPushBrowserRead(
        new Promise<never>(() => undefined),
        'read timed out',
        context,
      )
    })
    const oldResult = stalledRead.catch((error: unknown) => error)
    await started

    const nextGeneration = beginPushSessionTransition()
    const nextOperation = runPushOperation(nextGeneration, async () => 'next session')

    expect(await oldResult).toBeInstanceOf(Error)
    await expect(nextOperation).resolves.toBe('next session')
  })

  it('turns a never-settling browser mutation into a bounded cleanup error', async () => {
    vi.useFakeTimers()
    try {
      const oldGeneration = getPushSessionGeneration()
      let markStarted!: () => void
      const started = new Promise<void>((resolve) => { markStarted = resolve })
      const stalledMutation = runPushOperation(oldGeneration, async (context) => {
        markStarted()
        await waitForPushBrowserMutation(
          new Promise<never>(() => undefined),
          'mutation timed out',
          context,
        )
      })
      const oldResult = stalledMutation.catch((error: unknown) => error)
      await started

      const nextGeneration = beginPushSessionTransition()
      const nextOperation = runPushOperation(nextGeneration, async () => 'unsafe')
      const nextResult = nextOperation.catch((error: unknown) => error)
      expect(await oldResult).toBeInstanceOf(Error)
      await Promise.resolve()
      await vi.advanceTimersByTimeAsync(10_000)

      const failure = await nextResult
      expect(failure).toBeInstanceOf(Error)
      expect((failure as Error).message).toContain('still pending')
    } finally {
      vi.useRealTimers()
    }
  })
})
