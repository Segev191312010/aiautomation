import React from 'react'
import { cn } from '@/utils/cn'

type BadgeVariant = 'neutral' | 'accent' | 'success' | 'warning' | 'danger'

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant
  dot?: boolean
}

const variantClasses: Record<BadgeVariant, string> = {
  neutral: 'border-theme-border bg-theme-hover text-theme-dim',
  accent: 'border-theme-accent/30 bg-theme-accent/10 text-theme-accent',
  success: 'border-theme-success/30 bg-theme-success/10 text-theme-success',
  warning: 'border-theme-warning/30 bg-theme-warning/10 text-theme-warning',
  danger: 'border-theme-danger/30 bg-theme-danger/10 text-theme-danger',
}

const dotColors: Record<BadgeVariant, string> = {
  neutral: 'bg-theme-muted',
  accent: 'bg-theme-accent',
  success: 'bg-theme-success',
  warning: 'bg-theme-warning',
  danger: 'bg-theme-danger',
}

export function Badge({ variant = 'neutral', dot = false, className, children, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-sans font-medium',
        variantClasses[variant],
        className,
      )}
      {...props}
    >
      {dot && <span className={cn('inline-block h-1.5 w-1.5 rounded-full', dotColors[variant])} />}
      {children}
    </span>
  )
}