/**
 * Auth bootstrap — mints a session JWT from the loopback dev bootstrap endpoint.
 *
 * The bootstrap secret is supplied via VITE_JWT_BOOTSTRAP_SECRET (dev / personal
 * use only). Server-side the /api/auth/token endpoint is loopback-only and
 * refuses to issue tokens while AUTOPILOT_MODE=LIVE, so the secret living in the
 * local dev env is an acceptable convenience, not a production auth flow.
 */
export interface AuthTokenResponse {
  access_token: string
  token_type: string
}

export async function fetchAuthToken(): Promise<AuthTokenResponse> {
  const resp = await fetch('/api/auth/token', {
    method: 'POST',
    headers: { 'X-Bootstrap-Secret': import.meta.env.VITE_JWT_BOOTSTRAP_SECRET ?? '' },
  })
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText)
    throw new Error(`POST /api/auth/token → ${resp.status}: ${text}`)
  }
  return resp.json() as Promise<AuthTokenResponse>
}
