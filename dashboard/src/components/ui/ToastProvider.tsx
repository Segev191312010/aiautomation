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

const COLORS: Record<ToastType, string> = {
  success: 'border-terminal-green/50 bg-terminal-green/10 text-terminal-green',
  error:   'border-terminal-red/50 bg-terminal-red/10 text-terminal-red',
  warning: 'border-terminal-amber/50 bg-terminal-amber/10 text-terminal-amber',
  info:    'border-terminal-blue/50 bg-terminal-blue/10 text-terminal-blue',
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
            className={`pointer-events-auto cursor-pointer px-4 py-2.5 rounded border font-mono text-xs
              flex items-center gap-2 shadow-lg backdrop-blur-sm animate-slide-in
              ${COLORS[t.type]}`}
          >
            <span className="text-sm">{ICONS[t.type]}</span>
            <span>{t.message}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}
