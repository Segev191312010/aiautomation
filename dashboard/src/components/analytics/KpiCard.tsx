import React from 'react'
import clsx from 'clsx'

export interface KpiCardProps {
  label: string
  value: string
  sub?: string
  positive?: boolean
  icon: React.ReactNode
  iconBg: string
  accentColor: string
}

export function KpiCard({ label, value, sub, positive, icon, iconBg, accentColor }: KpiCardProps) {
  const valueColor =
    positive === undefined ? 'text-[var(--text-primary)]' : positive ? 'text-[var(--success)]' : 'text-[var(--danger)]'
  const gradientFrom =
    positive === true ? 'from-[rgba(31,157,104,0.12)]' : positive === false ? 'from-[rgba(217,76,61,0.12)]' : 'from-[rgba(245,158,11,0.08)]'

  return (
    <div className={clsx(
      'relative overflow-hidden rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] p-4 shadow-[0_24px_48px_-32px_var(--shadow-color)]',
    )}>
      <div className={clsx(
        'pointer-events-none absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t to-transparent',
        gradientFrom,
      )} />
      <div
        className={clsx(
          'pointer-events-none absolute inset-y-5 left-0 w-1 rounded-r-full opacity-80',
          accentColor,
        )}
      />
      <div
        className="pointer-events-none absolute inset-x-4 top-0 h-px bg-gradient-to-r from-transparent to-transparent"
        style={{ backgroundImage: 'linear-gradient(90deg, transparent, var(--accent), transparent)' }}
      />
      <div className="relative flex items-center gap-2">
        <div className={clsx('flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-2xl border border-[var(--border)]', iconBg)}>
          {icon}
        </div>
        <span className="shell-kicker truncate">
          {label}
        </span>
      </div>
      <span className={clsx('relative text-[1.7rem] font-semibold leading-none', valueColor)}>
        {value}
      </span>
      {sub && (
        <span className="relative text-[11px] font-mono tabular-nums text-[var(--text-secondary)]">{sub}</span>
      )}
    </div>
  )
}

export function KpiSkeleton() {
  return (
    <div className="rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] p-4 animate-pulse shadow-[0_24px_48px_-32px_var(--shadow-color)]">
      <div className="flex items-center gap-2">
        <div className="h-8 w-8 rounded-2xl bg-[var(--bg-card)]" />
        <div className="h-2.5 w-24 rounded bg-[var(--bg-card)]" />
      </div>
      <div className="mt-4 h-7 w-32 rounded-xl bg-[var(--bg-card)]" />
    </div>
  )
}
