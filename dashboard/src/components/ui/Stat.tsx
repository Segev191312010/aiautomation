import React from 'react'
import { cn } from '@/utils/cn'

export interface StatProps {
  label: string
  value: React.ReactNode
  delta?: { value: string; positive?: boolean }
  caption?: string
  /** Explicit semantic state for the value color. If unset, uses text-primary. */
  tone?: 'neutral' | 'positive' | 'negative' | 'warning'
  className?: string
}

const toneClasses: Record<NonNullable<StatProps['tone']>, string> = {
  neutral: 'text-theme-text',
  positive: 'text-theme-success',
  negative: 'text-theme-danger',
  warning: 'text-theme-warning',
}

export function Stat({ label, value, delta, caption, tone = 'neutral', className }: StatProps) {
  return (
    <div className={cn('flex flex-col gap-1', className)}>
      <span className="shell-kicker">{label}</span>
      <span className={cn('font-mono text-2xl font-semibold leading-tight tabular-nums', toneClasses[tone])}>
        {value}
      </span>
      {delta && (
        <span
          className={cn(
            'text-xs font-sans font-medium',
            delta.positive ? 'text-theme-success' : 'text-theme-danger',
          )}
        >
          {delta.positive ? '▲' : '▼'} {delta.value}
        </span>
      )}
      {caption && <span className="text-xs font-sans text-theme-muted">{caption}</span>}
    </div>
  )
}