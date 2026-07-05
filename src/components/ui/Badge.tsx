import React from 'react'
import clsx from 'clsx'

type Tone = 'blue' | 'green' | 'amber' | 'red' | 'purple' | 'dim'

interface Props {
  tone?: Tone
  children: React.ReactNode
  className?: string
}

const TONES: Record<Tone, string> = {
  blue:   'bg-terminal-blue/10   text-terminal-blue   border-terminal-blue/30',
  green:  'bg-terminal-green/10  text-terminal-green  border-terminal-green/30',
  amber:  'bg-terminal-amber/10  text-terminal-amber  border-terminal-amber/30',
  red:    'bg-terminal-red/10    text-terminal-red    border-terminal-red/30',
  purple: 'bg-terminal-purple/10 text-terminal-purple border-terminal-purple/30',
  dim:    'bg-terminal-muted     text-terminal-dim    border-terminal-border',
}

/** Small inline status / category pill. */
export default function Badge({ tone = 'dim', children, className }: Props) {
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1 px-2 py-0.5 rounded border',
        'text-[10px] font-mono uppercase tracking-widest leading-none whitespace-nowrap',
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}
