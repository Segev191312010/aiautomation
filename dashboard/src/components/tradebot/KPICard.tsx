import React from 'react'
import clsx from 'clsx'

interface Props {
  label:     string
  value:     string | number
  subLabel?: string
  positive?: boolean  // true → green, false → red, undefined → neutral
  prefix?:   string
  suffix?:   string
  highlight?: boolean
}

export default function KPICard({
  label,
  value,
  subLabel,
  positive,
  prefix = '',
  suffix = '',
  highlight,
}: Props) {
  // Value colour: highlight wins if set, otherwise follow positive prop
  const valueColor =
    highlight            ? 'text-indigo-600'     :
    positive === true    ? 'text-green-600'  :
    positive === false   ? 'text-red-600'    :
                           'text-gray-800'

  // Subtle bottom gradient based on sentiment
  const gradientOverlay =
    positive === true
      ? 'before:from-green-600/[0.06]'
      : positive === false
        ? 'before:from-red-600/[0.06]'
        : 'before:from-gray-50/70'

  const displayValue =
    typeof value === 'number'
      ? value.toLocaleString('en-US', { maximumFractionDigits: 2 })
      : value

  return (
    <div
      className={clsx(
        // Base structure
        'card rounded-2xl shadow-card p-5',
        'flex flex-col gap-1.5',
        'relative overflow-hidden',

        // Left border accent when highlighted
        highlight
          ? 'border-l-2 border-l-indigo-600/60 shadow-glow-blue'
          : 'border-l-2 border-l-transparent',

        // Gradient pseudo-element overlay (bottom-up)
        'before:absolute before:inset-x-0 before:bottom-0 before:h-1/2',
        'before:bg-gradient-to-t before:to-transparent before:pointer-events-none',
        gradientOverlay,

        // Hover state
        'transition-colors duration-200 ease-out',
        'hover:bg-gray-100/60 cursor-default',
      )}
    >
      {/* Label */}
      <span className="text-[10px] font-sans uppercase tracking-wider text-gray-400">
        {label}
      </span>

      {/* Value row */}
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
          <span className={clsx('text-sm font-mono font-medium tabular-nums', valueColor, 'opacity-70')}>
            {suffix}
          </span>
        )}
      </div>

      {/* Optional sub-label */}
      {subLabel && (
        <span className="text-[11px] font-mono text-gray-500 leading-none">
          {subLabel}
        </span>
      )}
    </div>
  )
}
