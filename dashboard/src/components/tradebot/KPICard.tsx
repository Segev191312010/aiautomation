import React from 'react'
import clsx from 'clsx'

interface Props {
  label: string
  value: string | number
  subLabel?: string
  positive?: boolean
  prefix?: string
  suffix?: string
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
  const displayValue =
    typeof value === 'number'
      ? value.toLocaleString('en-US', { maximumFractionDigits: 2 })
      : value

  const toneClass = highlight
    ? 'text-[var(--accent)]'
    : positive === true
      ? 'text-[var(--success)]'
      : positive === false
        ? 'text-[var(--danger)]'
        : 'text-[var(--text-primary)]'

  return (
    <div
      className={clsx(
        'relative overflow-hidden rounded-[24px] border px-4 py-4 transition-all',
        highlight
          ? 'border-[rgba(245,158,11,0.28)] bg-[rgba(245,158,11,0.08)]'
          : 'border-[var(--border)] bg-[var(--bg-hover)] hover:bg-[var(--bg-card)]',
      )}
    >
      <div
        className="pointer-events-none absolute inset-x-4 top-0 h-px bg-gradient-to-r from-transparent to-transparent"
        style={{ backgroundImage: 'linear-gradient(90deg, transparent, var(--accent), transparent)' }}
      />

      <span className="shell-kicker">{label}</span>

      <div className="mt-3 flex items-end gap-1.5">
        {prefix && (
          <span className={clsx('text-sm font-semibold', toneClass)}>
            {prefix}
          </span>
        )}

        <span className={clsx('text-[1.55rem] font-semibold leading-none', toneClass)}>
          {displayValue}
        </span>

        {suffix && (
          <span className={clsx('pb-0.5 text-sm font-semibold opacity-80', toneClass)}>
            {suffix}
          </span>
        )}
      </div>

      {subLabel && (
        <span className="mt-2 block text-xs leading-5 text-[var(--text-secondary)]">
          {subLabel}
        </span>
      )}
    </div>
  )
}
