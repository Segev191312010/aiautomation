/**
 * CorrelationMatrix — Pairwise correlation heatmap.
 * Color scale: red (high positive) → white (zero) → blue (high negative).
 * Click cell to inspect details. Warning indicators for correlations > threshold.
 */
import React, { useState } from 'react'
import clsx from 'clsx'
import type { CorrelationMatrix as CorrelationMatrixType } from '@/types'

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Map correlation [-1, 1] to a background color using red–white–blue scale */
function corrToColor(c: number): string {
  const clamped = Math.max(-1, Math.min(1, c))
  if (Math.abs(clamped) < 0.02) return 'rgba(243,244,246,0.6)'    // nearly zero → very light

  if (clamped > 0) {
    // positive: white → red
    const t = clamped
    const r = Math.round(255)
    const g = Math.round(255 * (1 - t * 0.85))
    const b = Math.round(255 * (1 - t * 0.85))
    return `rgb(${r},${g},${b})`
  } else {
    // negative: white → blue
    const t = -clamped
    const r = Math.round(255 * (1 - t * 0.85))
    const g = Math.round(255 * (1 - t * 0.75))
    const b = Math.round(255)
    return `rgb(${r},${g},${b})`
  }
}

function corrToTextColor(c: number): string {
  return Math.abs(c) > 0.55 ? 'text-white' : 'text-gray-700'
}

// ── Detail popover ────────────────────────────────────────────────────────────

interface CellDetail {
  symA: string
  symB: string
  corr: number
}

function CellDetailPanel({ detail, onClose }: { detail: CellDetail; onClose: () => void }) {
  const { symA, symB, corr } = detail
  const isHigh = Math.abs(corr) >= 0.8
  const label =
    corr >= 0.8 ? 'Highly Correlated' :
    corr >= 0.5 ? 'Moderately Correlated' :
    corr <= -0.8 ? 'Strongly Inverse' :
    corr <= -0.5 ? 'Moderately Inverse' :
    'Low Correlation'

  return (
    <div className="card rounded-2xl shadow-card border border-gray-200 p-5 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="text-xs font-sans font-semibold text-gray-700">
          {symA} vs {symB}
        </div>
        <button
          onClick={onClose}
          className="text-gray-400 hover:text-gray-600 transition-colors"
          aria-label="Close"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="w-4 h-4">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>

      <div className="flex items-center gap-3">
        <span
          className={clsx(
            'text-3xl font-mono font-bold tabular-nums',
            corr >= 0.5 ? 'text-red-500' : corr <= -0.5 ? 'text-blue-500' : 'text-gray-700',
          )}
        >
          {corr.toFixed(3)}
        </span>
        <span className={clsx(
          'text-xs font-sans px-2 py-1 rounded-lg border',
          isHigh
            ? 'text-amber-600 bg-amber-500/10 border-amber-500/20'
            : 'text-gray-500 bg-gray-100/60 border-gray-200',
        )}>
          {label}
        </span>
      </div>

      {isHigh && (
        <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl bg-amber-500/10 border border-amber-500/25 text-amber-600 text-[11px] font-sans">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="w-3.5 h-3.5 flex-shrink-0 mt-0.5">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
            <line x1="12" y1="9" x2="12" y2="13" />
            <line x1="12" y1="17" x2="12.01" y2="17" />
          </svg>
          <span>
            High correlation indicates these positions move together and may reduce diversification.
          </span>
        </div>
      )}

      <div className="text-[10px] font-sans text-gray-400">
        Correlation scale: +1 = perfect positive, 0 = no correlation, −1 = perfect inverse
      </div>
    </div>
  )
}

// ── Color legend ──────────────────────────────────────────────────────────────

