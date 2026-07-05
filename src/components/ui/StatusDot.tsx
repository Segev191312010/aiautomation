import React from 'react'
import clsx from 'clsx'

type Tone = 'green' | 'red' | 'amber' | 'blue' | 'dim'

interface Props {
  tone?: Tone
  /** Optional text rendered to the right of the dot. */
  label?: string
  /** Animate the dot with a soft pulse (e.g. "live" / "connecting"). */
  pulse?: boolean
  className?: string
}

const DOTS: Record<Tone, string> = {
  green: 'bg-terminal-green shadow-glow-green',
  red:   'bg-terminal-red shadow-glow-red',
  amber: 'bg-terminal-amber',
  blue:  'bg-terminal-blue shadow-glow-blue',
  dim:   'bg-terminal-dim',
}

/** Colored status indicator dot with an optional label. */
export default function StatusDot({ tone = 'dim', label, pulse, className }: Props) {
  return (
    <span className={clsx('inline-flex items-center gap-2', className)}>
      <span
        className={clsx(
          'inline-block w-2 h-2 rounded-full shrink-0',
          DOTS[tone],
          pulse && 'animate-pulse-slow',
        )}
        aria-hidden="true"
      />
      {label && (
        <span className="text-[11px] font-mono text-terminal-dim uppercase tracking-wide">
          {label}
        </span>
      )}
    </span>
  )
}
