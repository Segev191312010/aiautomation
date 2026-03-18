import React, { useEffect, useState } from 'react'

/**
 * Modal shown when a 401 response is received anywhere in the app.
 * Dispatches a custom event from api.ts; this component listens for it.
 */

export const SESSION_EXPIRED_EVENT = 'session:expired'

export function emitSessionExpired() {
  window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT))
}

export default function SessionExpired() {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const handler = () => setVisible(true)
    window.addEventListener(SESSION_EXPIRED_EVENT, handler)
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, handler)
  }, [])

  if (!visible) return null

  return (
    <div
      className="fixed inset-0 z-[180] flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(4px)' }}
      role="dialog"
      aria-modal="true"
      aria-label="Session expired"
    >
      <div
        className="card rounded-2xl -lg w-full max-w-sm p-7 flex flex-col gap-5 animate-fade-in-up"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        {/* Icon */}
        <div className="flex items-center gap-3">
          <div
            className="flex h-10 w-10 items-center justify-center rounded-xl shrink-0"
            style={{ background: 'rgba(239,68,68,0.1)' }}
          >
            <svg viewBox="0 0 24 24" fill="none" className="w-5 h-5" aria-hidden="true">
              <rect x="3" y="11" width="18" height="11" rx="2" stroke="#ef4444" strokeWidth="1.75" strokeLinejoin="round" />
              <path d="M7 11V7a5 5 0 0 1 10 0v4" stroke="#ef4444" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
              <circle cx="12" cy="16" r="1" fill="#ef4444" />
            </svg>
          </div>
          <div>
            <p className="text-sm font-sans font-semibold" style={{ color: 'var(--text-primary)' }}>
              Session expired
            </p>
            <p className="text-xs font-sans mt-0.5" style={{ color: 'var(--text-secondary)' }}>
              Your session has timed out. Please reload to continue.
            </p>
          </div>
        </div>

        {/* Actions */}
        <div className="flex gap-3">
          <button
            onClick={() => window.location.reload()}
            className="flex-1 rounded-xl px-4 py-2.5 text-sm font-sans font-semibold text-white transition-all hover:opacity-90 active:scale-[0.98]"
            style={{ background: 'var(--accent)' }}
          >
            Reload
          </button>
          <button
            onClick={() => setVisible(false)}
            className="rounded-xl border px-4 py-2.5 text-sm font-sans font-medium transition-all hover:opacity-80 active:scale-[0.98]"
            style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)', background: 'transparent' }}
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  )
}
