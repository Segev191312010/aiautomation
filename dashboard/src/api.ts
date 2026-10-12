/** Minimal client for the local API: bootstrap token exchange plus bearer GETs. */

const TOKEN_KEY = 'autotrader.live.token'

export class ApiError extends Error {
  readonly status: number
  readonly retryAfterSeconds: number | null

  constructor(status: number, message: string, retryAfterSeconds: number | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.retryAfterSeconds = retryAfterSeconds
  }
}

export function loadToken(): string | null {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function saveToken(token: string): void {
  try {
    window.sessionStorage.setItem(TOKEN_KEY, token)
  } catch {
    // Storage can be unavailable (private mode); the token then lives only in memory.
  }
}

export function clearToken(): void {
  try {
    window.sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    // Nothing persisted, nothing to clear.
  }
}

async function readError(response: Response): Promise<ApiError> {
  let detail = ''
  try {
    const body: unknown = await response.json()
    if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
      detail = body.detail
    }
  } catch {
    // Non-JSON error body (for example the dev proxy when the API is down).
  }
  const retryHeader = Number(response.headers.get('Retry-After'))
  const retryAfter = Number.isFinite(retryHeader) && retryHeader > 0 ? retryHeader : null
  return new ApiError(response.status, detail || `HTTP ${response.status}`, retryAfter)
}

/** Exchange the operator's bootstrap secret for a bearer token. The secret is never stored. */
export async function requestToken(secret: string, signal?: AbortSignal): Promise<string> {
  const response = await fetch('/api/auth/token', {
    method: 'POST',
    headers: { 'X-Bootstrap-Secret': secret, Accept: 'application/json' },
    signal,
  })
  if (!response.ok) throw await readError(response)
  const body: unknown = await response.json()
  if (body && typeof body === 'object' && 'access_token' in body && typeof body.access_token === 'string') {
    return body.access_token
  }
  throw new ApiError(response.status, 'Token response did not contain an access token')
}

export async function getJson(path: string, token: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(path, {
    headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' },
    cache: 'no-store',
    signal,
  })
  if (!response.ok) throw await readError(response)
  return response.json()
}

export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status >= 500 && error.message === `HTTP ${error.status}`) {
      return `API unavailable (HTTP ${error.status}). Is the backend running on 127.0.0.1:8000?`
    }
    return error.message
  }
  if (error instanceof TypeError) {
    return 'Cannot reach the API. Is the backend running on 127.0.0.1:8000?'
  }
  return error instanceof Error ? error.message : 'Unexpected error'
}
