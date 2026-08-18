import { useCallback, useEffect, useState } from 'react'
import {
  fetchPushStatus,
  fetchPushSubscriptionStatus,
  subscribePush,
  testPushNotification,
  unsubscribePush,
} from '@/services/api'
import {
  browserPushSupported,
  decodeApplicationServerKey,
  getPushSessionGeneration,
  getPushRegistration,
  runPushOperation,
  subscriptionUsesKey,
  verifiedUnsubscribePush,
  waitForPushBrowserMutation,
  waitForPushBrowserRead,
  type PushOperationContext,
} from '@/services/browserPush'

export type NotificationPermission = 'default' | 'granted' | 'denied' | 'unsupported'

export interface UseNotificationsResult {
  permission: NotificationPermission
  supported:  boolean
  enabled:    boolean
  ready:      boolean
  subscribed: boolean
  loading:    boolean
  error:      string | null
  enable:     () => Promise<boolean>
  disable:    () => Promise<boolean>
  test:       () => Promise<boolean>
  refresh:    () => Promise<void>
  reconcile:  (preferenceEnabled: boolean) => Promise<void>
}

function currentPermission(): NotificationPermission {
  if (!browserPushSupported()) return 'unsupported'
  return Notification.permission
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Browser push operation failed'
}

function errorStatus(error: unknown): number | null {
  return error instanceof Error && 'status' in error
    ? (error as Error & { status: number }).status
    : null
}

async function removeSubscription(
  subscription: PushSubscription,
  registered: boolean,
  context: PushOperationContext,
): Promise<unknown | null> {
  let backendError: unknown = null
  if (registered) {
    try {
      await unsubscribePush(subscription.endpoint, context.signal)
    } catch (error) {
      if (errorStatus(error) !== 404) backendError = error
    }
  }
  await verifiedUnsubscribePush(subscription, context)
  return backendError
}

async function createSubscription(
  publicKey: string,
  context: PushOperationContext,
): Promise<PushSubscription> {
  const registration = await waitForPushBrowserRead(
    getPushRegistration(),
    'Service worker activation timed out',
    context,
  )
  context.assertCurrent()
  const subscription = await waitForPushBrowserMutation(
    registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: decodeApplicationServerKey(publicKey) as BufferSource,
    }),
    'Browser push registration timed out; retry securely',
    context,
  )
  context.assertCurrent()
  try {
    await subscribePush(subscription.toJSON(), context.signal)
    context.assertCurrent()
    return subscription
  } catch (subscribeError) {
    await verifiedUnsubscribePush(subscription, context)
    context.assertCurrent()
    throw subscribeError
  }
}

