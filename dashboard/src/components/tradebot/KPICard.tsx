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
        'glass rounded-2xl p-5 flex flex-col gap-1',
        highlight
          ? 'border-indigo-500/40 shadow-glow-blue'
          : 'border-white/[0.06]',
      )}
    >
      <span className="text-xs font-sans font-medium text-terminal-dim tracking-wide uppercase">
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
