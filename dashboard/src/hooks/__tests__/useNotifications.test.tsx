import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useNotifications } from '@/hooks/useNotifications'
import * as api from '@/services/api'
import {
  beginPushSessionTransition,
  getExistingPushSubscription,
  runPushOperation,
  verifiedUnsubscribePush,
} from '@/services/browserPush'
import { DEFAULT_NOTIFICATION_PREFS } from '@/types'

vi.mock('@/services/api', () => ({
  fetchPushStatus: vi.fn(),
  fetchPushSubscriptionStatus: vi.fn(),
  subscribePush: vi.fn(),
  unsubscribePush: vi.fn(),
  testPushNotification: vi.fn(),
}))

const endpoint = 'https://push.example.test/device'
const subscription = {
  endpoint,
  options: { applicationServerKey: Uint8Array.from([1, 2, 3]).buffer },
  toJSON: vi.fn(() => ({
    endpoint,
    expirationTime: null,
    keys: { p256dh: 'client-key', auth: 'client-auth' },
  })),
  unsubscribe: vi.fn(async () => true),
}

const pushManager = {
  getSubscription: vi.fn<() => Promise<typeof subscription | null>>(),
  subscribe: vi.fn(async () => subscription),
}

const readyStatus = {
  enabled: true,
  ready: true,
  public_key: 'AQID',
  missing_configuration: [],
  invalid_configuration: [],
  subscribed: false,
  subscription_count: 0,
  preferences: { ...DEFAULT_NOTIFICATION_PREFS },
}

function installBrowserPush(permission: NotificationPermission = 'granted') {
  const notificationApi = {
    permission,
    requestPermission: vi.fn(async () => 'granted' as NotificationPermission),
  }
  vi.stubGlobal('Notification', notificationApi)
  vi.stubGlobal('PushManager', class PushManager {})
  Object.defineProperty(navigator, 'serviceWorker', {
    configurable: true,
    value: {
      ready: Promise.resolve({ pushManager }),
      register: vi.fn(async () => ({ pushManager })),
      getRegistration: vi.fn(async () => ({ pushManager })),
    },
  })
  Object.defineProperty(navigator, 'permissions', {
    configurable: true,
    value: { query: vi.fn(async () => ({ onchange: null })) },
  })
  return notificationApi
}

