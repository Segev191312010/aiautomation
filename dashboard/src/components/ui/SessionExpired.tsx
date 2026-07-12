import React from 'react'

interface Props {
  bootstrapFailed: boolean
  message: string
  onRetry: () => void | Promise<void>
}

export default function SessionExpired({ bootstrapFailed, message, onRetry }: Props) {

  return (
    <div
      className="fixed inset-0 z-[180] flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(4px)' }}
      role="alert"
      aria-label={bootstrapFailed ? 'Session bootstrap failed' : 'Session expired'}
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
              {bootstrapFailed ? 'Session bootstrap failed' : 'Session expired'}
            </p>
            <p className="text-xs font-sans mt-0.5" style={{ color: 'var(--text-secondary)' }}>
              {message}
            </p>
          </div>
        </div>

        {/* Actions */}
        <div className="flex gap-3">
          <button
            onClick={() => { void onRetry() }}
            className="flex-1 rounded-xl px-4 py-2.5 text-sm font-sans font-semibold text-white transition-all hover:opacity-90 active:scale-[0.98]"
            style={{ background: 'var(--accent)' }}
          >
            Retry connection
          </button>
        </div>
      </div>
    </div>
  )
}
