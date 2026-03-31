/**
 * Shared HTTP transport layer — fetch wrapper, auth token, error handling.
 * All domain modules import { get, post, put, del } from here.
 */

export const BASE = ''  // same origin in prod; Vite proxy handles /api in dev

// Auth token storage — demo token bootstrapped on app init
let _authToken: string | null = null
export function setAuthToken(token: string | null) { _authToken = token }
export function getAuthToken() { return _authToken }

export async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  if (body) headers['Content-Type'] = 'application/json'
  if (_authToken) headers['Authorization'] = `Bearer ${_authToken}`

  const resp = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })

  // 401 → clear token (prep for Stage 8 login redirect)
  if (resp.status === 401) {
    _authToken = null
  }

  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText)
    throw new Error(`${method} ${path} → ${resp.status}: ${text}`)
  }
  return resp.json() as Promise<T>
}

export const get  = <T>(p: string)            => req<T>('GET',    p)
export const post = <T>(p: string, b?: unknown) => req<T>('POST', p, b)
export const put  = <T>(p: string, b?: unknown) => req<T>('PUT',  p, b)
export const del  = <T>(p: string)            => req<T>('DELETE', p)
