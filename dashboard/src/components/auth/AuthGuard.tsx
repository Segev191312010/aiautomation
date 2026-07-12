import React, { useEffect } from 'react'
import { useSessionStore } from '@/store/sessionStore'
import LoadingScreen from '@/components/ui/LoadingScreen'
import SessionExpired from '@/components/ui/SessionExpired'

interface Props {
  children: React.ReactNode
  onRetry: () => void | Promise<void>
}

/** Mount the trading workspace only while an in-memory session is current. */
export default function AuthGuard({ children, onRetry }: Props) {
  const status = useSessionStore((s) => s.status)
  const token = useSessionStore((s) => s.token)
  const expiresAt = useSessionStore((s) => s.expiresAt)
  const error = useSessionStore((s) => s.error)
  const reset = useSessionStore((s) => s.reset)

  useEffect(() => {
    if (status !== 'authenticated' || !expiresAt) return undefined
    const remaining = Date.parse(expiresAt) - Date.now()
    const expire = () => {
      reset('Your session expired. Reconnect to continue.')
      window.history.replaceState(window.history.state, '', '/session-expired')
      window.dispatchEvent(new PopStateEvent('popstate'))
    }
    if (!Number.isFinite(remaining) || remaining <= 0) {
      expire()
      return undefined
    }
    const timer = window.setTimeout(expire, remaining)
    return () => window.clearTimeout(timer)
  }, [expiresAt, reset, status])

  const sessionCurrent = Boolean(
    status === 'authenticated' &&
    token &&
    expiresAt &&
    Date.parse(expiresAt) > Date.now(),
  )
  if (sessionCurrent) return <>{children}</>
  if (status === 'authenticated') {
    return (
      <SessionExpired
        bootstrapFailed={false}
        message="Your session is invalid or expired. Reconnect to continue."
        onRetry={onRetry}
      />
    )
  }
  if (status === 'expired' || status === 'failed') {
    return (
      <SessionExpired
        bootstrapFailed={status === 'failed'}
        message={error ?? 'No active session is available.'}
        onRetry={onRetry}
      />
    )
  }
  return <LoadingScreen message="Establishing secure session…" />
}
