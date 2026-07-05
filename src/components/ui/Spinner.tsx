import React from 'react'
import clsx from 'clsx'

type Size = 'sm' | 'md' | 'lg'
type Tone = 'blue' | 'green' | 'red' | 'amber' | 'dim'

interface Props {
  size?: Size
  tone?: Tone
  /** Optional label rendered to the right of the spinner. */
  label?: string
  className?: string
}

const SIZES: Record<Size, string> = {
  sm: 'w-3.5 h-3.5 border-2',
  md: 'w-5 h-5 border-2',
  lg: 'w-8 h-8 border-[3px]',
}

const TONES: Record<Tone, string> = {
  blue:  'border-terminal-blue/30 border-t-terminal-blue',
  green: 'border-terminal-green/30 border-t-terminal-green',
  red:   'border-terminal-red/30 border-t-terminal-red',
  amber: 'border-terminal-amber/30 border-t-terminal-amber',
  dim:   'border-terminal-dim/30 border-t-terminal-dim',
}

/** Indeterminate loading spinner. */
export default function Spinner({ size = 'md', tone = 'blue', label, className }: Props) {
  return (
    <div className={clsx('inline-flex items-center gap-2', className)} role="status" aria-live="polite">
      <span
        className={clsx('inline-block rounded-full animate-spin', SIZES[size], TONES[tone])}
        aria-hidden="true"
      />
      {label && <span className="text-[11px] font-mono text-terminal-dim">{label}</span>}
      <span className="sr-only">Loading</span>
    </div>
  )
}
