import React, { useCallback, useEffect, useRef, useState } from 'react'
import { fetchAuthToken, fetchPushSubscriptionStatus, setAuthToken } from '@/services/api'
import {
  beginPushSessionTransition,
  getExistingPushSubscription,
  pushSessionIsCurrent,
  runPushOperation,
  unsubscribeLocalPush,
  verifiedUnsubscribePush,
} from '@/services/browserPush'
import { useAlertStore } from '@/store'
import LoadingScreen from '@/components/ui/LoadingScreen'
import LoginPage from './LoginPage'
import RegisterPage from './RegisterPage'

type AuthView = 'loading' | 'login' | 'register' | 'authenticated' | 'activation_error'

type RetryAction =
  | { kind: 'activate'; token: string; remember: boolean }
  | { kind: 'cleanup' }

const TOKEN_KEY = 'auth_token'

/**
 * AuthGuard wraps the entire application and gates access behind authentication.
 *
 * Flow:
 *  1. On mount, attempt to restore a persisted token (or fetch a demo token).
 *  2. If restoration fails → show login page.
 *  3. On login/register success → show the main app (children).
 *  4. Listens for 401 events from the API layer to reset auth state.
 *
 * NOTE: The current backend uses a simple demo token. This guard is forward-
 * compatible with a real JWT login endpoint — just update handleLogin to call
 * POST /api/auth/token.
 */
export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const [view, setView] = useState<AuthView>('loading')
  const [activationError, setActivationError] = useState<string | null>(null)
  const [retryAction, setRetryAction] = useState<RetryAction | null>(null)
  const activationVersionRef = useRef(0)

  const resetSession = useCallback(() => {
    useAlertStore.getState().resetForAuthTransition()
    setAuthToken(null)
    localStorage.removeItem(TOKEN_KEY)
  }, [])

  const activateSession = useCallback(async (
    token: string,
    remember: boolean,
  ): Promise<boolean> => {
    const pushGeneration = beginPushSessionTransition()
    const version = ++activationVersionRef.current
    setView('loading')
    setActivationError(null)
    setRetryAction({ kind: 'activate', token, remember })
    useAlertStore.getState().resetForAuthTransition()
    setAuthToken(null)

    try {
      await runPushOperation(pushGeneration, async (context) => {
        const { assertCurrent, signal } = context
        const subscription = await getExistingPushSubscription(context)
        assertCurrent()
        if (subscription) {
          const ownership = await fetchPushSubscriptionStatus(subscription.endpoint, token, signal)
          assertCurrent()
          if (!ownership.registered) {
            await verifiedUnsubscribePush(subscription, context)
            assertCurrent()
          }
        }
      })

      if (version !== activationVersionRef.current || !pushSessionIsCurrent(pushGeneration)) {
        return false
      }
      setAuthToken(token, remember)
      setRetryAction(null)
      setView('authenticated')
      return true
    } catch (error) {
      if (version !== activationVersionRef.current) return false
      resetSession()
      setActivationError(error instanceof Error
        ? error.message
        : 'Notification ownership could not be verified')
      setView('activation_error')
      return false
    }
  }, [resetSession])

  const cleanupUnauthorizedSession = useCallback(async () => {
    const pushGeneration = beginPushSessionTransition()
    const version = ++activationVersionRef.current
    resetSession()
    setView('loading')
    setActivationError(null)
    setRetryAction({ kind: 'cleanup' })
    try {
      await runPushOperation(pushGeneration, async (context) => {
        const { assertCurrent } = context
        await unsubscribeLocalPush(context)
        assertCurrent()
      })
      if (version !== activationVersionRef.current || !pushSessionIsCurrent(pushGeneration)) return
      setRetryAction(null)
      setView('login')
    } catch (error) {
      if (version !== activationVersionRef.current) return
      setActivationError(error instanceof Error
        ? error.message
        : 'Notifications from the previous session could not be cleared')
      setView('activation_error')
    }
  }, [resetSession])

  useEffect(() => {
    const bootstrap = async () => {
      const stored = localStorage.getItem(TOKEN_KEY)
      if (stored) {
        await activateSession(stored, true)
        return
      }

      try {
        const { access_token } = await fetchAuthToken()
        await activateSession(access_token, Boolean(localStorage.getItem('remember_me')))
      } catch {
        setView('login')
      }
    }
    void bootstrap()
  }, [activateSession])

  // Listen for 401 events emitted by the API layer
  useEffect(() => {
    const handle401 = () => {
      void cleanupUnauthorizedSession()
    }
    window.addEventListener('api:unauthorized', handle401)
    return () => window.removeEventListener('api:unauthorized', handle401)
  }, [cleanupUnauthorizedSession])

  const handleLogin = (token: string) => {
    void activateSession(token, Boolean(localStorage.getItem('remember_me')))
  }

  const retryActivation = () => {
    if (retryAction?.kind === 'activate') {
      void activateSession(retryAction.token, retryAction.remember)
    } else if (retryAction?.kind === 'cleanup') {
      void cleanupUnauthorizedSession()
    }
  }

  if (view === 'loading') {
    return <LoadingScreen message="Authenticating…" />
  }

  if (view === 'login') {
    return (
      <LoginPage
        onLogin={handleLogin}
        onShowRegister={() => setView('register')}
      />
    )
  }

  if (view === 'register') {
    return (
      <RegisterPage
        onShowLogin={() => setView('login')}
      />
    )
  }

  if (view === 'activation_error') {
    return (
      <div className="min-h-screen flex items-center justify-center p-4" style={{ background: 'var(--bg-primary)' }}>
        <div className="card w-full max-w-md rounded-2xl p-6" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
          <h1 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>
            Notification cleanup required
          </h1>
          <p className="mt-2 text-sm" role="alert" style={{ color: 'var(--danger)' }}>
            {activationError ?? 'The previous notification session could not be cleared.'}
          </p>
          <p className="mt-2 text-xs" style={{ color: 'var(--text-secondary)' }}>
            Private application data stays locked until this browser is safe for the current account.
          </p>
          <button
            type="button"
            onClick={retryActivation}
            className="mt-5 rounded-xl px-4 py-2 text-sm font-semibold text-white"
            style={{ background: 'var(--accent)' }}
          >
            Retry securely
          </button>
        </div>
      </div>
    )
  }

  return <>{children}</>
}
