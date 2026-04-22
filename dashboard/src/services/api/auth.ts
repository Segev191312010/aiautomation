import type { User } from '@/types'
import { get } from './client'

export const fetchAuthToken = async () => {
  const resp = await fetch('/api/auth/token', {
    method: 'POST',
    headers: {
      'X-Bootstrap-Secret': import.meta.env.VITE_JWT_BOOTSTRAP_SECRET ?? '',
    },
  })
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText)
    throw new Error(`POST /api/auth/token → ${resp.status}: ${text}`)
  }
  return resp.json() as Promise<{ access_token: string; token_type: string }>
}
export const fetchAuthMe    = () => get<User>('/api/auth/me')
