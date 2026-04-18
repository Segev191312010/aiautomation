import { useEffect, useId, useRef, useState } from 'react'
import clsx from 'clsx'

export interface ConfirmSummaryItem {
  label: string
  value: React.ReactNode
  tone?: 'default' | 'success' | 'danger'
}

export interface ConfirmModalProps {
  open: boolean
  title: string
  summary: ConfirmSummaryItem[]
  requirePhrase?: string
  confirmLabel?: string
  cancelLabel?: string
  onConfirm: () => void
  onCancel: () => void
}

export default function ConfirmModal({
  open,
  title,
  summary,
  requirePhrase = 'CONFIRM',
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  const [typed, setTyped] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const titleId = useId()
  const descId = useId()

  const phraseMatches = typed === requirePhrase

  useEffect(() => {
    if (!open) {
      setTyped('')
      return
    }
    const id = requestAnimationFrame(() => inputRef.current?.focus())
    return () => cancelAnimationFrame(id)
  }, [open])

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onCancel()
      } else if (e.key === 'Enter' && phraseMatches) {
        e.preventDefault()
        onConfirm()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, phraseMatches, onConfirm, onCancel])

  if (!open) return null

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={descId}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-md mx-4 rounded-2xl border border-zinc-800 bg-zinc-950 p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id={titleId} className="text-base font-sans font-semibold text-zinc-100">
          {title}
        </h2>

        <dl id={descId} className="mt-4 flex flex-col gap-2 rounded-xl border border-zinc-800 bg-zinc-900/60 p-3">
          {summary.map((item) => (
            <div key={item.label} className="flex items-baseline justify-between gap-4 text-xs font-mono">
              <dt className="text-zinc-500 uppercase tracking-wider">{item.label}</dt>
              <dd
                className={clsx(
                  'font-semibold tabular-nums',
                  item.tone === 'success' && 'text-emerald-400',
                  item.tone === 'danger'  && 'text-red-400',
                  (!item.tone || item.tone === 'default') && 'text-zinc-100',
                )}
              >
                {item.value}
              </dd>
            </div>
          ))}
        </dl>

        <label className="mt-4 block text-[11px] font-sans text-zinc-400">
          Type <span className="font-mono font-semibold text-zinc-200">{requirePhrase}</span> to confirm
        </label>
        <input
          ref={inputRef}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          aria-label={`Type ${requirePhrase} to confirm`}
          className={clsx(
            'mt-1.5 w-full text-sm font-mono bg-zinc-900 border rounded-xl px-3 py-2 text-zinc-100 focus:outline-none tracking-wider',
            phraseMatches ? 'border-emerald-500/60 focus:border-emerald-500' : 'border-zinc-800 focus:border-indigo-600/50',
          )}
        />

        <div className="mt-5 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="text-sm font-sans font-medium px-4 py-2 rounded-xl text-zinc-300 hover:text-zinc-100 hover:bg-zinc-900 transition-colors"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            disabled={!phraseMatches}
            onClick={onConfirm}
            className={clsx(
              'text-sm font-sans font-semibold px-5 py-2 rounded-xl transition-colors',
              phraseMatches
                ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 hover:bg-emerald-500/30'
                : 'bg-zinc-900 text-zinc-600 border border-zinc-800 cursor-not-allowed',
            )}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
