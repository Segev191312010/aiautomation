import React from 'react'
import clsx from 'clsx'

interface DegradedStateCardProps {
  title: string
  reason: string
  description?: string
  compact?: boolean
}

export default function DegradedStateCard({
  title,
  reason,
  description,
  compact = false,
}: DegradedStateCardProps) {
  return (
    <div
      role="status"
      className={clsx(
        'rounded-2xl border border-amber-300/30 bg-amber-500/5 text-amber-100',
        compact ? 'p-4' : 'p-5',
      )}
    >
      <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.2em] text-amber-300">
        <span className="inline-flex h-2 w-2 rounded-full bg-amber-300" />
        Data unavailable
      </div>
      <h3 className="mt-2 text-sm font-sans font-semibold text-amber-50">{title}</h3>
      <p className="mt-1 text-sm font-sans text-amber-100/90">{reason}</p>
      {description ? <p className="mt-2 text-xs font-sans text-amber-200/80">{description}</p> : null}
    </div>
  )
}
