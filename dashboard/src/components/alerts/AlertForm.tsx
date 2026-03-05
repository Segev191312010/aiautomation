/**
 * AlertForm — modal for creating or editing a single alert.
 *
 * Props:
 *   onClose        — dismisses the modal
 *   editAlert      — when set, form is in edit mode
 *   initialSymbol  — pre-fill symbol (e.g. from chart context menu)
 *   initialPrice   — pre-fill PRICE condition value
 */
import { useState, useEffect, useRef, useCallback, type FormEvent, type MouseEvent } from 'react'
import type { Alert, AlertCreate, AlertTestResult, AlertType, Condition, Indicator } from '@/types'
import {
  INDICATORS,
  OPERATORS,
  INDICATOR_PARAMS,
  defaultParams,
  formatConditionSummary,
} from '@/utils/conditionHelpers'
import { createAlert, updateAlert, testAlertNotification } from '@/services/api'
import { useAlertStore } from '@/store'
import { useToast } from '@/components/ui/ToastProvider'

// ── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  onClose: () => void
  editAlert?: Alert
  initialSymbol?: string
  initialPrice?: number
}

// ── Style constants ───────────────────────────────────────────────────────────

const INPUT_CLS =
  'w-full px-3 py-2 bg-black/20 border border-white/[0.08] rounded-xl ' +
  'font-sans text-sm text-terminal-text placeholder:text-terminal-ghost ' +
  'focus:outline-none focus:border-indigo-500/60 transition-colors'

const SELECT_CLS =
  'px-2 py-1.5 bg-black/20 border border-white/[0.08] rounded-xl ' +
  'font-sans text-sm text-terminal-text ' +
  'focus:outline-none focus:border-indigo-500/60 transition-colors'

const LABEL_CLS = 'block text-xs font-sans font-medium text-terminal-dim tracking-wide mb-1'

// ── Helpers ───────────────────────────────────────────────────────────────────

function buildDefaultCondition(initialPrice?: number): Condition {
  if (initialPrice !== undefined) {
    return { indicator: 'PRICE', params: {}, operator: '>', value: initialPrice }
  }
  return { indicator: 'RSI', params: { length: 14 }, operator: '<', value: 30 }
}

// ── ConditionEditor ───────────────────────────────────────────────────────────

interface ConditionEditorProps {
  cond: Condition
  onChange: (c: Condition) => void
}

function ConditionEditor({ cond, onChange }: ConditionEditorProps) {
  const paramDefs = INDICATOR_PARAMS[cond.indicator] ?? []

  return (
    <div className="space-y-3">
      {/* Indicator + dynamic params row */}
      <div className="flex items-end gap-3 flex-wrap">
        <div>
          <label className={LABEL_CLS}>Indicator</label>
          <select
            className={SELECT_CLS}
            value={cond.indicator}
            onChange={(e) => {
              const ind = e.target.value as Indicator
              onChange({ ...cond, indicator: ind, params: defaultParams(ind) })
            }}
          >
            {INDICATORS.map((ind) => (
              <option key={ind} value={ind}>{ind}</option>
            ))}
          </select>
        </div>

        {paramDefs.map((p) => (
          <div key={p.key}>
            <label className={LABEL_CLS}>{p.label}</label>
            {p.key === 'band' ? (
              <select
                className={SELECT_CLS}
                value={String(cond.params[p.key] ?? 'mid')}
                onChange={(e) =>
                  onChange({ ...cond, params: { ...cond.params, [p.key]: e.target.value } })
                }
              >
                <option value="upper">Upper</option>
                <option value="mid">Mid</option>
                <option value="lower">Lower</option>
              </select>
            ) : (
              <input
                type="number"
                className={`${SELECT_CLS} w-20`}
                value={cond.params[p.key] ?? p.def}
                onChange={(e) =>
                  onChange({
                    ...cond,
                    params: { ...cond.params, [p.key]: Number(e.target.value) },
                  })
                }
              />
            )}
          </div>
        ))}
      </div>

      {/* Operator + Value row */}
      <div className="flex items-end gap-3 flex-wrap">
        <div>
          <label className={LABEL_CLS}>Operator</label>
          <select
            className={SELECT_CLS}
            value={cond.operator}
            onChange={(e) => onChange({ ...cond, operator: e.target.value })}
          >
            {OPERATORS.map((op) => (
              <option key={op} value={op}>{op}</option>
            ))}
          </select>
        </div>

        <div>
          <label className={LABEL_CLS}>Value</label>
          <input
            type="number"
            step="any"
            className={`${SELECT_CLS} w-28`}
            value={cond.value as number}
            onChange={(e) => {
              const raw = e.target.value
              const num = Number(raw)
              onChange({ ...cond, value: isNaN(num) || raw === '' ? raw : num })
            }}
          />
        </div>
      </div>
    </div>
  )
}

