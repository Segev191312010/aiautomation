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
  'w-full px-3 py-2.5 bg-white border border-gray-200 rounded-xl ' +
  'font-sans text-sm text-gray-800 placeholder:text-gray-400 ' +
  'focus:outline-none focus:border-indigo-600/60 focus:ring-1 focus:ring-indigo-300 transition-all'

const SELECT_CLS =
  'px-3 py-2 bg-white border border-gray-200 rounded-xl ' +
  'font-sans text-sm text-gray-800 ' +
  'focus:outline-none focus:border-indigo-600/60 focus:ring-1 focus:ring-indigo-300 transition-all'

const LABEL_CLS = 'block text-[11px] font-sans font-semibold text-gray-500 tracking-wider uppercase mb-1.5'

// ── Operator color map ────────────────────────────────────────────────────────

function operatorColor(op: string): string {
  if (op === '>' || op === '>=') return 'text-green-600'
  if (op === '<' || op === '<=') return 'text-red-600'
  if (op === '==') return 'text-indigo-600'
  if (op === '!=') return 'text-amber-600'
  return 'text-gray-500'
}

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
      <div className="flex items-end gap-2.5 flex-wrap">
        <div>
          <label className={LABEL_CLS}>Indicator</label>
          <select
            className={`${SELECT_CLS} min-w-[90px]`}
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

      {/* Operator + Value row — styled with colored operator pill */}
      <div className="flex items-end gap-2.5 flex-wrap">
        <div>
          <label className={LABEL_CLS}>Operator</label>
          <div className="relative">
            <select
              className={`${SELECT_CLS} min-w-[90px] font-mono font-semibold ${operatorColor(cond.operator)}`}
              value={cond.operator}
              onChange={(e) => onChange({ ...cond, operator: e.target.value })}
            >
              {OPERATORS.map((op) => (
                <option key={op} value={op}>{op}</option>
              ))}
            </select>
          </div>
        </div>

        <div>
          <label className={LABEL_CLS}>Value</label>
          <input
            type="number"
            step="any"
            className={`${SELECT_CLS} w-32`}
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

  useEffect(() => {
    firstInputRef.current?.focus()
  }, [])

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
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      aria-modal="true"
      role="dialog"
      aria-label={editAlert ? 'Edit Alert' : 'New Alert'}
    >
      <div
        ref={dialogRef}
        className={[
          'relative w-full max-w-lg mx-4',
          'card-elevated rounded-2xl shadow-card-lg',
          'flex flex-col max-h-[90vh] overflow-hidden',
          'border border-gray-200',
        ].join(' ')}
        onClick={(e) => e.stopPropagation()}
      >
        {/* ── Header ──────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="flex items-center justify-center w-6 h-6 rounded-md bg-indigo-50 shrink-0">
              <svg viewBox="0 0 24 24" fill="currentColor" className="w-3.5 h-3.5 text-indigo-600">
                <path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.89 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z" />
              </svg>
            </div>
            <h2 className="text-sm font-sans font-semibold text-gray-800 tracking-wide">
              {editAlert ? 'Edit Alert' : 'New Alert'}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-800 hover:bg-gray-100 transition-colors"
            aria-label="Close"
          >
            <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
              <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
            </svg>
          </button>
        </div>

        {/* ── Form ────────────────────────────────────────────────────── */}
        <form onSubmit={handleSubmit} className="flex flex-col flex-1 overflow-hidden">
          {/* Scrollable body */}
          <div className="overflow-y-auto flex-1 px-5 py-5 space-y-5">

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

            {/* Condition card */}
            <div>
              <label className={LABEL_CLS}>Condition</label>
              <div className="p-4 bg-gray-50 border border-gray-200 rounded-xl">
                <ConditionEditor cond={condition} onChange={setCondition} />
              </div>
              {/* Condition preview pill */}
              <div className="mt-2 flex items-center gap-2">
                <span className="text-[10px] font-sans text-gray-400 uppercase tracking-wider">Preview</span>
                <span className="px-2 py-0.5 rounded-md bg-indigo-50 border border-indigo-100 text-xs font-mono text-indigo-600">
                  {conditionPreview}
                </span>
              </div>
            </div>

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
                      'px-3.5 py-2 rounded-xl border text-xs font-sans font-semibold transition-all duration-150',
                      alertType === t
                        ? 'bg-indigo-100 text-indigo-600 border-indigo-100 shadow-glow-blue'
                        : 'border-gray-200 text-gray-500 hover:border-gray-200 hover:text-gray-800',
                    ].join(' ')}
                  >
                    {t === 'one_shot' ? 'One-shot' : 'Recurring'}
                  </button>
                ))}
              </div>
              <p className="mt-1.5 text-xs font-sans text-gray-400">
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
                <p className="mt-1.5 text-xs font-sans text-gray-400">
                  Minimum time between consecutive triggers.
                </p>
              </div>
            )}

            {/* Test result inline display */}
            {testResult !== null && (
              <div
                className={[
                  'flex items-start gap-2.5 px-3.5 py-3 rounded-xl border text-xs font-sans',
                  testResult.triggered
                    ? 'bg-green-50 border-green-600/30 text-green-600'
                    : 'bg-gray-50 border-gray-200 text-gray-500',
                ].join(' ')}
              >
                {/* Status dot */}
                <span className={`mt-0.5 w-1.5 h-1.5 rounded-full shrink-0 ${testResult.triggered ? 'bg-green-600' : 'bg-gray-400'}`} />
                <div>
                  <span className="font-semibold">
                    {testResult.triggered ? 'Condition met' : 'Condition not met'}
                  </span>
                  {' — '}
                  <span className="font-mono">{testResult.condition_summary} at ${testResult.price.toFixed(2)}</span>
                </div>
              </div>
            )}

            {/* Inline error display */}
            {error !== null && (
              <div className="flex items-start gap-2.5 px-3.5 py-3 rounded-xl border bg-red-50 border-red-300 text-red-600 text-xs font-sans">
                <span className="mt-0.5 w-1.5 h-1.5 rounded-full bg-red-600 shrink-0" />
                {error}
              </div>
            )}

          </div>

          {/* ── Sticky footer ──────────────────────────────────────────── */}
          <div className="px-5 py-4 border-t border-gray-200 shrink-0 flex items-center justify-between gap-3 bg-gray-50">
            {/* Test button */}
            <button
              type="button"
              onClick={handleTest}
              disabled={testing || submitting}
              className={[
                'flex items-center gap-1.5 px-3 py-2 rounded-xl border border-gray-200',
                'text-xs font-sans font-medium text-gray-500',
                'hover:text-gray-800 hover:border-gray-200',
                'disabled:opacity-40 transition-colors',
              ].join(' ')}
            >
              {testing ? (
                <>
                  <span className="w-3 h-3 rounded-full border-2 border-gray-500 border-t-transparent animate-spin" />
                  Testing...
                </>
              ) : (
                <>
                  <svg viewBox="0 0 24 24" fill="currentColor" className="w-3.5 h-3.5">
                    <path d="M8 5v14l11-7z" />
                  </svg>
                  Test Now
                </>
              )}
            </button>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={onClose}
                className={[
                  'px-4 py-2 rounded-xl border border-gray-200',
                  'text-xs font-sans font-medium text-gray-500',
                  'hover:text-gray-800 hover:border-gray-200',
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
                  'hover:bg-indigo-600',
                  'shadow-glow-blue hover:shadow-[0_0_28px_rgba(99,102,241,0.4)]',
                  'disabled:opacity-40 disabled:shadow-none',
                  'transition-all duration-150',
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
