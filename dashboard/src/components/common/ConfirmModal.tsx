import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import clsx from 'clsx'

export interface ConfirmSummaryItem {
  label: string
  value: ReactNode
  tone?: 'default' | 'success' | 'danger'
}

export interface ConfirmModalProps {
  open: boolean
  title: string
  summary?: ConfirmSummaryItem[]
  description?: ReactNode
  requirePhrase?: string
  confirmPhrase?: string
  confirmLabel?: string
  cancelLabel?: string
  destructive?: boolean
  onConfirm: () => void
  onCancel: () => void
}

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'

export default function ConfirmModal({
  open,
  title,
  summary = [],
  description,
  requirePhrase,
  confirmPhrase,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  destructive = false,
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  const [typed, setTyped] = useState('')
  const dialogRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const openerRef = useRef<Element | null>(null)
  const titleId = useId()
  const descId = useId()
  const summaryId = useId()
  const instructionsId = useId()

  const expectedPhrase = confirmPhrase ?? requirePhrase ?? 'CONFIRM'
  const phraseMatches = typed === expectedPhrase
  const describedBy = [
    summary.length > 0 ? summaryId : null,
    description ? descId : null,
    instructionsId,
  ].filter(Boolean).join(' ')

  useEffect(() => {
    if (!open) {
      setTyped('')
      if (openerRef.current instanceof HTMLElement) {
        openerRef.current.focus()
      }
      openerRef.current = null
      return
    }

    openerRef.current = document.activeElement
    const frame = requestAnimationFrame(() => inputRef.current?.focus())
    return () => cancelAnimationFrame(frame)
  }, [open])

  useEffect(() => {
    if (!open) return

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onCancel()
        return
      }

      if (event.key !== 'Tab') return

      const root = dialogRef.current
      if (!root) return

      const focusables = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
        .filter((element) => !element.hasAttribute('disabled') && element.offsetParent !== null)

      if (focusables.length === 0) return

      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      const active = document.activeElement as HTMLElement | null

      if (event.shiftKey && active === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [open, onCancel])

  if (!open) return null

  const modal = (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={describedBy}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm"
      onClick={onCancel}
    >
      <div
        ref={dialogRef}
        className="w-full max-w-lg rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-5 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id={titleId} className="text-base font-semibold text-[var(--text-primary)] font-sans">
          {title}
        </h2>

        {description ? (
          <div id={descId} className="mt-3 space-y-2 text-sm leading-6 text-[var(--text-secondary)]">
            {description}
          </div>
        ) : null}

        {summary.length > 0 ? (
          <dl
            id={summaryId}
            className="mt-4 flex flex-col gap-2 rounded-xl border border-[var(--border)] bg-[var(--bg-hover)] p-3"
          >
            {summary.map((item) => (
              <div key={item.label} className="flex items-baseline justify-between gap-4 text-xs font-mono">
                <dt className="text-[var(--text-muted)] uppercase tracking-wider">{item.label}</dt>
                <dd
                  className={clsx(
                    'font-semibold tabular-nums',
                    item.tone === 'success' && 'text-[var(--success)]',
                    item.tone === 'danger' && 'text-[var(--danger)]',
                    (!item.tone || item.tone === 'default') && 'text-[var(--text-primary)]',
                  )}
                >
                  {item.value}
                </dd>
              </div>
            ))}
          </dl>
        ) : null}

        <label id={instructionsId} className="mt-4 block text-[11px] text-[var(--text-secondary)] font-sans">
          Type <span className="font-mono font-semibold text-[var(--text-primary)]">{expectedPhrase}</span> to confirm
        </label>
        <input
          ref={inputRef}
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && phraseMatches) {
              event.preventDefault()
              onConfirm()
            }
          }}
          aria-label={`Type ${expectedPhrase} to confirm`}
          className={clsx(
            'mt-1.5 w-full rounded-xl border bg-[var(--bg-primary)] px-3 py-2 text-sm tracking-wider text-[var(--text-primary)] focus:outline-none font-mono',
            phraseMatches
              ? destructive
                ? 'border-[var(--danger)]'
                : 'border-[var(--success)]'
              : 'border-[var(--border)] focus:border-[var(--accent)]',
          )}
        />

        <div className="mt-5 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-xl px-4 py-2 text-sm font-medium text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)] font-sans"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            disabled={!phraseMatches}
            onClick={onConfirm}
            className={clsx(
              'rounded-xl border px-5 py-2 text-sm font-semibold transition-colors font-sans',
              phraseMatches
                ? destructive
                  ? 'border-[rgba(217,76,61,0.4)] bg-[rgba(217,76,61,0.16)] text-[var(--danger)] hover:bg-[rgba(217,76,61,0.22)]'
                  : 'border-[rgba(31,157,104,0.4)] bg-[rgba(31,157,104,0.16)] text-[var(--success)] hover:bg-[rgba(31,157,104,0.22)]'
                : 'cursor-not-allowed border-[var(--border)] bg-[var(--bg-hover)] text-[var(--text-muted)]',
            )}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )

  return typeof document === 'undefined' ? modal : createPortal(modal, document.body)
}
