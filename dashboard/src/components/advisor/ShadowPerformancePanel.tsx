/**
 * ShadowPerformancePanel — Validation dashboard for AI shadow mode.
 * Shows KPIs, gating conditions, per-param breakdown, regime coverage,
 * and a guarded "Go Live" button when all conditions are met.
 * Data comes from props — no API calls.
 */
import React, { useState } from 'react'
import clsx from 'clsx'
import type { ShadowPerformance, GatingCondition, ParamTypeMetrics } from '@/types/advisor'

// ── Icons ──────────────────────────────────────────────────────────────────────

function IconCheck() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}
      strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  )
}

function IconX() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}
      strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
      <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  )
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function hitRateColor(rate: number | null): string {
  if (rate === null) return 'text-[var(--text-muted)]'
  if (rate >= 0.65) return 'text-emerald-600'
  if (rate >= 0.55) return 'text-amber-600'
  return 'text-red-600'
}

function paramHitRateColor(rate: number | null): string {
  if (rate === null) return 'text-[var(--text-muted)]'
  if (rate >= 0.55) return 'text-emerald-600'
  if (rate >= 0.45) return 'text-amber-600'
  return 'text-red-600'
}

function fmtPct(v: number | null): string {
  if (v === null || v === undefined) return 'N/A'
  return `${(Number(v) * 100).toFixed(1)}%`
}

// ── KPI Card ──────────────────────────────────────────────────────────────────

interface KPICardProps {
  label:     string
  value:     string
  valueClass?: string
  sub?:      string
}

function KPICard({ label, value, valueClass, sub }: KPICardProps) {
  return (
    <div className="bg-white border border-[var(--border)] rounded-xl p-4 flex flex-col gap-1.5 min-w-0">
      <span className="text-[10px] font-sans font-medium uppercase tracking-wider text-[var(--text-muted)] truncate">
        {label}
      </span>
      <span className={clsx('text-xl font-mono font-bold tabular-nums leading-none', valueClass ?? 'text-[var(--text-primary)]')}>
        {value}
      </span>
      {sub && (
        <span className="text-[11px] font-mono text-[var(--text-muted)]">{sub}</span>
      )}
    </div>
  )
}

// ── Gating condition row ───────────────────────────────────────────────────────

function GatingRow({ condition }: { condition: GatingCondition }) {
  const { name, met, actual, required } = condition
  const op = met ? '>=' : '<'

  return (
    <div className={clsx(
      'flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg text-xs font-sans',
      met ? 'bg-emerald-50 border border-emerald-100' : 'bg-red-50 border border-red-100',
    )}>
      <div className="flex items-center gap-2 min-w-0">
        <span className={clsx('flex-shrink-0', met ? 'text-emerald-600' : 'text-red-600')}>
          {met ? <IconCheck /> : <IconX />}
        </span>
        <span className={clsx(
          'font-mono font-medium truncate',
          met ? 'text-emerald-800' : 'text-red-800',
        )}>
          {name}
        </span>
      </div>
      <span className={clsx(
        'flex-shrink-0 font-mono tabular-nums',
        met ? 'text-emerald-700' : 'text-red-700',
      )}>
        {Number(actual).toFixed(2)} {op} {Number(required).toFixed(2)}
        <span className="ml-1.5">{met ? '✓' : '✗'}</span>
      </span>
    </div>
  )
}

// ── Param type table row ───────────────────────────────────────────────────────

function ParamRow({ paramType, metrics }: { paramType: string; metrics: ParamTypeMetrics }) {
  const { count, hit_rate, effect_size_avg, avg_confidence } = metrics

  return (
    <tr className="border-b border-[var(--border)]">
      <td className="py-2.5 px-3 text-left">
        <span className="text-xs font-mono text-[var(--text-primary)]">{paramType}</span>
      </td>
      <td className="py-2.5 px-3 text-right text-xs font-mono tabular-nums text-[var(--text-secondary)]">
        {count}
      </td>
      <td className={clsx(
        'py-2.5 px-3 text-right text-xs font-mono tabular-nums font-medium',
        paramHitRateColor(hit_rate),
      )}>
        {fmtPct(hit_rate)}
      </td>
      <td className="py-2.5 px-3 text-right text-xs font-mono tabular-nums text-[var(--text-secondary)]">
        {effect_size_avg !== null ? Number(effect_size_avg).toFixed(2) : 'N/A'}
      </td>
      <td className={clsx(
        'py-2.5 px-3 text-right text-xs font-mono tabular-nums',
        avg_confidence !== null
          ? Number(avg_confidence) >= 0.7
            ? 'text-emerald-600'
            : Number(avg_confidence) >= 0.5
              ? 'text-amber-600'
              : 'text-red-600'
          : 'text-[var(--text-muted)]',
      )}>
        {avg_confidence !== null ? fmtPct(avg_confidence) : 'N/A'}
      </td>
    </tr>
  )
}

// ── Confirm dialog ─────────────────────────────────────────────────────────────

interface ConfirmDialogProps {
  onConfirm: () => void
  onCancel:  () => void
}