// ── Main form component ───────────────────────────────────────────────────────

export default function AlertForm({ onClose, editAlert, initialSymbol, initialPrice }: Props) {
  const toast      = useToast()
  const loadAlerts = useAlertStore((s) => s.loadAlerts)

  // ── Form state ─────────────────────────────────────────────────────────────

  const [name, setName]           = useState(editAlert?.name ?? '')
  const [symbol, setSymbol]       = useState(editAlert?.symbol ?? initialSymbol ?? '')
  const [condition, setCondition] = useState<Condition>(
    editAlert?.condition ?? buildDefaultCondition(initialPrice),
  )
  const [alertType, setAlertType] = useState<AlertType>(editAlert?.alert_type ?? 'one_shot')
  const [cooldown, setCooldown]   = useState(editAlert?.cooldown_minutes ?? 60)

  const [submitting, setSubmitting] = useState(false)
  const [testing, setTesting]       = useState(false)
  const [error, setError]           = useState<string | null>(null)
  const [testResult, setTestResult] = useState<AlertTestResult | null>(null)

  // ── Refs ───────────────────────────────────────────────────────────────────

  const backdropRef  = useRef<HTMLDivElement>(null)
  const firstInputRef = useRef<HTMLInputElement>(null)

  // ── Focus trap ─────────────────────────────────────────────────────────────

  // Focus the first input when the modal opens
  useEffect(() => {
    firstInputRef.current?.focus()
  }, [])

  // Trap focus within the modal
  const dialogRef = useRef<HTMLDivElement>(null)

  const handleFocusTrap = useCallback((e: globalThis.KeyboardEvent) => {
    if (e.key !== 'Tab') return
    const dialog = dialogRef.current
    if (!dialog) return

    const focusable = dialog.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    )
    if (focusable.length === 0) return

    const first = focusable[0]
    const last  = focusable[focusable.length - 1]

    if (e.shiftKey) {
      if (document.activeElement === first) {
        e.preventDefault()
        last.focus()
      }
    } else {
      if (document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
  }, [])

  // ── Escape key + backdrop click ────────────────────────────────────────────

  useEffect(() => {
    function onKeyDown(e: globalThis.KeyboardEvent) {
      if (e.key === 'Escape') {
        onClose()
        return
      }
      handleFocusTrap(e)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose, handleFocusTrap])

  function handleBackdropClick(e: MouseEvent<HTMLDivElement>) {
    if (e.target === backdropRef.current) onClose()
  }

  // ── Auto-generate name ─────────────────────────────────────────────────────

  const handleNameBlur = useCallback(() => {
    if (!name.trim() && symbol.trim()) {
      setName(`${symbol.toUpperCase()} — ${formatConditionSummary(condition)}`)
    }
  }, [name, symbol, condition])

  // ── Build condition for preview / payload ──────────────────────────────────

  function buildCondition(): Condition {
    return condition
  }

  // ── Submit ─────────────────────────────────────────────────────────────────

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setError(null)

    const sym = symbol.trim().toUpperCase()
    if (!sym) {
      setError('Symbol is required')
      return
    }

    const resolvedName = name.trim() || `${sym} — ${formatConditionSummary(buildCondition())}`

    const payload: AlertCreate = {
      name:             resolvedName,
      symbol:           sym,
      condition:        buildCondition(),
      alert_type:       alertType,
      cooldown_minutes: alertType === 'recurring' ? cooldown : 0,
      enabled:          true,
    }

    setSubmitting(true)
    try {
      if (editAlert) {
        await updateAlert(editAlert.id, payload)
        toast.success('Alert updated')
      } else {
        await createAlert(payload)
        toast.success('Alert created')

        // Request browser notification permission on first creation
        if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
          void Notification.requestPermission()
        }
      }

      await loadAlerts()
      onClose()
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to save alert'
      setError(msg)
      toast.error(msg)
    } finally {
      setSubmitting(false)
    }
  }

  // ── Test notification ──────────────────────────────────────────────────────

  async function handleTest() {
    setError(null)
    setTestResult(null)

    const sym = symbol.trim().toUpperCase()
    if (!sym) {
      setError('Enter a symbol first')
      return
    }

    const payload: AlertCreate = {
      name:             name.trim() || `${sym} test`,
      symbol:           sym,
      condition:        buildCondition(),
      alert_type:       alertType,
      cooldown_minutes: alertType === 'recurring' ? cooldown : 0,
    }

    setTesting(true)
    try {
      const result = await testAlertNotification(payload)
      setTestResult(result)
      if (result.triggered) {
        toast.success(`Condition met — ${result.condition_summary} at $${result.price}`)
      } else {
        toast.info(`Condition not met — ${result.condition_summary} at $${result.price}`)
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Test failed'
      setError(msg)
      toast.error(msg)
    } finally {
      setTesting(false)
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  const conditionPreview = formatConditionSummary(buildCondition())

  return (
    <div
      ref={backdropRef}
      onClick={handleBackdropClick}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      aria-modal="true"
      role="dialog"
      aria-label={editAlert ? 'Edit Alert' : 'New Alert'}
    >
      <div
        ref={dialogRef}
        className={[
          'relative w-full max-w-lg mx-4',
          'glass-elevated rounded-2xl shadow-glass-lg',
          'flex flex-col max-h-[90vh] overflow-hidden',
        ].join(' ')}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/[0.06] shrink-0">
          <h2 className="text-xs font-sans font-semibold text-terminal-text tracking-wide">
            {editAlert ? 'Edit Alert' : 'New Alert'}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-terminal-dim hover:text-terminal-text hover:bg-white/[0.06] transition-colors"
            aria-label="Close"
          >
            <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
              <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
            </svg>
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="flex flex-col flex-1 overflow-hidden">
          {/* Scrollable body */}
          <div className="overflow-y-auto flex-1 px-5 py-4 space-y-5">

            {/* Name */}
            <div>
              <label className={LABEL_CLS}>Name</label>
              <input
                ref={firstInputRef}
                type="text"
                className={INPUT_CLS}
                placeholder="Auto-generated if left blank"
                value={name}
                onChange={(e) => setName(e.target.value)}
                onBlur={handleNameBlur}
                maxLength={120}
              />
            </div>

            {/* Symbol */}
            <div>
              <label className={LABEL_CLS}>Symbol</label>
              <input
                type="text"
                className={INPUT_CLS}
                placeholder="e.g. AAPL"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                required
                maxLength={20}
              />
            </div>

            {/* Condition */}
            <div>
              <label className={LABEL_CLS}>Condition</label>
              <div className="p-3 glass-elevated border border-white/[0.06] rounded-xl">
                <ConditionEditor cond={condition} onChange={setCondition} />
              </div>
            </div>

            {/* Condition preview */}
            <p className="text-xs font-sans text-terminal-dim -mt-2">
              Preview:{' '}
              <span className="font-mono text-indigo-400">{conditionPreview}</span>
            </p>

            {/* Alert type toggle */}
            <div>
              <label className={LABEL_CLS}>Alert Type</label>
              <div className="flex gap-2 mt-1">
                {(['one_shot', 'recurring'] as AlertType[]).map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setAlertType(t)}
                    className={[
                      'px-3 py-1.5 rounded-xl border text-xs font-sans font-medium transition-colors',
                      alertType === t
                        ? 'bg-indigo-500/20 text-indigo-400 border-indigo-500/40'
                        : 'border-white/[0.08] text-terminal-dim hover:border-white/20 hover:text-terminal-text',
                    ].join(' ')}
                  >
                    {t === 'one_shot' ? 'One-shot' : 'Recurring'}
                  </button>
                ))}
              </div>
              <p className="mt-1 text-xs font-sans text-terminal-ghost">
                {alertType === 'one_shot'
                  ? 'Fires once, then disables itself.'
                  : 'Fires repeatedly after the cooldown period.'}
              </p>
            </div>

            {/* Cooldown — only for recurring */}
            {alertType === 'recurring' && (
              <div>
                <label className={LABEL_CLS}>Cooldown (minutes)</label>
                <input
                  type="number"
                  className={INPUT_CLS}
                  min={1}
                  max={10080}
                  value={cooldown}
                  onChange={(e) => setCooldown(Number(e.target.value))}
                />
                <p className="mt-1 text-xs font-sans text-terminal-ghost">
                  Minimum time between consecutive triggers.
                </p>
              </div>
            )}

            {/* Test result inline display */}
            {testResult !== null && (
              <div
                className={[
                  'px-3 py-2.5 rounded-xl border text-xs font-sans',
                  testResult.triggered
                    ? 'bg-terminal-green/10 border-terminal-green/30 text-terminal-green'
                    : 'bg-white/[0.03] border-white/[0.06] text-terminal-dim',
                ].join(' ')}
              >
                <span className="font-semibold">
                  {testResult.triggered ? 'Condition met' : 'Condition not met'}
                </span>
                {' — '}
                <span className="font-mono">{testResult.condition_summary} at ${testResult.price.toFixed(2)}</span>
              </div>
            )}

            {/* Inline error display */}
            {error !== null && (
              <div className="px-3 py-2.5 rounded-xl border bg-terminal-red/10 border-terminal-red/30 text-terminal-red text-xs font-sans">
                {error}
              </div>
            )}

          </div>

          {/* Sticky footer */}
          <div className="px-5 py-4 border-t border-white/[0.06] shrink-0 flex items-center justify-between gap-3">
            {/* Test button */}
            <button
              type="button"
              onClick={handleTest}
              disabled={testing || submitting}
              className={[
                'px-3 py-1.5 rounded-xl border border-white/[0.08]',
                'text-xs font-sans font-medium text-terminal-dim',
                'hover:text-terminal-text hover:border-white/20',
                'disabled:opacity-40 transition-colors',
              ].join(' ')}
            >
              {testing ? 'Testing...' : 'Test Now'}
            </button>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={onClose}
                className={[
                  'px-4 py-2 rounded-xl border border-white/[0.08]',
                  'text-xs font-sans font-medium text-terminal-dim',
                  'hover:text-terminal-text hover:border-white/20',
                  'transition-colors',
                ].join(' ')}
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting || testing}
                className={[
                  'px-4 py-2 rounded-xl',
                  'bg-indigo-500 text-white',
                  'text-xs font-sans font-semibold',
                  'hover:bg-indigo-400 disabled:opacity-40 transition-colors',
                ].join(' ')}
              >
                {submitting ? 'Saving...' : editAlert ? 'Update Alert' : 'Create Alert'}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  )
}
