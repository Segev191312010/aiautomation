/**
 * RiskDashboard — Risk overview panel with:
 * - Drawdown gauge (semicircle SVG)
 * - Max drawdown with date
 * - Risk limit status cards (OK / WARN / BREACH)
 */
import React from 'react'
import clsx from 'clsx'
import type { RiskCheckResult, RiskLimits } from '@/types'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtValue(v: number, unit: '$' | '%' | 'count'): string {
  if (unit === '$') {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 0 }).format(v)
  }
  if (unit === '%') return `${v.toFixed(1)}%`
  return String(v)
}

// ── Drawdown Gauge (semicircle) ───────────────────────────────────────────────

const GCX = 90
const GCY = 88
const G_RADIUS = 70
const G_STROKE = 12
const SEMI_CIRC = Math.PI * G_RADIUS

function fractionToPoint(fraction: number): { x: number; y: number } {
  const angle = Math.PI - fraction * Math.PI
  return {
    x: GCX + G_RADIUS * Math.cos(angle),
    y: GCY - G_RADIUS * Math.sin(angle),
  }
}

interface DrawdownGaugeProps {
  currentDrawdownPct: number
  limitPct:           number
}

function DrawdownGauge({ currentDrawdownPct, limitPct }: DrawdownGaugeProps) {
  // fraction along gauge: 0 = no drawdown (left), 1 = max drawdown (right)
  const fraction = Math.min(1, Math.max(0, currentDrawdownPct / Math.max(limitPct * 1.25, 1)))
  const needle   = fractionToPoint(fraction)

  const arcStartX = GCX - G_RADIUS
  const arcStartY = GCY
  const arcEndX   = GCX + G_RADIUS
  const arcEndY   = GCY

  const color =
    fraction >= 0.9 ? '#ef4444' :
    fraction >= 0.6 ? '#f59e0b' :
    '#10b981'

  return (
    <div className="flex flex-col items-center">
      <svg
        viewBox={`0 0 ${GCX * 2} ${GCY + 12}`}
        className="w-full max-w-[200px]"
        aria-label={`Drawdown gauge: ${currentDrawdownPct.toFixed(2)}%`}
      >
        <defs>
          <linearGradient id="ddGrad" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%"   stopColor="#10b981" />
            <stop offset="50%"  stopColor="#f59e0b" />
            <stop offset="100%" stopColor="#ef4444" />
          </linearGradient>
          <filter id="ddNeedleGlow">
            <feGaussianBlur stdDeviation="2" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
        </defs>

        {/* Track */}
        <path
          d={`M ${arcStartX} ${arcStartY} A ${G_RADIUS} ${G_RADIUS} 0 0 1 ${arcEndX} ${arcEndY}`}
          fill="none"
          stroke="url(#ddGrad)"
          strokeWidth={G_STROKE}
          strokeLinecap="round"
          opacity="0.18"
        />

        {/* Filled arc up to current fraction */}
        <path
          d={`M ${arcStartX} ${arcStartY} A ${G_RADIUS} ${G_RADIUS} 0 0 1 ${arcEndX} ${arcEndY}`}
          fill="none"
          stroke="url(#ddGrad)"
          strokeWidth={G_STROKE}
          strokeLinecap="round"
          strokeDasharray={`${SEMI_CIRC}`}
          strokeDashoffset={SEMI_CIRC * (1 - fraction)}
        />

        {/* Limit marker */}
        {(() => {
          const limitFrac = Math.min(1, limitPct / (limitPct * 1.25))
          const pt = fractionToPoint(limitFrac)
          const outerPt = {
            x: GCX + (G_RADIUS + G_STROKE / 2 + 4) * Math.cos(Math.PI - limitFrac * Math.PI),
            y: GCY - (G_RADIUS + G_STROKE / 2 + 4) * Math.sin(Math.PI - limitFrac * Math.PI),
          }
          return (
            <line
              x1={pt.x} y1={pt.y}
              x2={outerPt.x} y2={outerPt.y}
              stroke="rgba(239,68,68,0.7)"
              strokeWidth="2"
              strokeDasharray="2 2"
            />
          )
        })()}

        {/* Needle */}
        <circle cx={GCX} cy={GCY} r={6} fill="#f9fafb" stroke="rgba(107,114,128,0.3)" strokeWidth="1.5" />
        <line
          x1={GCX} y1={GCY}
          x2={needle.x} y2={needle.y}
          stroke={color}
          strokeWidth="2.5"
          strokeLinecap="round"
          filter="url(#ddNeedleGlow)"
        />
        <circle cx={needle.x} cy={needle.y} r={3.5} fill={color} filter="url(#ddNeedleGlow)" />
        <circle cx={GCX} cy={GCY} r={3} fill="#f9fafb" />
        <circle cx={GCX} cy={GCY} r={1.5} fill={color} opacity="0.8" />

        {/* Labels */}
        <text x={arcStartX + 2} y={GCY + 14} textAnchor="start" fontSize="7.5" fill="rgba(107,114,128,0.6)" fontFamily="ui-monospace, monospace">0%</text>
        <text x={arcEndX - 2} y={GCY + 14} textAnchor="end" fontSize="7.5" fill="rgba(107,114,128,0.6)" fontFamily="ui-monospace, monospace">{(limitPct * 1.25).toFixed(0)}%</text>
      </svg>

      {/* Center readout below gauge */}
      <div className="flex flex-col items-center -mt-1">
        <span className={clsx(
          'text-2xl font-mono font-bold tabular-nums leading-none',
          fraction >= 0.9 ? 'text-red-500' : fraction >= 0.6 ? 'text-amber-500' : 'text-emerald-500',
        )}>
          -{currentDrawdownPct.toFixed(2)}%
        </span>
        <span className="text-[10px] font-sans text-gray-400 mt-1">Current Drawdown</span>
        <span className="text-[9px] font-mono text-gray-400/60">limit: -{limitPct}%</span>
      </div>
    </div>
  )
}

