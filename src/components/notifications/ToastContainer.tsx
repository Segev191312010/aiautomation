/**
 * ToastContainer — lightweight notification toasts for order fills, errors, etc.
 */
import React, { useEffect, useState, useCallback } from 'react'
import clsx from 'clsx'

export interface Toast {
  id: string
  type: 'success' | 'error' | 'info' | 'warning'
  title: string
  message?: string
  duration?: number
}

// ── Global toast bus ─────────────────────────────────────────────────────────

type ToastListener = (toast: Toast) => void
const listeners = new Set<ToastListener>()

export function addToast(toast: Omit<Toast, 'id'>) {
  const full: Toast = { ...toast, id: crypto.randomUUID() }
  listeners.forEach((fn) => fn(full))
}

// ── Single toast ─────────────────────────────────────────────────────────────

const TYPE_STYLES: Record<Toast['type'], string> = {
  success: 'border-terminal-green/40 bg-terminal-green/10',
  error:   'border-terminal-red/40 bg-terminal-red/10',
  info:    'border-terminal-blue/40 bg-terminal-blue/10',
  warning: 'border-terminal-amber/40 bg-terminal-amber/10',
}

const TITLE_STYLES: Record<Toast['type'], string> = {
  success: 'text-terminal-green',
  error:   'text-terminal-red',
  info:    'text-terminal-blue',
  warning: 'text-terminal-amber',
}

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDismiss, toast.duration ?? 5000)
    return () => clearTimeout(t)
  }, [toast.duration, onDismiss])

  return (
    <div
      className={clsx(
        'border rounded-lg p-3 shadow-terminal backdrop-blur-sm animate-slide-in',
        'min-w-[280px] max-w-[380px]',
        TYPE_STYLES[toast.type],
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className={clsx('text-xs font-mono font-semibold', TITLE_STYLES[toast.type])}>
            {toast.title}
          </p>
          {toast.message && (
            <p className="text-[11px] font-mono text-terminal-dim mt-0.5">{toast.message}</p>
          )}
        </div>
        <button
          onClick={onDismiss}
          className="text-terminal-ghost hover:text-terminal-dim text-xs shrink-0"
        >
          x
        </button>
      </div>
    </div>
  )
}

// ── Container ────────────────────────────────────────────────────────────────

export default function ToastContainer() {
  const [toasts, setToasts] = useState<Toast[]>([])

  const handleToast = useCallback((toast: Toast) => {
    setToasts((prev) => [...prev, toast].slice(-5))
  }, [])

  useEffect(() => {
    listeners.add(handleToast)
    return () => { listeners.delete(handleToast) }
  }, [handleToast])

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  if (toasts.length === 0) return null

  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2">
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
      ))}
    </div>
  )
}
