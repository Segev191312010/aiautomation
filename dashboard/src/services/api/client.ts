/**
 * Shared HTTP transport layer — fetch wrapper, auth token, error handling.
 * All domain modules import { get, post, put, del } from here.
 */

export const BASE = ''  // same origin in prod; Vite proxy handles /api in dev

// ── API failure telemetry ─────────────────────────────────────────────────────
// Lightweight event bus so the dashboard can surface API health without
// requiring every component to wire its own error tracking.

export type ApiFailureEvent = {
  method: string
  path: string
  status: number
  message: string
  timestamp: number
}

export interface ApiRequestOptions {
  signal?: AbortSignal
}

type ApiFailureListener = (ev: ApiFailureEvent) => void
const _failureListeners = new Set<ApiFailureListener>()

export function onApiFailure(fn: ApiFailureListener): () => void {
  _failureListeners.add(fn)
  return () => { _failureListeners.delete(fn) }
}

function _emitApiFailure(ev: ApiFailureEvent) {
  for (const fn of _failureListeners) fn(ev)
}

// ── Auth token storage ────────────────────────────────────────────────────────
const AUTH_TOKEN_KEY = 'auth_token'
let _sessionToken: string | null = localStorage.getItem(AUTH_TOKEN_KEY)

export function setAuthToken(token: string | null, persist = true) {
  _sessionToken = token
  if (token && persist) localStorage.setItem(AUTH_TOKEN_KEY, token)
  else localStorage.removeItem(AUTH_TOKEN_KEY)
  if (token) _bootstrapResolve?.(token)
}
export function getAuthToken() { return _sessionToken }

// Bootstrap gate — requests block here until a token is available on first load.
// Prevents the initial store fetches from firing before AuthGuard's fetchAuthToken
// completes, which was causing the cascade of 401s on /api/account, /api/positions,
// /api/autopilot/*. Once bootstrap has completed once, subsequent empty storage
// means we've been logged out — we must NOT re-use the resolved promise value.
let _bootstrapResolve: ((token: string) => void) | null = null
let _bootstrapDone = false
const _bootstrapPromise: Promise<string> = new Promise(resolve => {
  const existing = _sessionToken
  if (existing) {
    _bootstrapDone = true
    resolve(existing)
  } else {
    _bootstrapResolve = (token: string) => { _bootstrapDone = true; resolve(token) }
  }
})

async function _waitForToken(): Promise<string | null> {
  const existing = _sessionToken
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

async function sendRequest<T>(
  method: string,
  path: string,
  body: unknown,
  token: string | null,
  handleUnauthorized: boolean,
  options: ApiRequestOptions,
): Promise<T> {
  const headers: Record<string, string> = {}
  if (body) headers['Content-Type'] = 'application/json'
  if (token) headers['Authorization'] = `Bearer ${token}`

  const resp = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    signal: options.signal,
  })

  if (!resp.ok) {
    if (resp.status === 401 && handleUnauthorized) {
      setAuthToken(null)
      window.dispatchEvent(new Event('api:unauthorized'))
    }
    const text = await resp.text().catch(() => resp.statusText)
    const err = new Error(`${method} ${path} → ${resp.status}: ${text}`)
    ;(err as Error & { status: number }).status = resp.status
    // Emit telemetry for non-401 failures (401s are auth lifecycle, not API bugs)
    if (resp.status !== 401) {
      _emitApiFailure({ method, path, status: resp.status, message: text, timestamp: Date.now() })
    }
    throw err
  }
  return resp.json() as Promise<T>
}

export async function req<T>(
  method: string,
  path: string,
  body?: unknown,
  options: ApiRequestOptions = {},
): Promise<T> {
  const token = path === '/api/auth/token' ? null : await _waitForToken()
  return sendRequest<T>(method, path, body, token, true, options)
}

export function reqWithAuthToken<T>(
  method: string,
  path: string,
  token: string,
  body?: unknown,
  options: ApiRequestOptions = {},
): Promise<T> {
  return sendRequest<T>(method, path, body, token, false, options)
}

export const get  = <T>(p: string, options?: ApiRequestOptions) => req<T>('GET', p, undefined, options)
export const post = <T>(p: string, b?: unknown, options?: ApiRequestOptions) => req<T>('POST', p, b, options)
export const put  = <T>(p: string, b?: unknown, options?: ApiRequestOptions) => req<T>('PUT', p, b, options)
export const del  = <T>(p: string, b?: unknown, options?: ApiRequestOptions) => req<T>('DELETE', p, b, options)
export const postWithAuthToken = <T>(
  p: string,
  token: string,
  b?: unknown,
  options?: ApiRequestOptions,
) => reqWithAuthToken<T>('POST', p, token, b, options)
