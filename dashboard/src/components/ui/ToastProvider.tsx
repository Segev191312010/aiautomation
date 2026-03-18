import React, { createContext, useCallback, useContext, useState, useRef } from 'react'

type ToastType = 'success' | 'error' | 'warning' | 'info'

interface Toast {
  id: number
  type: ToastType
  message: string
  exiting: boolean
}

interface ToastAPI {
  success: (msg: string) => void
  error:   (msg: string) => void
  warning: (msg: string) => void
  info:    (msg: string) => void
}

const ToastCtx = createContext<ToastAPI | null>(null)

export function useToast(): ToastAPI {
  const ctx = useContext(ToastCtx)
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>')
  return ctx
}

// ── Per-type style tokens ────────────────────────────────────────────────────

const BORDER_ACCENT: Record<ToastType, string> = {
  success: 'border-l-green-600',
  error:   'border-l-red-600',
  warning: 'border-l-amber-600',
  info:    'border-l-indigo-600',
}

const ICON_COLOR: Record<ToastType, string> = {
  success: 'text-emerald-400',
  error:   'text-red-400',
  warning: 'text-amber-600',
  info:    'text-indigo-600',
}

const ICON_BG: Record<ToastType, string> = {
  success: 'rgba(16,185,129,0.12)',
  error:   'rgba(239,68,68,0.12)',
  warning: 'rgba(245,158,11,0.12)',
  info:    'rgba(99,102,241,0.12)',
}

// ── SVG icons ────────────────────────────────────────────────────────────────

function IconSuccess() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="w-3.5 h-3.5" aria-hidden="true">
      <polyline
        points="20 6 9 17 4 12"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function IconError() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="w-3.5 h-3.5" aria-hidden="true">
      <line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      <line x1="6"  y1="6" x2="18" y2="18" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  )
}

function IconWarning() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="w-3.5 h-3.5" aria-hidden="true">
      <path
        d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <line x1="12" y1="9"  x2="12"    y2="13"    stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <line x1="12" y1="17" x2="12.01" y2="17"    stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  )
}

function IconInfo() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="w-3.5 h-3.5" aria-hidden="true">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2" />
      <line x1="12" y1="8"  x2="12"    y2="8.01"  stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      <line x1="12" y1="12" x2="12"    y2="16"    stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}

function IconClose() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="w-3 h-3" aria-hidden="true">
      <line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      <line x1="6"  y1="6" x2="18" y2="18" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  )
}

const ICONS: Record<ToastType, React.ReactNode> = {
  success: <IconSuccess />,
  error:   <IconError />,
  warning: <IconWarning />,
  info:    <IconInfo />,
}

// ── ToastItem ────────────────────────────────────────────────────────────────

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: number) => void }) {
  return (
    <div
      className={[
        // layout
        'pointer-events-auto',
        'flex items-center gap-3',
        'pl-3 pr-3 py-3 min-w-[260px] max-w-[360px]',
        // shape + border
        'rounded-xl border-l-[3px]',
        BORDER_ACCENT[toast.type],
        // card background + subtle outer ring
        'border border-zinc-800',
        // shadow
        '',
        // animation
        toast.exiting ? 'animate-toast-out' : 'animate-slide-in',
      ].join(' ')}
      style={{
        // cream card surface
        background: 'rgba(250, 248, 245, 0.97)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
      }}
      role="alert"
      aria-live="polite"
    >
      {/* Colored icon badge */}
      <div
        className={['shrink-0 w-7 h-7 rounded-lg flex items-center justify-center', ICON_COLOR[toast.type]].join(' ')}
        style={{ background: ICON_BG[toast.type] }}
      >
        {ICONS[toast.type]}
      </div>

      {/* Message */}
      <span className="flex-1 text-xs font-sans font-medium text-zinc-100 leading-relaxed">
        {toast.message}
      </span>

      {/* Close button */}
      <button
        type="button"
        onClick={() => onDismiss(toast.id)}
        className={[
          'shrink-0 w-6 h-6 rounded-lg flex items-center justify-center',
          'text-zinc-500 hover:text-zinc-400',
          'hover:bg-zinc-800 active:bg-zinc-800',
          'transition-colors duration-150',
        ].join(' ')}
        aria-label="Dismiss"
      >
        <IconClose />
      </button>
    </div>
  )
}

// ── Provider ─────────────────────────────────────────────────────────────────

const AUTO_DISMISS_MS = 4000
const EXIT_ANIMATION_MS = 200

export default function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const idRef = useRef(0)

  const dismiss = useCallback((id: number) => {
    // Mark exiting first so exit animation plays, then remove
    setToasts((prev) => prev.map((t) => (t.id === id ? { ...t, exiting: true } : t)))
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
    }, EXIT_ANIMATION_MS)
  }, [])

  const push = useCallback((type: ToastType, message: string) => {
    const id = ++idRef.current
    setToasts((prev) => [...prev, { id, type, message, exiting: false }])
    setTimeout(() => dismiss(id), AUTO_DISMISS_MS)
  }, [dismiss])

  const api: ToastAPI = {
    success: (msg) => push('success', msg),
    error:   (msg) => push('error', msg),
    warning: (msg) => push('warning', msg),
    info:    (msg) => push('info', msg),
  }

  return (
    <ToastCtx.Provider value={api}>
      {children}
      {/* Toast stack — fixed bottom-right, stacks upward */}
      <div
        className="fixed bottom-5 right-5 z-50 flex flex-col-reverse gap-2.5 pointer-events-none"
        aria-label="Notifications"
      >
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} onDismiss={dismiss} />
        ))}
      </div>
    </ToastCtx.Provider>
  )
}
