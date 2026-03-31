import type {
  Alert,
  AlertCreate,
  AlertHistory,
  AlertStats,
  AlertTestResult,
  AlertUpdate,
} from '@/types'
import { get, post, put, del } from './client'

export const fetchAlerts       = () => get<Alert[]>('/api/alerts')
export const fetchAlert        = (id: string) => get<Alert>(`/api/alerts/${id}`)
export const createAlert       = (body: AlertCreate) => post<Alert>('/api/alerts', body)
export const updateAlert       = (id: string, body: AlertUpdate) => put<Alert>(`/api/alerts/${id}`, body)
export const deleteAlert       = (id: string) => del<{ deleted: boolean }>(`/api/alerts/${id}`)
export const toggleAlert       = (id: string) => post<{ id: string; enabled: boolean }>(`/api/alerts/${id}/toggle`)
export const fetchAlertHistory = (limit = 100) => get<AlertHistory[]>(`/api/alerts/history?limit=${limit}`)
export const testAlertNotification = (body: AlertCreate) => post<AlertTestResult>('/api/alerts/test', body)
export const fetchAlertStats   = () => get<AlertStats>('/api/alerts/stats')

/** Subscribe this browser to Web Push notifications. */
export const subscribePush = (subscription: PushSubscriptionJSON) =>
  post<{ subscribed: boolean }>('/api/push/subscribe', subscription)
