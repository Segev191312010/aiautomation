import React, { useState } from 'react'
import clsx from 'clsx'
import type { StageDistribution } from '@/types'

interface Props {
  data: StageDistribution | null
}

const STAGE_META = [
  { key: 'stage_1', symbolsKey: 'stage_1_symbols', label: 'Stage 1 — Base (accumulation, flat 30WMA)',    color: 'bg-yellow-500', textColor: 'text-yellow-500' },
  { key: 'stage_2', symbolsKey: 'stage_2_symbols', label: 'Stage 2 — Advance (above rising 30WMA)',       color: 'bg-[var(--success)]', textColor: 'text-[var(--success)]' },
  { key: 'stage_3', symbolsKey: 'stage_3_symbols', label: 'Stage 3 — Top (flattening 30WMA, distribution)', color: 'bg-orange-500', textColor: 'text-orange-500' },
  { key: 'stage_4', symbolsKey: 'stage_4_symbols', label: 'Stage 4 — Decline (below declining 30WMA)',    color: 'bg-[var(--danger)]', textColor: 'text-[var(--danger)]' },
] as const

export default function StageAnalysis({ data }: Props) {
  const [expanded, setExpanded] = useState<string | null>(null)

  if (!data) return null

  const total = data.stage_1 + data.stage_2 + data.stage_3 + data.stage_4

  return (
    <div className="card">
      <h3 className="shell-kicker mb-1">Stage Analysis</h3>
      <p className="text-xs text-[var(--text-muted)] mb-4">
        Weinstein stage classification using 30-week MA (SMA150). Total: {total.toLocaleString()} stocks.
        Buy Stage 2, avoid Stage 4.
      </p>

      {/* Bar chart */}
      <div className="space-y-2 mb-4">
        {STAGE_META.map((s) => {
          const count = data[s.key]
          const pct = total > 0 ? (count / total) * 100 : 0
          const symbols = data[s.symbolsKey] ?? []
          const isOpen = expanded === s.key
          return (
            <div key={s.key}>
              <button
                onClick={() => setExpanded(isOpen ? null : s.key)}
                className="w-full text-left"
                aria-expanded={isOpen}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className={clsx('text-xs font-medium', s.textColor)}>{s.label}</span>
                  <span className="text-xs font-mono tabular-nums text-[var(--text-secondary)]">
                    {count.toLocaleString()} ({pct.toFixed(1)}%)
                  </span>
                </div>
                <div className="w-full h-4 rounded-full bg-[var(--bg-secondary)] overflow-hidden">
                  <div
                    className={clsx('h-full rounded-full transition-all', s.color)}
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </button>

              {/* Expanded symbol list */}
              {isOpen && symbols.length > 0 && (
                <div className="mt-2 mb-1 pl-2 flex flex-wrap gap-1.5">
                  {symbols.map((sym) => (
                    <span
                      key={sym}
                      className="px-2 py-0.5 rounded text-xs font-mono font-medium bg-[var(--bg-secondary)] text-[var(--accent)]"
                    >
                      {sym}
                    </span>
                  ))}
                  {count > symbols.length && (
                    <span className="px-2 py-0.5 text-xs text-[var(--text-muted)]">
                      +{(count - symbols.length).toLocaleString()} more
                    </span>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
