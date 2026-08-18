const REGISTRATION_TIMEOUT_MS = 10_000
const BROWSER_OPERATION_TIMEOUT_MS = 10_000

let registrationPromise: Promise<ServiceWorkerRegistration> | null = null
let pushSessionGeneration = 0
let pushOperationQueue: Promise<void> = Promise.resolve()
let pushSessionController = new AbortController()
const pendingBrowserMutations = new Set<Promise<unknown>>()

export interface PushOperationContext {
  generation: number
  signal: AbortSignal
  assertCurrent: () => void
}

export function beginPushSessionTransition(): number {
  pushSessionController.abort()
  pushSessionController = new AbortController()
  pushSessionGeneration += 1
  return pushSessionGeneration
}

export function getPushSessionGeneration(): number {
  return pushSessionGeneration
}

export function pushSessionIsCurrent(generation: number): boolean {
  return generation === pushSessionGeneration
}

export function runPushOperation<T>(
  generation: number,
  operation: (context: PushOperationContext) => Promise<T>,
): Promise<T> {
  const assertCurrent = () => {
    if (!pushSessionIsCurrent(generation) || pushSessionController.signal.aborted) {
      throw new Error('The browser notification session changed during this operation')
    }
  }
  const execute = async () => {
    assertCurrent()
    const context = {
      generation,
      signal: pushSessionController.signal,
      assertCurrent,
    }
    await waitForPendingBrowserMutations(context)
    const result = await operation(context)
    assertCurrent()
    return result
  }
  const result = pushOperationQueue.then(execute, execute)
  pushOperationQueue = result.then(() => undefined, () => undefined)
  return result
}

function waitForBrowserPromise<T>(
  promise: Promise<T>,
  message: string,
  context?: PushOperationContext,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    let settled = false
    const finish = (callback: () => void) => {
      if (settled) return
      settled = true
      window.clearTimeout(timer)
      context?.signal.removeEventListener('abort', abort)
      callback()
    }
    const abort = () => finish(() => reject(new Error(
      'The browser notification session changed during this operation',
    )))
    const timer = window.setTimeout(
      () => finish(() => reject(new Error(message))),
      BROWSER_OPERATION_TIMEOUT_MS,
    )
    if (context?.signal.aborted) {
      abort()
      return
    }
    context?.signal.addEventListener('abort', abort, { once: true })
    promise.then(
      (value) => finish(() => resolve(value)),
      (error) => finish(() => reject(error)),
    )
  })
}

export function waitForPushBrowserRead<T>(
  promise: Promise<T>,
  message: string,
  context?: PushOperationContext,
): Promise<T> {
  return waitForBrowserPromise(promise, message, context)
}

export function waitForPushBrowserMutation<T>(
  promise: Promise<T>,
  message: string,
  context: PushOperationContext,
): Promise<T> {
  const tracked = promise as Promise<unknown>
  pendingBrowserMutations.add(tracked)
  tracked.then(
    () => pendingBrowserMutations.delete(tracked),
    () => pendingBrowserMutations.delete(tracked),
  )
  return waitForBrowserPromise(promise, message, context)
}

async function waitForPendingBrowserMutations(
  context: PushOperationContext,
): Promise<void> {
  const pending = [...pendingBrowserMutations]
  if (pending.length === 0) return
  await waitForBrowserPromise(
    Promise.allSettled(pending).then(() => undefined),
    'A previous browser notification change is still pending; retry securely',
    context,
  )
  context.assertCurrent()
}

export function browserPushSupported(): boolean {
  return (
    typeof window !== 'undefined'
    && typeof navigator !== 'undefined'
    && 'Notification' in window
    && 'serviceWorker' in navigator
    && 'PushManager' in window
  )
}

function withTimeout<T>(promise: Promise<T>, message: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error(message)), REGISTRATION_TIMEOUT_MS)
    promise.then(
      (value) => {
        window.clearTimeout(timer)
        resolve(value)
      },
      (error) => {
        window.clearTimeout(timer)
        reject(error)
      },
    )
  })
}

export function registerPushServiceWorker(): Promise<ServiceWorkerRegistration> {
  if (!browserPushSupported()) {
    return Promise.reject(new Error('Persistent browser notifications are not supported here'))
  }
  if (!registrationPromise) {
    registrationPromise = navigator.serviceWorker.register('/sw.js').catch((error) => {
      registrationPromise = null
      throw error
    })
  }
  return withTimeout(registrationPromise, 'Service worker registration timed out')
}

export async function getPushRegistration(): Promise<ServiceWorkerRegistration> {
  await registerPushServiceWorker()
  return withTimeout(
    navigator.serviceWorker.ready,
    'Service worker activation timed out',
  )
}

export async function getExistingPushSubscription(
  context?: PushOperationContext,
): Promise<PushSubscription | null> {
  if (!browserPushSupported()) return null
  const registration = typeof navigator.serviceWorker.getRegistration === 'function'
    ? await waitForPushBrowserRead(
      navigator.serviceWorker.getRegistration(),
      'Service worker lookup timed out',
      context,
    )
    : await waitForPushBrowserRead(
      navigator.serviceWorker.ready,
      'Service worker lookup timed out',
      context,
    )
  if (!registration) return null
  return waitForPushBrowserRead(
    registration.pushManager.getSubscription(),
    'Browser push subscription lookup timed out',
    context,
  )
}

export async function verifiedUnsubscribePush(
  subscription: PushSubscription,
  context?: PushOperationContext,
): Promise<void> {
  let unsubscribeResult = false
  let unsubscribeError: unknown = null
  try {
    const operation = subscription.unsubscribe()
    unsubscribeResult = context
      ? await waitForPushBrowserMutation(
        operation,
        'Browser push removal timed out; retry securely',
        context,
      )
      : await operation
  } catch (error) {
    unsubscribeError = error
  }

  const remaining = await getExistingPushSubscription(context)
  if (!remaining) return
  if (remaining && remaining.endpoint === subscription.endpoint) {
    if (unsubscribeError) throw unsubscribeError
    if (!unsubscribeResult) throw new Error('The browser did not remove its push subscription')
    throw new Error('The browser push subscription is still active')
  }
  if (remaining) throw new Error('A different browser push subscription appeared during cleanup')
}

export async function unsubscribeLocalPush(context?: PushOperationContext): Promise<void> {
  const subscription = await getExistingPushSubscription(context)
  if (subscription) await verifiedUnsubscribePush(subscription, context)
}

export function decodeApplicationServerKey(value: string): Uint8Array {
  const padding = '='.repeat((4 - (value.length % 4)) % 4)
  const base64 = (value + padding).replace(/-/g, '+').replace(/_/g, '/')
  return Uint8Array.from(atob(base64), (character) => character.charCodeAt(0))
}

export function subscriptionUsesKey(
  subscription: PushSubscription,
  publicKey: string,
): boolean {
  const currentKey = subscription.options.applicationServerKey
  if (!currentKey) return false
  const currentBytes = new Uint8Array(currentKey)
  const expectedBytes = decodeApplicationServerKey(publicKey)
  return (
    currentBytes.length === expectedBytes.length
    && currentBytes.every((byte, index) => byte === expectedBytes[index])
  )
}
