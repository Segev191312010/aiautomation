/**
 * useNotifications — browser push notification hook.
 *
 * Tracks Notification API permission state, provides a `request()` function
 * to prompt the user, and subscribes this browser endpoint to the backend
 * push service when permission is granted.
 *
 * Returns:
 *   permission   — 'default' | 'granted' | 'denied' | 'unsupported'
 *   request()    — async function: prompts if needed, returns true if granted
 *   notify()     — imperatively show a local browser notification
 */
import { useState, useCallback, useEffect } from 'react'
import { subscribePush } from '@/services/api'

// ── Types ─────────────────────────────────────────────────────────────────────

export type NotificationPermission = 'default' | 'granted' | 'denied' | 'unsupported'

export interface UseNotificationsResult {
  permission:  NotificationPermission
  request:     () => Promise<boolean>
  notify:      (title: string, options?: NotificationOptions) => void
  supported:   boolean
}

// ── VAPID public key ──────────────────────────────────────────────────────────
// Set VITE_VAPID_PUBLIC_KEY in your .env file if you have a push server.
// If absent we fall back to basic (non-persistent) browser notifications only.
const VAPID_PUBLIC_KEY = import.meta.env.VITE_VAPID_PUBLIC_KEY as string | undefined

// ── Helpers ───────────────────────────────────────────────────────────────────

function isSupported(): boolean {
  return typeof Notification !== 'undefined'
}

function currentPermission(): NotificationPermission {
  if (!isSupported()) return 'unsupported'
  return Notification.permission as NotificationPermission
}

/** Convert a base64url VAPID key to a Uint8Array. */
function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding    = '='.repeat((4 - (base64String.length % 4)) % 4)
  const base64     = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/')
  const rawData    = atob(base64)
  const outputArray = new Uint8Array(rawData.length)
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i)
  }
  return outputArray
}

/** Subscribe to the push manager and send the endpoint to the backend. */
async function subscribeToPush(): Promise<void> {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) return
  if (!VAPID_PUBLIC_KEY) return

  try {
    const registration = await navigator.serviceWorker.ready
    const existing     = await registration.pushManager.getSubscription()

    const subscription = existing ?? await registration.pushManager.subscribe({
      userVisibleOnly:      true,
      applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY) as BufferSource,
    })

    await subscribePush(subscription.toJSON())
  } catch (err) {
    // Non-fatal: push subscription is best-effort
    console.warn('[useNotifications] push subscribe failed:', err)
  }
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useNotifications(): UseNotificationsResult {
  const [permission, setPermission] = useState<NotificationPermission>(currentPermission)

  // Keep permission state in sync if the user changes it in browser settings
  // (PermissionStatus change events are the reliable way to do this)
  useEffect(() => {
    if (!isSupported() || !navigator.permissions) return

    let status: PermissionStatus | null = null

    navigator.permissions
      .query({ name: 'notifications' as PermissionName })
      .then((s) => {
        status = s
        s.onchange = () => setPermission(currentPermission())
      })
      .catch(() => { /* ignore — API not available in all browsers */ })

    return () => {
      if (status) status.onchange = null
    }
  }, [])

  const request = useCallback(async (): Promise<boolean> => {
    if (!isSupported()) return false
    if (Notification.permission === 'granted') return true
    if (Notification.permission === 'denied')  return false

    const result = await Notification.requestPermission()
    setPermission(result as NotificationPermission)

    if (result === 'granted') {
      await subscribeToPush()
    }

    return result === 'granted'
  }, [])

  const notify = useCallback(
    (title: string, options?: NotificationOptions) => {
      if (!isSupported() || Notification.permission !== 'granted') return
      try {
        new Notification(title, {
          icon: '/favicon.ico',
          ...options,
        })
      } catch {
        // Some browsers restrict Notification outside service worker in certain contexts
      }
    },
    [],
  )

  return {
    permission,
    request,
    notify,
    supported: isSupported(),
  }
}
