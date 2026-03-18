import React, { useEffect, useState } from 'react'
import { SHORTCUT_DEFS, SHORTCUT_SHOW_HELP } from '@/hooks/useKeyboardShortcuts'

/**
 * Modal displaying all registered keyboard shortcuts, grouped by category.
 * Opens via the custom `shortcut:show-help` event or the `?` key.
 */
export default function ShortcutHelp() {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const handler = () => setOpen(true)
    window.addEventListener(SHORTCUT_SHOW_HELP, handler)
    return () => window.removeEventListener(SHORTCUT_SHOW_HELP, handler)
  }, [])

  // Close on Escape
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open])

  if (!open) return null

  // Group shortcuts
  const groups: Record<string, typeof SHORTCUT_DEFS> = {}
  for (const def of SHORTCUT_DEFS) {
    if (!groups[def.group]) groups[def.group] = []
    groups[def.group].push(def)
  }

  return (
    <div
      className="fixed inset-0 z-[170] flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.5)', backdropFilter: 'blur(4px)' }}
      onClick={() => setOpen(false)}
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
    >
      <div
        className="card rounded-2xl shadow-card-lg w-full max-w-md animate-fade-in-up overflow-hidden"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-5 py-4"
          style={{ borderBottom: '1px solid var(--border)' }}
        >
          <div className="flex items-center gap-2.5">
            <div
              className="flex h-7 w-7 items-center justify-center rounded-lg"
              style={{ background: 'color-mix(in srgb, var(--accent) 12%, transparent)' }}
            >
              <svg viewBox="0 0 24 24" fill="none" className="w-3.5 h-3.5" aria-hidden="true">
                <rect x="2" y="4" width="20" height="16" rx="3" stroke="var(--accent)" strokeWidth="1.75" />
                <path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01M6 12h.01M10 12h.01M14 12h2M6 16h12"
                  stroke="var(--accent)" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            </div>
            <h2 className="text-sm font-sans font-semibold" style={{ color: 'var(--text-primary)' }}>
              Keyboard Shortcuts
            </h2>
          </div>
          <button
            onClick={() => setOpen(false)}
            className="rounded-lg p-1.5 transition-colors hover:opacity-70"
            style={{ color: 'var(--text-muted)' }}
            aria-label="Close shortcuts"
          >
            <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4" aria-hidden="true">
              <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto max-h-[70vh] p-5 flex flex-col gap-5">
          {Object.entries(groups).map(([group, defs]) => (
            <div key={group}>
              <p
                className="text-[10px] font-sans uppercase tracking-[0.2em] mb-2.5"
                style={{ color: 'var(--text-muted)' }}
              >
                {group}
              </p>
              <div className="flex flex-col gap-1.5">
                {defs.map((def) => (
                  <div key={def.key} className="flex items-center justify-between gap-4">
                    <span className="text-xs font-sans" style={{ color: 'var(--text-secondary)' }}>
                      {def.description}
                    </span>
                    <kbd
                      className="shrink-0 inline-flex items-center gap-0.5 rounded-md px-2 py-1 text-[10px] font-mono"
                      style={{
                        background: 'var(--bg-hover)',
                        border: '1px solid var(--border)',
                        color: 'var(--text-primary)',
                      }}
                    >
                      {def.key}
                    </kbd>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Footer hint */}
        <div
          className="px-5 py-3 text-center"
          style={{ borderTop: '1px solid var(--border)' }}
        >
          <p className="text-[10px] font-sans" style={{ color: 'var(--text-muted)' }}>
            Press <kbd
              className="inline rounded px-1 py-0.5 font-mono text-[9px]"
              style={{ background: 'var(--bg-hover)', border: '1px solid var(--border)' }}
            >Esc</kbd> or click outside to close
          </p>
        </div>
      </div>
    </div>
  )
}
