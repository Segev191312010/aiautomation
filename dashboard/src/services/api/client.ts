/**
 * Shared HTTP transport layer — fetch wrapper, auth token, error handling.
 * All domain modules import { get, post, put, del } from here.
 */

import { useSessionStore } from '@/store/sessionStore'


export const BASE = ''  // same origin in prod; Vite proxy handles /api in dev

/** Compatibility helper for non-React transports such as WebSocket clients. */
export function getAuthToken() {
  const session = useSessionStore.getState()
  const expiry = session.expiresAt ? Date.parse(session.expiresAt) : Number.NaN
  if (session.status === 'authenticated' && session.token && expiry > Date.now()) {
    return session.token
  }
  if (session.token) {
    session.reset('Your session expired. Reconnect to continue.')
    routeToSessionExpired()
  }
  return null
}

const PUBLIC_API_PATHS = new Set(['/api/health', '/api/status', '/api/session/bootstrap'])

function routeToSessionExpired() {
  if (typeof window === 'undefined') return
  if (window.location.pathname !== '/session-expired') {
    window.history.replaceState(window.history.state, '', '/session-expired')
    window.dispatchEvent(new PopStateEvent('popstate'))
  }
  window.dispatchEvent(new Event('api:unauthorized'))
}

export async function reqWithStatus<T>(method: string, path: string, body?: unknown, acceptedStatuses: number[] = []): Promise<{ status: number; data: T }> {
  const headers: Record<string, string> = {}
  if (body) headers['Content-Type'] = 'application/json'
  const isPublic = PUBLIC_API_PATHS.has(path)
  const session = useSessionStore.getState()
  const requestGeneration = session.generation
  const requestToken = session.token
  if (!isPublic) {
    const expiry = session.expiresAt ? Date.parse(session.expiresAt) : Number.NaN
    if (session.status !== 'authenticated' || !session.token || expiry <= Date.now()) {
      session.reset('Your session is unavailable or expired.')
      routeToSessionExpired()
      throw new Error(`${method} ${path} blocked: no active session`)
    }
    headers['Authorization'] = `Bearer ${session.token}`
  }

  const resp = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })

  if (!isPublic) {
    const current = useSessionStore.getState()
    if (current.generation !== requestGeneration || current.token !== requestToken) {
      throw new Error(`${method} ${path} ignored: session changed during request`)
    }
  }

  if (!resp.ok && !acceptedStatuses.includes(resp.status)) {
    if (resp.status === 401 || resp.status === 403) {
      useSessionStore.getState().reset(
        resp.status === 401
          ? 'The backend rejected this session.'
          : 'This session is not authorized for the requested operation.',
      )
      routeToSessionExpired()
    }
    const text = await resp.text().catch(() => resp.statusText)
    throw new Error(`${method} ${path} → ${resp.status}: ${text}`)
  }
  const text = await resp.text()
  if (!isPublic) {
    const current = useSessionStore.getState()
    if (current.generation !== requestGeneration || current.token !== requestToken) {
      throw new Error(`${method} ${path} ignored: session changed while reading response`)
    }
  }
  const data = (text ? JSON.parse(text) : undefined) as T
  return { status: resp.status, data }
}

export async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const result = await reqWithStatus<T>(method, path, body)
  return result.data
}

export const get  = <T>(p: string)            => req<T>('GET',    p)
export const post = <T>(p: string, b?: unknown) => req<T>('POST', p, b)
export const postWithStatus = <T>(p: string, b: unknown, acceptedStatuses: number[] = [202, 409]) => reqWithStatus<T>('POST', p, b, acceptedStatuses)
export const put  = <T>(p: string, b?: unknown) => req<T>('PUT',  p, b)
export const del  = <T>(p: string)            => req<T>('DELETE', p)
