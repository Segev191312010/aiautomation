import React from 'react'

interface Props {
  message?: string
}

/**
 * Full-screen branded loading screen shown during initial app bootstrap.
 * Uses CSS-variable colors so it respects the active theme.
 */
export default function LoadingScreen({ message = 'Loading workspace…' }: Props) {
  return (
    <div
      className="fixed inset-0 z-[200] flex flex-col items-center justify-center gap-6"
      style={{ background: 'var(--bg-primary)' }}
      aria-live="polite"
      aria-label="Loading"
    >
      {/* Brand mark */}
      <div className="flex flex-col items-center gap-3">
        <div
          className="flex h-12 w-12 items-center justify-center rounded-2xl"
          style={{ background: 'var(--accent)', boxShadow: '0 0 32px color-mix(in srgb, var(--accent) 35%, transparent)' }}
        >
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-6 h-6 text-white">
            <path d="M3.5 18.49l6-6.01 4 4L22 6.92l-1.41-1.41-7.09 7.97-4-4L2 16.99z" />
          </svg>
        </div>

        <div className="text-center">
          <p
            className="text-[10px] font-sans uppercase tracking-[0.28em]"
            style={{ color: 'var(--text-muted)' }}
          >
            TradeBot
          </p>
          <p
            className="text-sm font-mono font-semibold"
            style={{ color: 'var(--text-primary)' }}
          >
            Market Desk
          </p>
        </div>
      </div>

      {/* Animated bar */}
      <div
        className="h-0.5 w-40 rounded-full overflow-hidden"
        style={{ background: 'var(--border)' }}
      >
        <div
          className="h-full rounded-full animate-loading-bar"
          style={{ background: 'var(--accent)', width: '40%' }}
        />
      </div>

      {/* Message */}
      <p
        className="text-[11px] font-sans"
        style={{ color: 'var(--text-muted)' }}
      >
        {message}
      </p>

      <style>{`
        @keyframes loading-bar {
          0%   { transform: translateX(-200%); }
          100% { transform: translateX(400%); }
        }
        .animate-loading-bar {
          animation: loading-bar 1.4s ease-in-out infinite;
        }
      `}</style>
    </div>
  )
}
