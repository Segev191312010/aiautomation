/**
 * Shared HTTP transport layer — fetch wrapper, auth token, error handling.
 * All domain modules import { get, post, put, del } from here.
 */

export const BASE = ''  // same origin in prod; Vite proxy handles /api in dev

// Auth token storage — persisted to localStorage so in-flight 401s can't strand it
const AUTH_TOKEN_KEY = 'auth_token'
export function setAuthToken(token: string | null) {
  if (token) localStorage.setItem(AUTH_TOKEN_KEY, token)
  else localStorage.removeItem(AUTH_TOKEN_KEY)
  if (token) _bootstrapResolve?.(token)
}
export function getAuthToken() { return localStorage.getItem(AUTH_TOKEN_KEY) }

// Bootstrap gate — requests block here until a token is available on first load.
// Prevents the initial store fetches from firing before AuthGuard's fetchAuthToken
// completes, which was causing the cascade of 401s on /api/account, /api/positions,
// /api/autopilot/*. Once bootstrap has completed once, subsequent empty storage
// means we've been logged out — we must NOT re-use the resolved promise value.
let _bootstrapResolve: ((token: string) => void) | null = null
let _bootstrapDone = false
const _bootstrapPromise: Promise<string> = new Promise(resolve => {
  const existing = localStorage.getItem(AUTH_TOKEN_KEY)
  if (existing) {
    _bootstrapDone = true
    resolve(existing)
  } else {
    _bootstrapResolve = (token: string) => { _bootstrapDone = true; resolve(token) }
  }
})

async function _waitForToken(): Promise<string | null> {
  const existing = localStorage.getItem(AUTH_TOKEN_KEY)
  if (existing) return existing
  // Post-bootstrap with empty storage = logged-out state. Do not fall through
  // to the already-resolved bootstrap promise, which still holds the stale
  // initial token.
  if (_bootstrapDone) return null
  // Race the bootstrap promise against a 5s timeout so a missing bootstrap
  // doesn't hang the app forever.
  return Promise.race([
    _bootstrapPromise,
    new Promise<null>(resolve => setTimeout(() => resolve(null), 5000)),
  ])
}

export async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  if (body) headers['Content-Type'] = 'application/json'
  const token = path === '/api/auth/token' ? null : await _waitForToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const resp = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })

  if (!resp.ok) {
    if (resp.status === 401) {
      localStorage.removeItem(AUTH_TOKEN_KEY)
      window.dispatchEvent(new Event('api:unauthorized'))
    }
    const text = await resp.text().catch(() => resp.statusText)
    throw new Error(`${method} ${path} → ${resp.status}: ${text}`)
  }
  return resp.json() as Promise<T>
}

export const get  = <T>(p: string)            => req<T>('GET',    p)
export const post = <T>(p: string, b?: unknown) => req<T>('POST', p, b)
export const put  = <T>(p: string, b?: unknown) => req<T>('PUT',  p, b)
export const del  = <T>(p: string)            => req<T>('DELETE', p)
