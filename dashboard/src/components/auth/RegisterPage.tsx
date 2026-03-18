import React, { useState, useMemo } from 'react'
import { useToast } from '@/components/ui/ToastProvider'

interface Props {
  onShowLogin: () => void
}

// ── Password strength ─────────────────────────────────────────────────────────

interface StrengthResult {
  score: 0 | 1 | 2 | 3 | 4   // 0=empty, 1=weak, 2=fair, 3=good, 4=strong
  label: string
  color: string
}

function getStrength(pw: string): StrengthResult {
  if (!pw) return { score: 0, label: '', color: '' }
  let score = 0
  if (pw.length >= 8)                          score++
  if (/[A-Z]/.test(pw))                        score++
  if (/[0-9]/.test(pw))                        score++
  if (/[^A-Za-z0-9]/.test(pw))                score++
  const map: StrengthResult[] = [
    { score: 0, label: '',       color: '' },
    { score: 1, label: 'Weak',   color: '#ef4444' },
    { score: 2, label: 'Fair',   color: '#f59e0b' },
    { score: 3, label: 'Good',   color: '#22c55e' },
    { score: 4, label: 'Strong', color: '#16a34a' },
  ]
  return map[score] as StrengthResult
}

/**
 * Registration form with password strength meter.
 * Submits to POST /api/auth/register (stub — returns success toast then routes to login).
 */
