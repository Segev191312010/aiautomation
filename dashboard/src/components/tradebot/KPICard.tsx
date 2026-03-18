import React from 'react'
import clsx from 'clsx'

interface Props {
  label:     string
  value:     string | number
  subLabel?: string
  positive?: boolean
  prefix?:   string
  suffix?:   string
  highlight?: boolean
}

export default function KPICard({
  label, value, subLabel, positive, prefix = '', suffix = '', highlight,
}: Props) {
  const valueColor =
    highlight          ? 'text-blue-400'      :
    positive === true  ? 'text-emerald-400'   :
    positive === false ? 'text-red-400'       :
                         'text-zinc-100'

  const displayValue =
    typeof value === 'number'
      ? value.toLocaleString('en-US', { maximumFractionDigits: 2 })
      : value

  return (
    <div
      className={clsx(
        'bg-zinc-900/80 border border-zinc-800 rounded-2xl p-4',
        'flex flex-col gap-1.5 relative overflow-hidden',
        highlight && 'border-l-2 border-l-blue-500/60',
        'transition-colors hover:bg-zinc-800/60 cursor-default',
      )}
    >
      <span className="text-[10px] font-sans uppercase tracking-wider text-zinc-500">
        {label}
      </span>
      <div className="flex items-baseline gap-1">
        {prefix && (
          <span className={clsx('text-sm font-mono font-semibold tabular-nums', valueColor)}>
            {prefix}
          </span>
        )}
        <span className={clsx('text-xl font-mono font-bold tabular-nums leading-none', valueColor)}>
          {displayValue}
        </span>
        {suffix && (
          <span className={clsx('text-sm font-mono font-medium tabular-nums opacity-70', valueColor)}>
            {suffix}
          </span>
        )}
      </div>
      {subLabel && (
        <span className="text-[11px] font-mono text-zinc-500 leading-none">
          {subLabel}
        </span>
      )}
    </div>
  )
}
