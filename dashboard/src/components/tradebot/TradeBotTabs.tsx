import React from 'react'
import clsx from 'clsx'

interface TabDef {
  id: string
  label: string
  count?: number
}

interface Props {
  activeTab: string
  onTabChange: (tab: string) => void
  tabs: TabDef[]
}

export default function TradeBotTabs({ activeTab, onTabChange, tabs }: Props) {
  return (
    <div className="inline-flex w-fit flex-wrap gap-1 rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] p-1.5 shadow-[0_20px_40px_-30px_var(--shadow-color)]">
      {tabs.map((tab) => {
        const isActive = activeTab === tab.id
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => onTabChange(tab.id)}
            className={clsx(
              'flex items-center gap-1.5 rounded-2xl px-3.5 py-2 text-[13px] font-sans font-medium transition-all duration-150',
              isActive
                ? 'bg-[var(--accent)] text-white shadow-[0_18px_32px_-18px_rgba(245,158,11,0.7)]'
                : 'text-[var(--text-secondary)] hover:bg-[var(--bg-card)] hover:text-[var(--text-primary)]',
            )}
          >
            <span>{tab.label}</span>
            {tab.count != null && (
              <span
                className={clsx(
                  'text-[10px] font-mono px-1.5 py-0.5 rounded-md leading-none',
                  isActive
                    ? 'bg-white/20 text-white'
                    : 'bg-[var(--bg-card)] text-[var(--text-muted)]',
                )}
              >
                {tab.count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
