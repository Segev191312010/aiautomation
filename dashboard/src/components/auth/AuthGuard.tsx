import React, { useEffect, useState } from 'react'
import { fetchAuthToken, setAuthToken, getAuthToken } from '@/services/api'
import LoadingScreen from '@/components/ui/LoadingScreen'
import LoginPage from './LoginPage'
import RegisterPage from './RegisterPage'

type AuthView = 'loading' | 'login' | 'register' | 'authenticated'

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

  const bootstrap = async () => {
    // 1. Check for a stored token from a previous session
    const stored = localStorage.getItem(TOKEN_KEY)
    if (stored) {
      setAuthToken(stored)
      setView('authenticated')
      return
    }

    // 2. Try to fetch a demo token (works when backend is up)
    try {
      const { access_token } = await fetchAuthToken()
      setAuthToken(access_token)
      // Store only if "remember me" was previously set
      if (localStorage.getItem('remember_me')) {
        localStorage.setItem(TOKEN_KEY, access_token)
      }
      setView('authenticated')
    } catch {
      // Backend offline or real auth required → show login
      setView('login')
    }
  }

  useEffect(() => {
    bootstrap()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Listen for 401 events emitted by the API layer
  useEffect(() => {
    const handle401 = () => {
      setAuthToken(null)
      localStorage.removeItem(TOKEN_KEY)
      setView('login')
    }
    window.addEventListener('api:unauthorized', handle401)
    return () => window.removeEventListener('api:unauthorized', handle401)
  }, [])

  const handleLogin = (token: string) => {
    if (localStorage.getItem('remember_me')) {
      localStorage.setItem(TOKEN_KEY, token)
    }
    setView('authenticated')
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

  return <>{children}</>
}
