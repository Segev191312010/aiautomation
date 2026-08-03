import React from 'react'
import { cn } from '@/utils/cn'

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
      className={cn(
        'rounded-2xl border border-theme-warning/30 bg-theme-warning/5 text-theme-warning',
        compact ? 'p-4' : 'p-5',
      )}
    >
      <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.2em] text-theme-warning">
        <span className="inline-flex h-2 w-2 rounded-full bg-theme-warning" />
        Data unavailable
      </div>
      <h3 className="mt-2 text-sm font-sans font-semibold text-theme-text">{title}</h3>
      <p className="mt-1 text-sm font-sans text-theme-warning/90">{reason}</p>
      {description ? <p className="mt-2 text-xs font-sans text-theme-warning/80">{description}</p> : null}
    </div>
  )
}
