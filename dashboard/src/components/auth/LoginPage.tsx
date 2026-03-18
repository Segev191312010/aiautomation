import React, { useState } from 'react'
import { fetchAuthToken, setAuthToken } from '@/services/api'
import { useToast } from '@/components/ui/ToastProvider'

interface Props {
  onLogin: (token: string) => void
  onShowRegister: () => void
}

/**
 * Centered login form.
 * On success calls onLogin(token); the parent (AuthGuard) stores the token and
 * renders the main app.
 */
export default function LoginPage({ onLogin, onShowRegister }: Props) {
  const toast = useToast()
  const [username,   setUsername]   = useState('')
  const [password,   setPassword]   = useState('')
  const [remember,   setRemember]   = useState(false)
  const [loading,    setLoading]    = useState(false)
  const [error,      setError]      = useState<string | null>(null)
  const [showPwd,    setShowPwd]    = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username.trim() || !password.trim()) {
      setError('Username and password are required.')
      return
    }
    setError(null)
    setLoading(true)
    try {
      // Demo flow: fetchAuthToken ignores credentials; in production this would
      // POST /api/auth/token with { username, password }.
      const { access_token } = await fetchAuthToken()
      setAuthToken(access_token)
      if (remember) {
        localStorage.setItem('remember_me', '1')
      } else {
        localStorage.removeItem('remember_me')
      }
      toast.success('Welcome back!')
      onLogin(access_token)
    } catch {
      setError('Invalid username or password. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  const inputCls =
    'w-full rounded-xl border px-4 py-2.5 text-sm font-sans outline-none ' +
    'transition-all duration-150 bg-[var(--bg-input)] text-[var(--text-primary)] ' +
    'border-[var(--border)] placeholder:text-[var(--text-muted)] ' +
    'focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent)]/20'

  return (
    <div
      className="min-h-screen flex items-center justify-center p-4"
      style={{ background: 'var(--bg-primary)' }}
    >
      <div className="w-full max-w-sm">
        {/* Brand */}
        <div className="flex flex-col items-center gap-3 mb-8">
          <div
            className="flex h-12 w-12 items-center justify-center rounded-2xl"
            style={{ background: 'var(--accent)', boxShadow: '0 0 32px color-mix(in srgb, var(--accent) 30%, transparent)' }}
          >
            <svg viewBox="0 0 24 24" fill="currentColor" className="w-6 h-6 text-white">
              <path d="M3.5 18.49l6-6.01 4 4L22 6.92l-1.41-1.41-7.09 7.97-4-4L2 16.99z" />
            </svg>
          </div>
          <div className="text-center">
            <p className="text-[10px] font-sans uppercase tracking-[0.28em]" style={{ color: 'var(--text-muted)' }}>
              TradeBot
            </p>
            <h1 className="text-xl font-mono font-bold" style={{ color: 'var(--text-primary)' }}>
              Market Desk
            </h1>
          </div>
        </div>

        {/* Card */}
        <div
          className="card rounded-2xl -lg p-6 flex flex-col gap-5"
          style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}
        >
          <div>
            <h2 className="text-sm font-sans font-semibold" style={{ color: 'var(--text-primary)' }}>
              Sign in to your account
            </h2>
            <p className="text-xs font-sans mt-0.5" style={{ color: 'var(--text-secondary)' }}>
              Enter your credentials to continue
            </p>
          </div>

          {/* Error message */}
          {error && (
            <div
              className="flex items-start gap-2.5 rounded-xl px-3.5 py-3 text-xs font-sans"
              style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', color: 'var(--danger)' }}
              role="alert"
            >
              <svg viewBox="0 0 24 24" fill="none" className="w-3.5 h-3.5 mt-0.5 shrink-0" aria-hidden="true">
                <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2" />
                <line x1="12" y1="8" x2="12" y2="12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                <line x1="12" y1="16" x2="12.01" y2="16" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
              </svg>
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
            {/* Username */}
            <div>
              <label className="block text-[11px] font-sans font-medium uppercase tracking-wide mb-1.5"
                style={{ color: 'var(--text-secondary)' }}
                htmlFor="login-username"
              >
                Username
              </label>
              <input
                id="login-username"
                type="text"
                autoComplete="username"
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="demo"
                className={inputCls}
                disabled={loading}
              />
            </div>

            {/* Password */}
            <div>
              <label className="block text-[11px] font-sans font-medium uppercase tracking-wide mb-1.5"
                style={{ color: 'var(--text-secondary)' }}
                htmlFor="login-password"
              >
                Password
              </label>
              <div className="relative">
                <input
                  id="login-password"
                  type={showPwd ? 'text' : 'password'}
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className={`${inputCls} pr-10`}
                  disabled={loading}
                />
                <button
                  type="button"
                  onClick={() => setShowPwd((p) => !p)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 transition-opacity hover:opacity-70"
                  style={{ color: 'var(--text-muted)' }}
                  tabIndex={-1}
                  aria-label={showPwd ? 'Hide password' : 'Show password'}
                >
                  {showPwd ? (
                    <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4" aria-hidden="true">
                      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
                      <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
                      <line x1="1" y1="1" x2="23" y2="23" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
                    </svg>
                  ) : (
                    <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4" aria-hidden="true">
                      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
                      <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.75" />
                    </svg>
                  )}
                </button>
              </div>
            </div>

            {/* Remember me */}
            <label className="flex items-center gap-2.5 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
                className="w-4 h-4 rounded border accent-[var(--accent)]"
                style={{ accentColor: 'var(--accent)' }}
              />
              <span className="text-xs font-sans" style={{ color: 'var(--text-secondary)' }}>
                Remember me
              </span>
            </label>

            {/* Submit */}
            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-xl px-4 py-2.5 text-sm font-sans font-semibold text-white transition-all hover:opacity-90 active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
              style={{ background: 'var(--accent)' }}
            >
              {loading && (
                <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              )}
              {loading ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        </div>

        {/* Register link */}
        <p className="text-center text-xs font-sans mt-5" style={{ color: 'var(--text-secondary)' }}>
          Don't have an account?{' '}
          <button
            onClick={onShowRegister}
            className="font-medium transition-opacity hover:opacity-80"
            style={{ color: 'var(--accent)' }}
          >
            Create one
          </button>
        </p>
      </div>
    </div>
  )
}
