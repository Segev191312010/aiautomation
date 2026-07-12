import { create } from 'zustand'
import { resetAllStores } from './resetAllStores'

export type SessionStatus = 'idle' | 'bootstrapping' | 'authenticated' | 'expired' | 'failed'

export interface SessionCredentials {
  accessToken: string
  expiresAt: string
}

interface SessionState {
  token: string | null
  expiresAt: string | null
  generation: number
  status: SessionStatus
  error: string | null
  beginBootstrap: () => void
  setSession: (session: SessionCredentials) => void
  failBootstrap: (message: string) => void
  reset: (message?: string) => void
}

const EMPTY_SESSION = {
  token: null,
  expiresAt: null,
} as const

export const useSessionStore = create<SessionState>((set) => ({
  ...EMPTY_SESSION,
  generation: 0,
  status: 'idle',
  error: null,

  beginBootstrap: () => set((state) => ({
    ...EMPTY_SESSION,
    generation: state.generation + 1,
    status: 'bootstrapping',
    error: null,
  })),

  setSession: ({ accessToken, expiresAt }) => {
    const expiry = Date.parse(expiresAt)
    if (!accessToken || !Number.isFinite(expiry) || expiry <= Date.now()) {
      resetAllStores()
      set((state) => ({
        ...EMPTY_SESSION,
        generation: state.generation + 1,
        status: 'failed',
        error: 'The backend returned an invalid or expired session.',
      }))
      return
    }
    set((state) => ({
      token: accessToken,
      expiresAt,
      generation: state.generation + 1,
      status: 'authenticated',
      error: null,
    }))
  },

  failBootstrap: (message) => {
    resetAllStores()
    set((state) => ({
      ...EMPTY_SESSION,
      generation: state.generation + 1,
      status: 'failed',
      error: message,
    }))
  },

  reset: (message = 'Your session expired. Reconnect to continue.') => {
    resetAllStores()
    set((state) => ({
      ...EMPTY_SESSION,
      generation: state.generation + 1,
      status: 'expired',
      error: message,
    }))
  },
}))

export function sessionIsCurrent(): boolean {
  const { token, expiresAt, status } = useSessionStore.getState()
  return Boolean(
    status === 'authenticated' &&
    token &&
    expiresAt &&
    Date.parse(expiresAt) > Date.now(),
  )
}