export function useNotifications(): UseNotificationsResult {
  const supported = browserPushSupported()
  const [permission, setPermission] = useState<NotificationPermission>(currentPermission)
  const [enabled, setEnabled] = useState(false)
  const [ready, setReady] = useState(false)
  const [subscribed, setSubscribed] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const readDeviceState = useCallback(async (
    signal?: AbortSignal,
    context?: PushOperationContext,
  ) => {
    const status = await fetchPushStatus(signal)
    setEnabled(status.enabled)
    setReady(status.ready)

    const registration = await waitForPushBrowserRead(
      getPushRegistration(),
      'Service worker activation timed out',
      context,
    )
    const localSubscription = await waitForPushBrowserRead(
      registration.pushManager.getSubscription(),
      'Browser push subscription lookup timed out',
      context,
    )
    if (!localSubscription) {
      setSubscribed(false)
      return { status, localSubscription: null, registered: false }
    }

    const ownership = await fetchPushSubscriptionStatus(localSubscription.endpoint, undefined, signal)
    const usesCurrentKey = status.public_key
      ? subscriptionUsesKey(localSubscription, status.public_key)
      : true
    setSubscribed(ownership.registered && usesCurrentKey)
    return {
      status,
      localSubscription,
      registered: ownership.registered,
    }
  }, [])

  const refresh = useCallback(async () => {
    setPermission(currentPermission())
    if (!supported) {
      setEnabled(false)
      setReady(false)
      setSubscribed(false)
      return
    }

    setLoading(true)
    setError(null)
    try {
      await readDeviceState()
    } catch (refreshError) {
      setError(errorMessage(refreshError))
      setReady(false)
    } finally {
      setLoading(false)
    }
  }, [readDeviceState, supported])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    if (!supported || !navigator.permissions) return
    let status: PermissionStatus | null = null

    navigator.permissions
      .query({ name: 'notifications' as PermissionName })
      .then((nextStatus) => {
        status = nextStatus
        nextStatus.onchange = () => setPermission(currentPermission())
      })
      .catch(() => undefined)

    return () => {
      if (status) status.onchange = null
    }
  }, [supported])

  const enable = useCallback(async (): Promise<boolean> => {
    if (!supported) {
      setError('Persistent browser notifications are not supported here')
      return false
    }

    setLoading(true)
    setError(null)
    try {
      const generation = getPushSessionGeneration()
      return await runPushOperation(generation, async (context) => {
        const { assertCurrent, signal } = context
        const status = await fetchPushStatus(signal)
        assertCurrent()
        setEnabled(status.enabled)
        setReady(status.ready)
        if (!status.enabled) throw new Error('Browser push is disabled on this environment')
        if (!status.ready || !status.public_key) {
          throw new Error('Browser push is not configured on the server')
        }

        const nextPermission = Notification.permission === 'default'
          ? await waitForPushBrowserRead(
            Notification.requestPermission(),
            'Browser notification permission timed out; retry securely',
            context,
          )
          : Notification.permission
        assertCurrent()
        setPermission(nextPermission)
        if (nextPermission !== 'granted') {
          throw new Error(nextPermission === 'denied'
            ? 'Browser notification permission is blocked'
            : 'Browser notification permission was not granted')
        }

        const registration = await waitForPushBrowserRead(
          getPushRegistration(),
          'Service worker activation timed out',
          context,
        )
        assertCurrent()
        let subscription = await waitForPushBrowserRead(
          registration.pushManager.getSubscription(),
          'Browser push subscription lookup timed out',
          context,
        )
        assertCurrent()
        if (subscription) {
          const ownership = await fetchPushSubscriptionStatus(subscription.endpoint, undefined, signal)
          assertCurrent()
          if (!ownership.registered || !subscriptionUsesKey(subscription, status.public_key)) {
            const cleanupError = await removeSubscription(subscription, ownership.registered, context)
            assertCurrent()
            if (cleanupError) throw cleanupError
            subscription = null
          }
        }

        if (!subscription) {
          subscription = await createSubscription(status.public_key, context)
        } else {
          try {
            await subscribePush(subscription.toJSON(), signal)
            assertCurrent()
          } catch (subscribeError) {
            if (errorStatus(subscribeError) === 409) {
              await verifiedUnsubscribePush(subscription, context)
              assertCurrent()
            }
            throw subscribeError
          }
        }
        setSubscribed(true)
        return true
      })
    } catch (enableError) {
      setError(errorMessage(enableError))
      setSubscribed(false)
      return false
    } finally {
      setLoading(false)
    }
  }, [supported])

  const disable = useCallback(async (): Promise<boolean> => {
    if (!supported) {
      setError('Persistent browser notifications are not supported here')
      return false
    }

    setLoading(true)
    setError(null)
    try {
      const generation = getPushSessionGeneration()
      return await runPushOperation(generation, async (context) => {
        const { assertCurrent } = context
        const registration = await waitForPushBrowserRead(
          getPushRegistration(),
          'Service worker activation timed out',
          context,
        )
        assertCurrent()
        const subscription = await waitForPushBrowserRead(
          registration.pushManager.getSubscription(),
          'Browser push subscription lookup timed out',
          context,
        )
        assertCurrent()
        if (!subscription) {
          setSubscribed(false)
          return true
        }

        const backendError = await removeSubscription(subscription, true, context)
        assertCurrent()
        setSubscribed(false)
        if (backendError) throw backendError
        return true
      })
    } catch (disableError) {
      setError(errorMessage(disableError))
      return false
    } finally {
      setLoading(false)
    }
  }, [supported])

  const reconcile = useCallback(async (preferenceEnabled: boolean): Promise<void> => {
    if (!supported) return
    setError(null)
    try {
      const generation = getPushSessionGeneration()
      await runPushOperation(generation, async (context) => {
        const { assertCurrent, signal } = context
        const { status, localSubscription, registered } = await readDeviceState(signal, context)
        assertCurrent()
        if (!localSubscription) return

        if (!registered) {
          await verifiedUnsubscribePush(localSubscription, context)
          assertCurrent()
          setSubscribed(false)
          return
        }

        if (!preferenceEnabled) {
          const cleanupError = await removeSubscription(localSubscription, true, context)
          assertCurrent()
          setSubscribed(false)
          if (cleanupError) throw cleanupError
          return
        }

        if (status.public_key && !subscriptionUsesKey(localSubscription, status.public_key)) {
          const cleanupError = await removeSubscription(localSubscription, true, context)
          assertCurrent()
          setSubscribed(false)
          if (cleanupError) throw cleanupError
          if (status.ready && Notification.permission === 'granted') {
            await createSubscription(status.public_key, context)
            setSubscribed(true)
          }
        }
      })
    } catch (reconcileError) {
      setError(errorMessage(reconcileError))
    }
  }, [readDeviceState, supported])

  const test = useCallback(async (): Promise<boolean> => {
    setLoading(true)
    setError(null)
    try {
      const generation = getPushSessionGeneration()
      return await runPushOperation(generation, async ({ assertCurrent, signal }) => {
        const result = await testPushNotification(signal)
        assertCurrent()
        if (result.delivered < 1) throw new Error('The push provider did not deliver the test')
        return true
      })
    } catch (testError) {
      setError(errorMessage(testError))
      return false
    } finally {
      setLoading(false)
    }
  }, [])

  return {
    permission,
    supported,
    enabled,
    ready,
    subscribed,
    loading,
    error,
    enable,
    disable,
    test,
    refresh,
    reconcile,
  }
}