describe('useNotifications', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    installBrowserPush()
    pushManager.getSubscription.mockResolvedValue(null)
    subscription.unsubscribe.mockImplementation(async () => {
      pushManager.getSubscription.mockResolvedValue(null)
      return true
    })
    subscription.options.applicationServerKey = Uint8Array.from([1, 2, 3]).buffer
    vi.mocked(api.fetchPushStatus).mockResolvedValue({ ...readyStatus })
    vi.mocked(api.fetchPushSubscriptionStatus).mockResolvedValue({ registered: true })
    vi.mocked(api.subscribePush).mockResolvedValue({
      subscribed: true,
      created: true,
      subscription_count: 1,
      preferences: { ...DEFAULT_NOTIFICATION_PREFS, browser_push: true },
    })
    vi.mocked(api.unsubscribePush).mockResolvedValue({
      subscribed: false,
      subscription_count: 0,
    })
    vi.mocked(api.testPushNotification).mockResolvedValue({
      success: true,
      subscription_count: 1,
      delivered: 1,
      expired_removed: 0,
      failed: 0,
      skipped_preference: false,
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('registers push even when browser permission was already granted', async () => {
    const notificationApi = installBrowserPush('granted')
    const { result } = renderHook(() => useNotifications())
    await waitFor(() => expect(result.current.ready).toBe(true))

    let enabled = false
    await act(async () => {
      enabled = await result.current.enable()
    })

    expect(enabled).toBe(true)
    expect(notificationApi.requestPermission).not.toHaveBeenCalled()
    expect(pushManager.subscribe).toHaveBeenCalledOnce()
    expect(api.subscribePush).toHaveBeenCalledWith(subscription.toJSON(), expect.anything())
    expect(result.current.subscribed).toBe(true)
  })

  it('fails visibly without prompting when the environment kill switch is off', async () => {
    const notificationApi = installBrowserPush('default')
    vi.mocked(api.fetchPushStatus).mockResolvedValue({
      ...readyStatus,
      enabled: false,
      ready: false,
      public_key: null,
    })
    const { result } = renderHook(() => useNotifications())
    await waitFor(() => expect(result.current.loading).toBe(false))

    let enabled = true
    await act(async () => {
      enabled = await result.current.enable()
    })

    expect(enabled).toBe(false)
    expect(notificationApi.requestPermission).not.toHaveBeenCalled()
    expect(api.subscribePush).not.toHaveBeenCalled()
    expect(result.current.error).toContain('disabled')
  })

  it('removes both the backend and browser subscriptions', async () => {
    pushManager.getSubscription.mockResolvedValue(subscription)
    const { result } = renderHook(() => useNotifications())
    await waitFor(() => expect(result.current.ready).toBe(true))

    let disabled = false
    await act(async () => {
      disabled = await result.current.disable()
    })

    expect(disabled).toBe(true)
    expect(api.unsubscribePush).toHaveBeenCalledWith(endpoint, expect.anything())
    expect(subscription.unsubscribe).toHaveBeenCalledOnce()
    expect(result.current.subscribed).toBe(false)
  })

  it('fails closed when the browser refuses to remove its subscription', async () => {
    pushManager.getSubscription.mockResolvedValue(subscription)
    subscription.unsubscribe.mockResolvedValueOnce(false)
    const { result } = renderHook(() => useNotifications())
    await waitFor(() => expect(result.current.subscribed).toBe(true))

    let disabled = true
    await act(async () => {
      disabled = await result.current.disable()
    })

    expect(disabled).toBe(false)
    expect(pushManager.getSubscription).toHaveBeenCalled()
    expect(result.current.subscribed).toBe(true)
    expect(result.current.error).toContain('did not remove')
  })

  it('quarantines an endpoint owned by another authenticated user before subscribing', async () => {
    pushManager.getSubscription.mockResolvedValue(subscription)
    vi.mocked(api.fetchPushSubscriptionStatus).mockResolvedValue({ registered: false })
    const { result } = renderHook(() => useNotifications())
    await waitFor(() => expect(result.current.loading).toBe(false))

    let enabled = false
    await act(async () => {
      enabled = await result.current.enable()
    })

    expect(enabled).toBe(true)
    expect(subscription.unsubscribe).toHaveBeenCalledOnce()
    expect(api.unsubscribePush).not.toHaveBeenCalled()
    expect(pushManager.subscribe).toHaveBeenCalledOnce()
    expect(api.subscribePush).toHaveBeenCalledOnce()
  })

  it('rotates a same-user subscription when the VAPID key changes', async () => {
    pushManager.getSubscription.mockResolvedValue(subscription)
    subscription.options.applicationServerKey = Uint8Array.from([9, 9, 9]).buffer
    const { result } = renderHook(() => useNotifications())
    await waitFor(() => expect(result.current.loading).toBe(false))

    let enabled = false
    await act(async () => {
      enabled = await result.current.enable()
    })

    expect(enabled).toBe(true)
    expect(api.unsubscribePush).toHaveBeenCalledWith(endpoint, expect.anything())
    expect(subscription.unsubscribe).toHaveBeenCalledOnce()
    expect(pushManager.subscribe).toHaveBeenCalledOnce()
    expect(api.subscribePush).toHaveBeenCalledOnce()
  })

  it('repairs an already-enrolled device during reconciliation', async () => {
    pushManager.getSubscription.mockResolvedValue(subscription)
    subscription.options.applicationServerKey = Uint8Array.from([9, 9, 9]).buffer
    const { result } = renderHook(() => useNotifications())
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      await result.current.reconcile(true)
    })

    expect(api.unsubscribePush).toHaveBeenCalledWith(endpoint, expect.anything())
    expect(subscription.unsubscribe).toHaveBeenCalledOnce()
    expect(pushManager.subscribe).toHaveBeenCalledOnce()
    expect(api.subscribePush).toHaveBeenCalledOnce()
    expect(result.current.subscribed).toBe(true)
  })

  it('aborts an in-flight enable when the authenticated session changes', async () => {
    const { result } = renderHook(() => useNotifications())
    await waitFor(() => expect(result.current.ready).toBe(true))
    let resolveStatus!: (status: typeof readyStatus) => void
    vi.mocked(api.fetchPushStatus).mockImplementationOnce(() => new Promise((resolve) => {
      resolveStatus = resolve
    }))

    let enablePromise!: Promise<boolean>
    act(() => {
      enablePromise = result.current.enable()
    })
    await waitFor(() => expect(result.current.loading).toBe(true))
    beginPushSessionTransition()
    resolveStatus({ ...readyStatus })

    let enabled = true
    await act(async () => {
      enabled = await enablePromise
    })
    expect(enabled).toBe(false)
    expect(pushManager.subscribe).not.toHaveBeenCalled()
    expect(api.subscribePush).not.toHaveBeenCalled()
  })

  it('quarantines a subscription created after its session was invalidated', async () => {
    const { result } = renderHook(() => useNotifications())
    await waitFor(() => expect(result.current.ready).toBe(true))
    let resolveSubscribe!: (value: typeof subscription) => void
    pushManager.subscribe.mockImplementationOnce(() => new Promise((resolve) => {
      resolveSubscribe = resolve
    }))

    let enablePromise!: Promise<boolean>
    act(() => {
      enablePromise = result.current.enable()
    })
    await waitFor(() => expect(pushManager.subscribe).toHaveBeenCalledOnce())
    const nextGeneration = beginPushSessionTransition()
    const cleanup = runPushOperation(nextGeneration, async ({ assertCurrent }) => {
      const existing = await getExistingPushSubscription()
      assertCurrent()
      if (existing) await verifiedUnsubscribePush(existing)
    })
    pushManager.getSubscription.mockResolvedValue(subscription)
    resolveSubscribe(subscription)

    let enabled = true
    await act(async () => {
      enabled = await enablePromise
      await cleanup
    })
    expect(enabled).toBe(false)
    expect(api.subscribePush).not.toHaveBeenCalled()
    expect(subscription.unsubscribe).toHaveBeenCalledOnce()
    expect(await pushManager.getSubscription()).toBeNull()
  })

  it('quarantines a local endpoint when backend registration finishes after transition', async () => {
    const { result } = renderHook(() => useNotifications())
    await waitFor(() => expect(result.current.ready).toBe(true))
    pushManager.subscribe.mockImplementationOnce(async () => {
      pushManager.getSubscription.mockResolvedValue(subscription)
      return subscription
    })
    let resolveBackend!: (value: Awaited<ReturnType<typeof api.subscribePush>>) => void
    vi.mocked(api.subscribePush).mockImplementationOnce(() => new Promise((resolve) => {
      resolveBackend = resolve
    }))

    let enablePromise!: Promise<boolean>
    act(() => {
      enablePromise = result.current.enable()
    })
    await waitFor(() => expect(api.subscribePush).toHaveBeenCalledOnce())
    const nextGeneration = beginPushSessionTransition()
    const cleanup = runPushOperation(nextGeneration, async ({ assertCurrent }) => {
      const existing = await getExistingPushSubscription()
      assertCurrent()
      if (existing) await verifiedUnsubscribePush(existing)
    })
    resolveBackend({
      subscribed: true,
      created: true,
      subscription_count: 1,
      preferences: { ...DEFAULT_NOTIFICATION_PREFS, browser_push: true },
    })

    let enabled = true
    await act(async () => {
      enabled = await enablePromise
      await cleanup
    })
    expect(enabled).toBe(false)
    expect(subscription.unsubscribe).toHaveBeenCalledOnce()
    expect(await pushManager.getSubscription()).toBeNull()
  })

  it('does not recreate a stale VAPID endpoint after session transition', async () => {
    pushManager.getSubscription.mockResolvedValue(subscription)
    subscription.options.applicationServerKey = Uint8Array.from([9, 9, 9]).buffer
    const { result } = renderHook(() => useNotifications())
    await waitFor(() => expect(result.current.loading).toBe(false))
    let resolveDelete!: (value: Awaited<ReturnType<typeof api.unsubscribePush>>) => void
    vi.mocked(api.unsubscribePush).mockImplementationOnce(() => new Promise((resolve) => {
      resolveDelete = resolve
    }))

    let reconcilePromise!: Promise<void>
    act(() => {
      reconcilePromise = result.current.reconcile(true)
    })
    await waitFor(() => expect(api.unsubscribePush).toHaveBeenCalledOnce())
    beginPushSessionTransition()
    resolveDelete({ subscribed: false, subscription_count: 0 })

    await act(async () => {
      await reconcilePromise
    })
    expect(subscription.unsubscribe).toHaveBeenCalledOnce()
    expect(pushManager.subscribe).not.toHaveBeenCalled()
    expect(api.subscribePush).not.toHaveBeenCalled()
  })

  it('serializes duplicate reconciliation into one effective key rotation', async () => {
    pushManager.getSubscription.mockResolvedValue(subscription)
    subscription.options.applicationServerKey = Uint8Array.from([9, 9, 9]).buffer
    pushManager.subscribe.mockImplementation(async () => {
      subscription.options.applicationServerKey = Uint8Array.from([1, 2, 3]).buffer
      pushManager.getSubscription.mockResolvedValue(subscription)
      return subscription
    })
    const { result } = renderHook(() => useNotifications())
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      await Promise.all([
        result.current.reconcile(true),
        result.current.reconcile(true),
      ])
    })

    expect(api.unsubscribePush).toHaveBeenCalledTimes(1)
    expect(subscription.unsubscribe).toHaveBeenCalledTimes(1)
    expect(pushManager.subscribe).toHaveBeenCalledTimes(1)
    expect(api.subscribePush).toHaveBeenCalledTimes(1)
    expect(result.current.subscribed).toBe(true)
  })

  it('does not silently enroll a browser from the account-wide preference', async () => {
    pushManager.getSubscription.mockResolvedValue(null)
    const { result } = renderHook(() => useNotifications())
    await waitFor(() => expect(result.current.ready).toBe(true))

    await act(async () => {
      await result.current.reconcile(true)
    })

    expect(pushManager.subscribe).not.toHaveBeenCalled()
    expect(api.subscribePush).not.toHaveBeenCalled()
    expect(result.current.subscribed).toBe(false)
  })

  it('quarantines a foreign endpoint without enrolling it for the current user', async () => {
    pushManager.getSubscription.mockResolvedValue(subscription)
    vi.mocked(api.fetchPushSubscriptionStatus).mockResolvedValue({ registered: false })
    const { result } = renderHook(() => useNotifications())
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      await result.current.reconcile(true)
    })

    expect(subscription.unsubscribe).toHaveBeenCalledOnce()
    expect(api.unsubscribePush).not.toHaveBeenCalled()
    expect(pushManager.subscribe).not.toHaveBeenCalled()
    expect(api.subscribePush).not.toHaveBeenCalled()
  })

  it('removes an owned device when the account preference is disabled', async () => {
    pushManager.getSubscription.mockResolvedValue(subscription)
    const { result } = renderHook(() => useNotifications())
    await waitFor(() => expect(result.current.subscribed).toBe(true))

    await act(async () => {
      await result.current.reconcile(false)
    })

    expect(api.unsubscribePush).toHaveBeenCalledWith(endpoint, expect.anything())
    expect(subscription.unsubscribe).toHaveBeenCalledOnce()
    expect(pushManager.subscribe).not.toHaveBeenCalled()
    expect(result.current.subscribed).toBe(false)
  })
})
