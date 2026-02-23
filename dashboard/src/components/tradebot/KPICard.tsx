import React from 'react'
import clsx from 'clsx'

interface Props {
  label:    string
  value:    string | number
  subLabel?: string
  positive?: boolean  // true → green, false → red, undefined → neutral
  prefix?:  string
  suffix?:  string
  highlight?: boolean
}

export default function KPICard({ label, value, subLabel, positive, prefix = '', suffix = '', highlight }: Props) {
  const colorClass =
    positive === true  ? 'text-terminal-green' :
    positive === false ? 'text-terminal-red'   : 'text-terminal-text'

  return (
    <div
      className={clsx(
        'bg-terminal-surface border rounded-lg p-4 flex flex-col gap-1',
        highlight
          ? 'border-terminal-blue/40 shadow-glow-blue'
          : 'border-terminal-border',
      )}
    >
      <span className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">
        {label}
      </span>
      <span className={clsx('text-2xl font-mono font-bold tabular-nums', colorClass)}>
        {prefix}{typeof value === 'number' ? value.toLocaleString('en-US', { maximumFractionDigits: 2 }) : value}{suffix}
      </span>
      {subLabel && (
        <span className="text-[11px] font-mono text-terminal-dim">{subLabel}</span>
      )}
    </div>
  )
}
