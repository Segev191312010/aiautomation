import type {
  Alert,
  AlertCreate,
  AlertHistory,
  AlertStats,
  AlertTestResult,
  AlertUpdate,
  NotificationPrefs,
  PushStatus,
  PushSubscriptionResult,
  PushSubscriptionStatus,
  PushTestResult,
} from '@/types'
import { get, post, postWithAuthToken, put, del } from './client'

export const fetchAlerts       = () => get<Alert[]>('/api/alerts')
export const fetchAlert        = (id: string) => get<Alert>(`/api/alerts/${id}`)
export const createAlert       = (body: AlertCreate) => post<Alert>('/api/alerts', body)
export const updateAlert       = (id: string, body: AlertUpdate) => put<Alert>(`/api/alerts/${id}`, body)
export const deleteAlert       = (id: string) => del<{ deleted: boolean }>(`/api/alerts/${id}`)
export const toggleAlert       = (id: string) => post<{ id: string; enabled: boolean }>(`/api/alerts/${id}/toggle`)
export const fetchAlertHistory = (limit = 100) => get<AlertHistory[]>(`/api/alerts/history?limit=${limit}`)
export const testAlertNotification = (body: AlertCreate) => post<AlertTestResult>('/api/alerts/test', body)
/** Backend route /api/alerts/stats is not yet implemented.
 *  The alertStore falls back to client-side computation from history data. */
export const fetchAlertStats   = () => get<AlertStats>('/api/alerts/stats')

export const subscribePush = (subscription: PushSubscriptionJSON, signal?: AbortSignal) =>
  post<PushSubscriptionResult>('/api/push/subscribe', subscription, { signal })

export const unsubscribePush = (endpoint: string, signal?: AbortSignal) =>
  del<PushSubscriptionResult>('/api/push/subscribe', { endpoint }, { signal })

export const fetchPushSubscriptionStatus = (
  endpoint: string,
  token?: string,
  signal?: AbortSignal,
) =>
  token
    ? postWithAuthToken<PushSubscriptionStatus>('/api/push/subscription/status', token, { endpoint }, { signal })
    : post<PushSubscriptionStatus>('/api/push/subscription/status', { endpoint }, { signal })

export const fetchPushStatus = (signal?: AbortSignal) =>
  get<PushStatus>('/api/push/status', { signal })

export const fetchPushPreferences = () =>
  get<NotificationPrefs>('/api/push/preferences')

export const updatePushPreferences = (partial: Partial<NotificationPrefs>) =>
  put<NotificationPrefs>('/api/push/preferences', partial)

export const testPushNotification = (signal?: AbortSignal) =>
  post<PushTestResult>('/api/push/test', undefined, { signal })
