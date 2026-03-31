import React from 'react'
import clsx from 'clsx'
import type { CorrelationMatrix } from '@/types'

function corrBg(v: number): string {
  if (v === 1)     return 'bg-zinc-800'
  const a = Math.abs(v)
  if (a >= 0.8)    return v > 0 ? 'bg-red-200'    : 'bg-blue-200'
  if (a >= 0.6)    return v > 0 ? 'bg-red-500/15'    : 'bg-blue-100'
  if (a >= 0.4)    return v > 0 ? 'bg-orange-100' : 'bg-indigo-100'
  if (a >= 0.2)    return v > 0 ? 'bg-amber-50'   : 'bg-sky-50'
  return 'bg-zinc-900'
}

function corrText(v: number): string {
  if (v === 1) return 'text-zinc-400'
  const a = Math.abs(v)
  if (a >= 0.6) return v > 0 ? 'text-red-700'    : 'text-blue-700'
  if (a >= 0.3) return v > 0 ? 'text-orange-700' : 'text-indigo-700'
  return 'text-zinc-400'
}

interface CorrelationMatrixPanelProps {
  matrix: CorrelationMatrix
}

export function CorrelationMatrixPanel({ matrix }: CorrelationMatrixPanelProps) {
  const { symbols, matrix: mat } = matrix
  return (
    <div className="overflow-x-auto">
      <table className="border-collapse text-center" style={{ minWidth: (symbols.length + 1) * 72 }}>
        <thead>
          <tr>
            <th className="w-16 h-8" />
            {symbols.map((s) => (
              <th key={s} className="w-16 h-8 text-[10px] font-mono font-semibold text-zinc-400 tracking-wide">{s}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {symbols.map((rowSym, i) => (
            <tr key={rowSym}>
              <td className="text-[10px] font-mono font-semibold text-zinc-400 pr-2 text-right whitespace-nowrap">{rowSym}</td>
              {symbols.map((_, j) => {
                const v = mat[i][j]
                return (
                  <td key={j} className={clsx('w-14 h-10 text-[11px] font-mono font-semibold rounded-sm border border-white/50', corrBg(v), corrText(v))}>
                    {v.toFixed(2)}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-3 flex items-center gap-4 text-[10px] font-sans text-zinc-400 flex-wrap">
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-sm bg-red-200 inline-block" /> High positive (concentrated)</span>
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-sm bg-zinc-900 border border-zinc-800 inline-block" /> Low (diversified)</span>
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-sm bg-blue-200 inline-block" /> Negative (hedge)</span>
      </div>
    </div>
  )
}
