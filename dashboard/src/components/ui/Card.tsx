import React from 'react'
import { cn } from '@/utils/cn'

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  as?: React.ElementType
  interactive?: boolean
}

export const Card = React.forwardRef<HTMLDivElement, CardProps>(
  ({ className, children, as: Component = 'div', interactive = false, ...props }, ref) => {
    return (
      <Component
        ref={ref}
        className={cn(
          'rounded-[var(--radius-card)] border border-theme-border bg-theme-card backdrop-blur-md shadow-card',
          interactive && 'transition-colors hover:border-theme-accent/40 cursor-pointer',
          className,
        )}
        {...props}
      >
        {children}
      </Component>
    )
  },
)
Card.displayName = 'Card'

export interface CardHeaderProps extends React.HTMLAttributes<HTMLDivElement> {
  title: string
  subtitle?: string
  action?: React.ReactNode
  icon?: React.ReactNode
}

export function CardHeader({ title, subtitle, action, icon, className, ...props }: CardHeaderProps) {
  return (
    <div
      className={cn('flex items-start justify-between gap-3 p-5 pb-0', className)}
      {...props}
    >
      <div className="flex items-center gap-2.5 min-w-0">
        {icon && <span className="text-theme-dim shrink-0">{icon}</span>}
        <div className="min-w-0">
          <h3 className="text-sm font-sans font-semibold text-theme-text truncate">{title}</h3>
          {subtitle && (
            <p className="mt-0.5 text-xs font-sans text-theme-muted truncate">{subtitle}</p>
          )}
        </div>
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  )
}

export function CardBody({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('p-5', className)} {...props}>
      {children}
    </div>
  )
}

export function CardFooter({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'flex items-center justify-between gap-3 border-t border-theme-border px-5 py-3',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  )
}