// ── Risk check card ───────────────────────────────────────────────────────────

function RiskCheckCard({ check }: { check: RiskCheckResult }) {
  const ratio  = check.limit > 0 ? check.current / check.limit : 0
  const pctBar = Math.min(100, ratio * 100)

  const statusStyles = {
    OK:     { badge: 'text-emerald-500 bg-emerald-500/10 border-emerald-500/20', bar: 'bg-emerald-500', border: 'border-l-emerald-500/50' },
    WARN:   { badge: 'text-amber-600 bg-amber-500/10 border-amber-500/20',       bar: 'bg-amber-500',   border: 'border-l-amber-500/50'   },
    BREACH: { badge: 'text-red-500 bg-red-500/10 border-red-500/20',             bar: 'bg-red-500',     border: 'border-l-red-500/60'     },
  }[check.status]

  return (
    <div className={clsx(
      'card rounded-xl shadow-card p-4 flex flex-col gap-2.5 border-l-2',
      statusStyles.border,
    )}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-sans text-gray-600 truncate">{check.name}</span>
        <span className={clsx('text-[9px] font-mono font-semibold px-2 py-0.5 rounded-lg border flex-shrink-0', statusStyles.badge)}>
          {check.status}
        </span>
      </div>

      <div className="flex items-baseline justify-between gap-1">
        <span className="text-sm font-mono font-bold tabular-nums text-gray-800">
          {fmtValue(check.current, check.unit)}
        </span>
        <span className="text-[10px] font-mono text-gray-400">
          / {fmtValue(check.limit, check.unit)}
        </span>
      </div>

      {/* Progress bar */}
      <div className="h-1 rounded-full bg-gray-100 overflow-hidden">
        <div
          className={clsx('h-full rounded-full transition-all duration-300', statusStyles.bar)}
          style={{ width: `${pctBar}%` }}
        />
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  riskLimits:  RiskLimits | null
  riskChecks:  RiskCheckResult[]
  loading:     boolean
}

export default function RiskDashboard({ riskLimits, riskChecks, loading }: Props) {
  const drawdownCheck = riskChecks.find((c) =>
    c.name.toLowerCase().includes('drawdown'),
  )
  const currentDD = drawdownCheck ? drawdownCheck.current : 0
  const limitDD   = riskLimits?.drawdown_limit_pct ?? 10

  const breachCount = riskChecks.filter((c) => c.status === 'BREACH').length
  const warnCount   = riskChecks.filter((c) => c.status === 'WARN').length

  if (loading && !riskLimits) {
    return (
      <div className="animate-pulse space-y-3">
        <div className="h-40 bg-gray-100 rounded-2xl" />
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {[1, 2, 3, 4].map((i) => <div key={i} className="h-24 bg-gray-100 rounded-xl" />)}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {/* Drawdown gauge + summary */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {/* Gauge */}
        <div className="card rounded-2xl shadow-card p-5">
          <div className="text-[10px] font-sans uppercase tracking-widest text-gray-400 mb-3">
            Drawdown Monitor
          </div>
          <DrawdownGauge currentDrawdownPct={currentDD} limitPct={limitDD} />
        </div>

        {/* Status summary */}
        <div className="card rounded-2xl shadow-card p-5 flex flex-col gap-4">
          <div className="text-[10px] font-sans uppercase tracking-widest text-gray-400">
            Risk Status
          </div>

          <div className="flex flex-col gap-3">
            {/* Overall */}
            <div className="flex items-center gap-3">
              <div className={clsx(
                'w-3 h-3 rounded-full flex-shrink-0',
                breachCount > 0 ? 'bg-red-500 shadow-[0_0_6px_rgba(239,68,68,0.6)]' :
                warnCount   > 0 ? 'bg-amber-500 shadow-[0_0_6px_rgba(245,158,11,0.6)]' :
                'bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.6)]',
              )} />
              <div>
                <div className="text-sm font-sans font-semibold text-gray-800">
                  {breachCount > 0 ? 'Risk Breach Detected' :
                   warnCount   > 0 ? 'Risk Warning Active' :
                   'All Limits OK'}
                </div>
                <div className="text-[10px] font-sans text-gray-400">
                  {riskChecks.length} checks ·{' '}
                  {breachCount > 0 ? `${breachCount} breach${breachCount > 1 ? 'es' : ''}` :
                   warnCount > 0   ? `${warnCount} warning${warnCount > 1 ? 's' : ''}` :
                   'No issues'}
                </div>
              </div>
            </div>

            {/* Quick stats */}
            {riskLimits && (
              <div className="grid grid-cols-2 gap-2 mt-1">
                {[
                  { label: 'Max Position', value: `${riskLimits.max_position_size_pct}%` },
                  { label: 'Daily Loss Limit', value: `$${riskLimits.daily_loss_limit.toLocaleString()}` },
                  { label: 'DD Limit', value: `${riskLimits.drawdown_limit_pct}%` },
                  { label: 'Max Positions', value: String(riskLimits.max_open_positions) },
                ].map(({ label, value }) => (
                  <div key={label} className="flex flex-col gap-0.5">
                    <span className="text-[9px] font-sans uppercase tracking-wider text-gray-400">{label}</span>
                    <span className="text-xs font-mono font-semibold text-gray-700">{value}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Risk check cards grid */}
      {riskChecks.length > 0 && (
        <div>
          <div className="text-[10px] font-sans uppercase tracking-widest text-gray-400 mb-3">
            Limit Status
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {riskChecks.map((check) => (
              <RiskCheckCard key={check.name} check={check} />
            ))}
          </div>
        </div>
      )}

      {!riskLimits && !loading && (
        <div className="text-center py-8 text-sm text-gray-400">
          No risk limits configured. Connect backend to enable risk monitoring.
        </div>
      )}
    </div>
  )
}
