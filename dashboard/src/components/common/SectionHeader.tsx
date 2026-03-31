import React from 'react'

interface SectionHeaderProps {
  icon?: React.ReactNode
  eyebrow: string
  title: string
  badge?: React.ReactNode
  action?: React.ReactNode
}

export function SectionHeader({ icon, eyebrow, title, badge, action }: SectionHeaderProps) {
  return (
    <div className="flex items-center gap-2.5 mb-4">
      {icon && (
        <div className="w-7 h-7 rounded-lg bg-zinc-800/60 flex items-center justify-center flex-shrink-0">
          {icon}
        </div>
      )}
      <div className="min-w-0">
        <div className="text-[10px] font-sans uppercase tracking-[0.2em] text-zinc-500">{eyebrow}</div>
        <h2 className="text-sm font-sans font-semibold text-zinc-100 tracking-wide">{title}</h2>
      </div>
      {badge && <div className="ml-1">{badge}</div>}
      {action && <div className="ml-auto">{action}</div>}
    </div>
  )
}