export default function RegisterPage({ onShowLogin }: Props) {
  const toast = useToast()
  const [username,  setUsername]  = useState('')
  const [email,     setEmail]     = useState('')
  const [password,  setPassword]  = useState('')
  const [confirm,   setConfirm]   = useState('')
  const [loading,   setLoading]   = useState(false)
  const [errors,    setErrors]    = useState<Record<string, string>>({})
  const [showPwd,   setShowPwd]   = useState(false)

  const strength = useMemo(() => getStrength(password), [password])

  const validate = () => {
    const e: Record<string, string> = {}
    if (!username.trim())            e.username  = 'Username is required.'
    if (username.length < 3)         e.username  = 'Username must be at least 3 characters.'
    if (!email.includes('@'))        e.email     = 'Enter a valid email address.'
    if (password.length < 8)         e.password  = 'Password must be at least 8 characters.'
    if (!/[A-Z]/.test(password))     e.password  = 'Password must include an uppercase letter.'
    if (!/[0-9]/.test(password))     e.password  = 'Password must include a number.'
    if (password !== confirm)        e.confirm   = 'Passwords do not match.'
    return e
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const errs = validate()
    setErrors(errs)
    if (Object.keys(errs).length > 0) return

    setLoading(true)
    try {
      // Stub — in production: POST /api/auth/register
      await new Promise((r) => setTimeout(r, 800))
      toast.success('Account created! Please sign in.')
      onShowLogin()
    } catch {
      toast.error('Registration failed. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  const inputCls = (hasErr: boolean) =>
    'w-full rounded-xl border px-4 py-2.5 text-sm font-sans outline-none ' +
    'transition-all duration-150 bg-[var(--bg-input)] text-[var(--text-primary)] ' +
    `${hasErr ? 'border-[var(--danger)]' : 'border-[var(--border)]'} ` +
    'placeholder:text-[var(--text-muted)] ' +
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
              Create Account
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
              Create your account
            </h2>
            <p className="text-xs font-sans mt-0.5" style={{ color: 'var(--text-secondary)' }}>
              Set up your trading workspace
            </p>
          </div>

          <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
            {/* Username */}
            <div>
              <label className="block text-[11px] font-sans font-medium uppercase tracking-wide mb-1.5"
                style={{ color: 'var(--text-secondary)' }}
                htmlFor="reg-username"
              >
                Username
              </label>
              <input
                id="reg-username"
                type="text"
                autoComplete="username"
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="tradername"
                className={inputCls(!!errors.username)}
                disabled={loading}
              />
              {errors.username && (
                <p className="text-[10px] font-sans mt-1" style={{ color: 'var(--danger)' }}>{errors.username}</p>
              )}
            </div>

            {/* Email */}
            <div>
              <label className="block text-[11px] font-sans font-medium uppercase tracking-wide mb-1.5"
                style={{ color: 'var(--text-secondary)' }}
                htmlFor="reg-email"
              >
                Email
              </label>
              <input
                id="reg-email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className={inputCls(!!errors.email)}
                disabled={loading}
              />
              {errors.email && (
                <p className="text-[10px] font-sans mt-1" style={{ color: 'var(--danger)' }}>{errors.email}</p>
              )}
            </div>

            {/* Password */}
            <div>
              <label className="block text-[11px] font-sans font-medium uppercase tracking-wide mb-1.5"
                style={{ color: 'var(--text-secondary)' }}
                htmlFor="reg-password"
              >
                Password
              </label>
              <div className="relative">
                <input
                  id="reg-password"
                  type={showPwd ? 'text' : 'password'}
                  autoComplete="new-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className={`${inputCls(!!errors.password)} pr-10`}
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
                  <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4" aria-hidden="true">
                    {showPwd ? (
                      <>
                        <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
                        <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
                        <line x1="1" y1="1" x2="23" y2="23" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
                      </>
                    ) : (
                      <>
                        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
                        <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.75" />
                      </>
                    )}
                  </svg>
                </button>
              </div>
              {errors.password && (
                <p className="text-[10px] font-sans mt-1" style={{ color: 'var(--danger)' }}>{errors.password}</p>
              )}

              {/* Strength meter */}
              {password && (
                <div className="mt-2 flex items-center gap-2">
                  <div className="flex-1 flex gap-1">
                    {[1, 2, 3, 4].map((level) => (
                      <div
                        key={level}
                        className="h-1 flex-1 rounded-full transition-all duration-300"
                        style={{
                          background: strength.score >= level ? strength.color : 'var(--border)',
                        }}
                      />
                    ))}
                  </div>
                  {strength.label && (
                    <span className="text-[10px] font-sans font-medium shrink-0" style={{ color: strength.color }}>
                      {strength.label}
                    </span>
                  )}
                </div>
              )}

              {/* Requirements */}
              <div className="mt-2 grid grid-cols-2 gap-1">
                {[
                  { ok: password.length >= 8,      label: '8+ characters' },
                  { ok: /[A-Z]/.test(password),    label: 'Uppercase letter' },
                  { ok: /[0-9]/.test(password),    label: 'Number' },
                  { ok: /[^A-Za-z0-9]/.test(password), label: 'Special character' },
                ].map(({ ok, label }) => (
                  <div key={label} className="flex items-center gap-1.5">
                    <span style={{ color: ok ? 'var(--success)' : 'var(--text-muted)' }}>
                      {ok ? (
                        <svg viewBox="0 0 24 24" fill="none" className="w-3 h-3" aria-hidden="true">
                          <polyline points="20 6 9 17 4 12" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      ) : (
                        <svg viewBox="0 0 24 24" fill="none" className="w-3 h-3" aria-hidden="true">
                          <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="1.5" />
                        </svg>
                      )}
                    </span>
                    <span className="text-[10px] font-sans" style={{ color: ok ? 'var(--success)' : 'var(--text-muted)' }}>
                      {label}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Confirm password */}
            <div>
              <label className="block text-[11px] font-sans font-medium uppercase tracking-wide mb-1.5"
                style={{ color: 'var(--text-secondary)' }}
                htmlFor="reg-confirm"
              >
                Confirm Password
              </label>
              <input
                id="reg-confirm"
                type={showPwd ? 'text' : 'password'}
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder="••••••••"
                className={inputCls(!!errors.confirm)}
                disabled={loading}
              />
              {errors.confirm && (
                <p className="text-[10px] font-sans mt-1" style={{ color: 'var(--danger)' }}>{errors.confirm}</p>
              )}
            </div>

            {/* Submit */}
            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-xl px-4 py-2.5 text-sm font-sans font-semibold text-white transition-all hover:opacity-90 active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 mt-1"
              style={{ background: 'var(--accent)' }}
            >
              {loading && (
                <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              )}
              {loading ? 'Creating account…' : 'Create Account'}
            </button>
          </form>
        </div>

        {/* Sign-in link */}
        <p className="text-center text-xs font-sans mt-5" style={{ color: 'var(--text-secondary)' }}>
          Already have an account?{' '}
          <button
            onClick={onShowLogin}
            className="font-medium transition-opacity hover:opacity-80"
            style={{ color: 'var(--accent)' }}
          >
            Sign in
          </button>
        </p>
      </div>
    </div>
  )
}
