/** Minimal client for the local API: bootstrap token exchange plus bearer GETs. */
import type {
  ManagerCurrentPlanView,
  ManagerPlanHistoryView,
  ManagerSpendView,
  ManagerStatusView,
  ManagerUnavailable,
  ManagerVerdictsView,
} from './types'

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

// ---------- the AI day manager (read-only; there is no POST) ----------

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** `{ available: false }` while migration 16 is pending; anything else is the caller's to check. */
function asUnavailable(value: unknown): ManagerUnavailable | null {
  if (isObject(value) && value.available === false) {
    return {
      available: false,
      reason: typeof value.reason === 'string' ? value.reason : 'unavailable',
      detail: typeof value.detail === 'string' ? value.detail : '',
    }
  }
  return null
}

function unexpected(label: string): Error {
  return new Error(`Unexpected manager ${label} response`)
}

export async function getManagerStatus(token: string, signal?: AbortSignal): Promise<ManagerStatusView> {
  const value = await getJson('/api/live/manager/status', token, signal)
  const unavailable = asUnavailable(value)
  if (unavailable) return unavailable
  if (
    isObject(value) &&
    value.available === true &&
    isObject(value.switch) &&
    (value.switch.state === 'on' || value.switch.state === 'off') &&
    isObject(value.plans) &&
    isObject(value.spend) &&
    isObject(value.day_universe) &&
    isObject(value.competing_login) &&
    isObject(value.migrations)
  ) {
    return value as ManagerStatusView
  }
  throw unexpected('status')
}

export async function getManagerPlan(token: string, signal?: AbortSignal): Promise<ManagerCurrentPlanView> {
  const value = await getJson('/api/live/manager/plan', token, signal)
  const unavailable = asUnavailable(value)
  if (unavailable) return unavailable
  if (isObject(value) && value.available === true && typeof value.in_force === 'boolean') {
    const plan = value.plan
    if (plan === null || (isObject(plan) && Array.isArray(plan.strategies) && Array.isArray(plan.focus))) {
      return value as ManagerCurrentPlanView
    }
  }
  throw unexpected('plan')
}

export async function getManagerPlans(token: string, signal?: AbortSignal): Promise<ManagerPlanHistoryView> {
  const value = await getJson('/api/live/manager/plans', token, signal)
  const unavailable = asUnavailable(value)
  if (unavailable) return unavailable
  if (isObject(value) && value.available === true && Array.isArray(value.plans)) {
    return { ...value, plans: value.plans.filter(isObject) } as ManagerPlanHistoryView
  }
  throw unexpected('plans')
}

export async function getManagerVerdicts(token: string, signal?: AbortSignal): Promise<ManagerVerdictsView> {
  const value = await getJson('/api/live/manager/verdicts', token, signal)
  const unavailable = asUnavailable(value)
  if (unavailable) return unavailable
  if (isObject(value) && value.available === true && Array.isArray(value.verdicts)) {
    return { ...value, verdicts: value.verdicts.filter(isObject) } as ManagerVerdictsView
  }
  throw unexpected('verdicts')
}

export async function getManagerSpend(token: string, signal?: AbortSignal): Promise<ManagerSpendView> {
  const value = await getJson('/api/live/manager/spend', token, signal)
  const unavailable = asUnavailable(value)
  if (unavailable) return unavailable
  if (isObject(value) && value.available === true && typeof value.used_pct === 'number' && typeof value.budget_usd === 'string') {
    return value as ManagerSpendView
  }
  throw unexpected('spend')
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
