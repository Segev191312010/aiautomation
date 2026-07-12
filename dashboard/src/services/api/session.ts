import { post } from './client'

export interface SessionBootstrapResponse {
  access_token: string
  token_type: 'bearer'
  expires_at: string
  expires_in_seconds: number
}

declare global {
  interface Window {
    __TRADEBOT_SESSION_BOOTSTRAP__?: string
  }
}

const FRAGMENT_KEY = 'session-bootstrap'
let runtimeLaunchToken: string | undefined
let inFlightBootstrap: Promise<SessionBootstrapResponse> | null = null

function consumeRuntimeLaunchToken(): string | undefined {
  if (runtimeLaunchToken) return runtimeLaunchToken
  if (typeof window === 'undefined') return undefined

  const injected = window.__TRADEBOT_SESSION_BOOTSTRAP__?.trim()
  if (injected) {
    runtimeLaunchToken = injected
    window.__TRADEBOT_SESSION_BOOTSTRAP__ = undefined
    return runtimeLaunchToken
  }

  const fragment = window.location.hash.startsWith('#')
    ? window.location.hash.slice(1)
    : window.location.hash
  const params = new URLSearchParams(fragment)
  const fromFragment = params.get(FRAGMENT_KEY)?.trim()
  if (!fromFragment) return undefined

  runtimeLaunchToken = fromFragment
  params.delete(FRAGMENT_KEY)
  const remaining = params.toString()
  const cleanUrl = `${window.location.pathname}${window.location.search}${remaining ? `#${remaining}` : ''}`
  window.history.replaceState(window.history.state, '', cleanUrl)
  return runtimeLaunchToken
}

async function requestBootstrap(): Promise<SessionBootstrapResponse> {
  const launchToken = consumeRuntimeLaunchToken()
  const session = await post<SessionBootstrapResponse>(
    '/api/session/bootstrap',
    launchToken ? { launch_token: launchToken } : {},
  )
  if (
    !session.access_token ||
    session.token_type !== 'bearer' ||
    !Number.isFinite(Date.parse(session.expires_at)) ||
    Date.parse(session.expires_at) <= Date.now() ||
    session.expires_in_seconds <= 0
  ) {
    throw new Error('Session bootstrap returned an invalid response')
  }
  return session
}

/** Deduplicate React StrictMode startup while keeping retries possible. */
export function bootstrapSession(): Promise<SessionBootstrapResponse> {
  if (inFlightBootstrap) return inFlightBootstrap
  const request = requestBootstrap()
  inFlightBootstrap = request
  void request.finally(() => {
    if (inFlightBootstrap === request) inFlightBootstrap = null
  }).catch(() => undefined)
  return request
}
