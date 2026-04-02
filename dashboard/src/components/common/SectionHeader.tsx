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
    <div className="mb-5 flex flex-wrap items-start gap-3">
      <div className="flex min-w-0 items-start gap-3">
        {icon && (
          <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-2xl border border-[var(--border)] bg-[var(--bg-hover)] text-[var(--text-primary)] shadow-[0_18px_36px_-24px_var(--shadow-color)]">
            {icon}
          </div>
        )}
        <div className="min-w-0">
          {eyebrow ? (
            <div className="shell-kicker">{eyebrow}</div>
          ) : (
            <div className="h-[0.65rem]" aria-hidden="true" />
          )}
          <h2 className="display-font mt-2 text-[1.45rem] leading-none text-[var(--text-primary)] sm:text-[1.6rem]">
            {title}
          </h2>
        </div>
      </div>
      {(badge || action) && (
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {badge && <div>{badge}</div>}
          {action && <div>{action}</div>}
        </div>
      )}
    </div>
  )
}
