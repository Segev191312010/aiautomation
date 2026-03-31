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
    positive === undefined ? 'text-zinc-100' : positive ? 'text-emerald-600' : 'text-red-400'
  const gradientFrom =
    positive === true ? 'from-emerald-600/[0.04]' : positive === false ? 'from-red-600/[0.04]' : 'from-zinc-50/50'

  return (
    <div className={clsx(
      'card rounded-2xl  p-4 flex flex-col gap-2 border-l-2 relative overflow-hidden',
      accentColor,
    )}>
      <div className={clsx(
        'absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t to-transparent pointer-events-none',
        gradientFrom,
      )} />
      <div className="flex items-center gap-2">
        <div className={clsx('w-6 h-6 rounded-lg flex items-center justify-center flex-shrink-0', iconBg)}>
          {icon}
        </div>
        <span className="text-[10px] font-sans font-medium text-zinc-400 tracking-widest uppercase truncate">
          {label}
        </span>
      </div>
      <span className={clsx('text-xl font-mono font-bold tabular-nums leading-none', valueColor)}>
        {value}
      </span>
      {sub && (
        <span className="text-[11px] font-mono text-zinc-500 tabular-nums">{sub}</span>
      )}
    </div>
  )
}

export function KpiSkeleton() {
  return (
    <div className="card rounded-2xl  p-4 flex flex-col gap-3 animate-pulse">
      <div className="flex items-center gap-2">
        <div className="w-6 h-6 rounded-lg bg-zinc-800" />
        <div className="h-2.5 w-24 rounded bg-zinc-800" />
      </div>
      <div className="h-6 w-32 rounded-xl bg-zinc-800" />
    </div>
  )
}