function ColorLegend() {
  const steps = [-1, -0.75, -0.5, -0.25, 0, 0.25, 0.5, 0.75, 1]
  return (
    <div className="flex items-center gap-2">
      <span className="text-[9px] font-mono text-gray-400">−1</span>
      <div className="flex h-3 rounded-sm overflow-hidden flex-1 max-w-32">
        {steps.map((v, i) => (
          <div
            key={i}
            className="flex-1"
            style={{ background: corrToColor(v) }}
          />
        ))}
      </div>
      <span className="text-[9px] font-mono text-gray-400">+1</span>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  data:           CorrelationMatrixType | null
  loading:        boolean
  warnThreshold?: number
}

export default function CorrelationMatrix({ data, loading, warnThreshold = 0.8 }: Props) {
  const [selected, setSelected] = useState<CellDetail | null>(null)

  if (loading && !data) {
    return (
      <div className="animate-pulse space-y-2">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="h-8 bg-gray-100 rounded" />
        ))}
      </div>
    )
  }

  if (!data || data.symbols.length === 0) {
    return (
      <div className="flex items-center justify-center py-10 text-sm text-gray-400">
        No correlation data (need at least 2 positions with price history)
      </div>
    )
  }

  const { symbols, matrix } = data
  const highCorrPairs: Array<[string, string, number]> = []
  for (let i = 0; i < symbols.length; i++) {
    for (let j = i + 1; j < symbols.length; j++) {
      const c = matrix[i]?.[j] ?? 0
      if (Math.abs(c) >= warnThreshold) {
        highCorrPairs.push([symbols[i], symbols[j], c])
      }
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Warning summary */}
      {highCorrPairs.length > 0 && (
        <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl bg-amber-500/10 border border-amber-500/25 text-amber-600 text-xs font-sans">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="w-3.5 h-3.5 flex-shrink-0 mt-0.5">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
            <line x1="12" y1="9" x2="12" y2="13" />
            <line x1="12" y1="17" x2="12.01" y2="17" />
          </svg>
          <span>
            {highCorrPairs.length} highly correlated pair{highCorrPairs.length > 1 ? 's' : ''} detected (|ρ| ≥ {warnThreshold}):
            {' '}{highCorrPairs.map(([a, b]) => `${a}↔${b}`).join(', ')}
          </span>
        </div>
      )}

      {/* Matrix grid */}
      <div className="overflow-x-auto">
        <table className="border-separate border-spacing-0.5" style={{ tableLayout: 'fixed' }}>
          <thead>
            <tr>
              <th className="w-14" />
              {symbols.map((sym) => (
                <th key={sym} className="w-14 text-center pb-1">
                  <span className="text-[9px] font-mono text-gray-500 writing-mode-vertical truncate block max-w-[56px] overflow-hidden">
                    {sym}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {symbols.map((rowSym, i) => (
              <tr key={rowSym}>
                <td className="text-right pr-1.5 pb-0.5">
                  <span className="text-[9px] font-mono text-gray-500 truncate block">{rowSym}</span>
                </td>
                {symbols.map((colSym, j) => {
                  const corr = matrix[i]?.[j] ?? 0
                  const isDiag = i === j
                  const isWarn = !isDiag && Math.abs(corr) >= warnThreshold
                  return (
                    <td key={colSym} className="p-0.5">
                      <button
                        disabled={isDiag}
                        onClick={() =>
                          !isDiag && setSelected({ symA: rowSym, symB: colSym, corr })
                        }
                        title={isDiag ? `${rowSym} (self)` : `${rowSym} vs ${colSym}: ${corr.toFixed(3)}`}
                        className={clsx(
                          'w-14 h-7 rounded text-center text-[9px] font-mono tabular-nums transition-transform duration-100',
                          corrToTextColor(corr),
                          !isDiag && 'hover:scale-110 hover:shadow-md cursor-pointer',
                          isDiag && 'cursor-default',
                          isWarn && 'ring-1 ring-amber-400/60',
                        )}
                        style={{ background: isDiag ? 'rgba(107,114,128,0.15)' : corrToColor(corr) }}
                      >
                        {isDiag ? '1.00' : corr.toFixed(2)}
                      </button>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Legend + detail */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <ColorLegend />
        <span className="text-[9px] font-sans text-gray-400">Click a cell for details</span>
      </div>

      {selected && (
        <CellDetailPanel detail={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  )
}
