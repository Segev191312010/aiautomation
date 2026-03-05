import React, { createContext, useCallback, useContext, useState, useRef } from 'react'

type ToastType = 'success' | 'error' | 'warning' | 'info'

interface Toast {
  id: number
  type: ToastType
  message: string
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

// Left border accent per type — keep the colored stripe for fast visual scanning
const BORDER_ACCENT: Record<ToastType, string> = {
  success: 'border-l-terminal-green',
  error:   'border-l-terminal-red',
  warning: 'border-l-terminal-amber',
  info:    'border-l-indigo-400',
}

const TEXT_COLOR: Record<ToastType, string> = {
  success: 'text-terminal-green',
  error:   'text-terminal-red',
  warning: 'text-terminal-amber',
  info:    'text-indigo-400',
}

const ICONS: Record<ToastType, string> = {
  success: '\u2713',
  error:   '\u2717',
  warning: '\u26A0',
  info:    '\u2139',
}

export default function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const idRef = useRef(0)

  const push = useCallback((type: ToastType, message: string) => {
    const id = ++idRef.current
    setToasts((prev) => [...prev, { id, type, message }])
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
    }, 4000)
  }, [])

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const api: ToastAPI = {
    success: (msg) => push('success', msg),
    error:   (msg) => push('error', msg),
    warning: (msg) => push('warning', msg),
    info:    (msg) => push('info', msg),
  }

  return (
    <ToastCtx.Provider value={api}>
      {children}
      {/* Toast stack — fixed bottom-right */}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 pointer-events-none">
        {toasts.map((t) => (
          <div
            key={t.id}
            onClick={() => dismiss(t.id)}
            className={[
              'pointer-events-auto cursor-pointer',
              'glass-elevated rounded-xl shadow-glass',
              'pl-4 pr-5 py-3',
              'flex items-center gap-3',
              'border-l-2',
              'animate-slide-in',
              BORDER_ACCENT[t.type],
            ].join(' ')}
          >
            <span className={['text-sm font-semibold', TEXT_COLOR[t.type]].join(' ')}>
              {ICONS[t.type]}
            </span>
            <span className="text-xs font-sans text-terminal-text">{t.message}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}