function ConfirmDialog({ onConfirm, onCancel }: ConfirmDialogProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm">
      <div className="bg-white border border-[var(--border)] rounded-2xl shadow-lg p-6 max-w-sm w-full mx-4">
        <h3 className="text-sm font-sans font-semibold text-[var(--text-primary)] mb-2">
          Disable Shadow Mode?
        </h3>
        <p className="text-xs font-sans text-[var(--text-secondary)] mb-4 leading-relaxed">
          All gating conditions are met. The AI Advisor will start applying
          parameter changes to live trading immediately. This cannot be undone
          without manually re-enabling shadow mode.
        </p>
        <div className="flex gap-2 justify-end">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-xs font-sans text-[var(--text-secondary)] border border-[var(--border)]
                       rounded-lg hover:bg-[var(--bg-hover)] transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-3 py-1.5 text-xs font-sans font-semibold text-white
                       bg-emerald-600 hover:bg-emerald-700 rounded-lg transition-colors"
          >
            Confirm — Go Live
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

interface Props {
  performance: ShadowPerformance | null
  onGoLive:    () => void | Promise<void>
}

export default function ShadowPerformancePanel({ performance, onGoLive }: Props) {
  const [confirming, setConfirming] = useState(false)
  const [goingLive, setGoingLive] = useState(false)

  if (!performance) {
    return (
      <div className="flex items-center justify-center py-10 text-sm font-sans text-[var(--text-muted)]">
        No shadow data yet — run the optimizer in shadow mode first.
      </div>
    )
  }

  const {
    total_decisions,
    decisions_with_data,
    overall_hit_rate,
    overall_effect_size_avg,
    active_days,
    regimes_covered,
    by_param_type,
    gating_conditions,
    ready_for_live,
  } = performance

  const allMet      = gating_conditions.length > 0 && gating_conditions.every((c) => c.met)
  const paramTypes  = Object.entries(by_param_type)
  const regimesList = Object.entries(regimes_covered ?? {})

  const hitRatePct = overall_hit_rate !== null
    ? (Number(overall_hit_rate) * 100).toFixed(1) + '%'
    : 'N/A'

  function handleGoLiveClick() {
    setConfirming(true)
  }

  async function handleConfirm() {
    if (goingLive) return
    setGoingLive(true)
    try {
      await onGoLive()
      setConfirming(false)
    } catch {
      setConfirming(false)  // dismiss dialog — error shows in page error banner
    } finally {
      setGoingLive(false)
    }
  }

  return (
    <>
      {confirming && (
        <ConfirmDialog
          onConfirm={handleConfirm}
          onCancel={() => setConfirming(false)}
        />
      )}

      <div className="space-y-6">

        {/* KPI row */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <KPICard
            label="Hit Rate"
            value={hitRatePct}
            valueClass={hitRateColor(overall_hit_rate)}
          />
          <KPICard
            label="Decisions Scored"
            value={`${decisions_with_data}`}
            sub={`of ${total_decisions} total`}
          />
          <KPICard
            label="Effect Size"
            value={overall_effect_size_avg !== null
              ? Number(overall_effect_size_avg).toFixed(2)
              : 'N/A'
            }
          />
          <KPICard
            label="Active Days"
            value={String(active_days)}
          />
        </div>

        {/* Gating conditions */}
        <div className="space-y-2.5">
          <div className="flex items-center gap-2">
            <h3 className="text-xs font-sans font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
              Go-Live Conditions
            </h3>
            <span className={clsx(
              'flex items-center gap-1 text-[10px] font-sans font-semibold uppercase tracking-widest px-2 py-0.5 rounded-full',
              allMet
                ? 'bg-emerald-100 text-emerald-700'
                : 'bg-red-100 text-red-700',
            )}>
              <span className={clsx('flex-shrink-0', allMet ? 'text-emerald-600' : 'text-red-600')}>
                {allMet ? <IconCheck /> : <IconX />}
              </span>
              {allMet ? 'All Met' : 'Not Ready'}
            </span>
          </div>

          {gating_conditions.length === 0 ? (
            <p className="text-xs font-sans text-[var(--text-muted)] py-2">
              No gating conditions defined.
            </p>
          ) : (
            <div className="space-y-1.5">
              {gating_conditions.map((cond) => (
                <GatingRow key={cond.name} condition={cond} />
              ))}
            </div>
          )}
        </div>

        {/* Param type breakdown */}
        {paramTypes.length > 0 && (
          <div className="space-y-2.5">
            <h3 className="text-xs font-sans font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
              Param Type Breakdown
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[520px]">
                <thead>
                  <tr className="border-b border-[var(--border)]">
                    {(['Type', 'Count', 'Hit Rate', 'Avg Effect', 'Avg Confidence'] as const).map((col) => (
                      <th
                        key={col}
                        className={clsx(
                          'py-2 px-3 text-[9px] font-sans uppercase tracking-widest text-[var(--text-muted)] font-medium',
                          col === 'Type' ? 'text-left' : 'text-right',
                        )}
                      >
                        {col}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {paramTypes.map(([type, metrics]) => (
                    <ParamRow key={type} paramType={type} metrics={metrics} />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Regime coverage */}
        {regimesList.length > 0 && (
          <div className="space-y-2.5">
            <h3 className="text-xs font-sans font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
              Regime Coverage
            </h3>
            <div className="flex flex-wrap gap-2">
              {regimesList.map(([regime, data]) => (
                <div
                  key={regime}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-[var(--bg-hover)] border border-[var(--border)]"
                >
                  <span className="text-xs font-mono font-semibold text-[var(--text-primary)]">
                    {regime}
                  </span>
                  <span className="text-[10px] font-mono text-[var(--text-muted)]">
                    {data.decisions} decisions
                  </span>
                  <span className={clsx(
                    'text-[10px] font-mono font-medium',
                    paramHitRateColor(data.hit_rate),
                  )}>
                    {fmtPct(data.hit_rate)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Go Live button */}
        {ready_for_live && (
          <div className="pt-1">
            <button
              onClick={handleGoLiveClick}
              className={clsx(
                'w-full py-3 text-sm font-sans font-semibold rounded-xl transition-colors',
                'bg-emerald-600 text-white hover:bg-emerald-700',
              )}
            >
              Disable Shadow Mode — Go Live
            </button>
          </div>
        )}

      </div>
    </>
  )
}
