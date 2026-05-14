/**
 * AlertToaster — listens for `tradebot-alert` window events and shows a stack
 * of dismissable toasts in the bottom-right.
 */
import React, { useEffect, useState } from 'react'

interface Toast {
  id:    number
  title: string
  body:  string
}

let nextId = 1

export default function AlertToaster() {
  const [toasts, setToasts] = useState<Toast[]>([])

  useEffect(() => {
    const handler = (e: Event) => {
      const ev = e as CustomEvent<{ title: string; body: string }>
      const id = nextId++
      setToasts((t) => [...t, { id, title: ev.detail.title, body: ev.detail.body }])
      setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 8_000)
    }
    window.addEventListener('tradebot-alert', handler as EventListener)
    return () => window.removeEventListener('tradebot-alert', handler as EventListener)
  }, [])

  if (toasts.length === 0) return null

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
      {toasts.map((t) => (
        <div
          key={t.id}
          className="bg-terminal-elevated border border-terminal-amber/50 rounded-lg p-3 shadow-glow-blue animate-pulse-slow"
          role="alert"
        >
          <div className="flex items-start justify-between gap-2">
            <div>
              <div className="text-xs font-mono font-semibold text-terminal-amber">{t.title}</div>
              <div className="text-[11px] font-mono text-terminal-text mt-0.5">{t.body}</div>
            </div>
            <button
              onClick={() => setToasts((all) => all.filter((x) => x.id !== t.id))}
              className="text-terminal-ghost hover:text-terminal-red text-xs"
              aria-label="Dismiss"
            >
              ✕
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}
