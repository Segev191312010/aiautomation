import React from 'react'
import { cn } from '@/utils/cn'

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string
  helpText?: string
  error?: string
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ label, helpText, error, className, id, ...props }, ref) => {
    const inputId = id || (label ? `input-${label.replace(/\s+/g, '-').toLowerCase()}` : undefined)
    return (
      <div className="flex flex-col gap-1">
        {label && (
          <label htmlFor={inputId} className="text-xs font-sans font-medium text-theme-dim">
            {label}
          </label>
        )}
        <input
          ref={ref}
          id={inputId}
          className={cn(
            'h-10 rounded-lg border border-theme-border bg-theme-input px-3 text-sm font-sans text-theme-text',
            'placeholder:text-theme-muted transition-colors',
            'focus-visible:outline-none focus-visible:border-theme-accent focus-visible:ring-1 focus-visible:ring-theme-accent',
            'disabled:opacity-50 disabled:cursor-not-allowed',
            error && 'border-theme-danger focus-visible:border-theme-danger focus-visible:ring-theme-danger',
            className,
          )}
          aria-invalid={!!error}
          {...props}
        />
        {helpText && !error && <p className="text-xs font-sans text-theme-muted">{helpText}</p>}
        {error && <p className="text-xs font-sans text-theme-danger">{error}</p>}
      </div>
    )
  },
)
Input.displayName = 'Input'

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label?: string
  helpText?: string
  error?: string
}

export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ label, helpText, error, className, id, children, ...props }, ref) => {
    const selectId = id || (label ? `select-${label.replace(/\s+/g, '-').toLowerCase()}` : undefined)
    return (
      <div className="flex flex-col gap-1">
        {label && (
          <label htmlFor={selectId} className="text-xs font-sans font-medium text-theme-dim">
            {label}
          </label>
        )}
        <select
          ref={ref}
          id={selectId}
          className={cn(
            'h-10 rounded-lg border border-theme-border bg-theme-input px-3 text-sm font-sans text-theme-text',
            'transition-colors cursor-pointer',
            'focus-visible:outline-none focus-visible:border-theme-accent focus-visible:ring-1 focus-visible:ring-theme-accent',
            'disabled:opacity-50 disabled:cursor-not-allowed',
            error && 'border-theme-danger',
            className,
          )}
          {...props}
        >
          {children}
        </select>
        {helpText && !error && <p className="text-xs font-sans text-theme-muted">{helpText}</p>}
        {error && <p className="text-xs font-sans text-theme-danger">{error}</p>}
      </div>
    )
  },
)
Select.displayName = 'Select'