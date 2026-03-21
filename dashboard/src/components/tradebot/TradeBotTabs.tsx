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
    <div className="flex gap-1 bg-[var(--bg-hover)] p-1 rounded-xl w-fit">
      {tabs.map((tab) => {
        const isActive = activeTab === tab.id
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => onTabChange(tab.id)}
            className={clsx(
              'flex items-center gap-1.5 px-3.5 py-1.5 text-[13px] font-sans font-medium transition-colors duration-100 rounded-lg',
              isActive
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]',
            )}
          >
            <span>{tab.label}</span>
            {tab.count != null && (
              <span
                className={clsx(
                  'text-[10px] font-mono px-1.5 py-0.5 rounded-md leading-none',
                  isActive
                    ? 'bg-white/20 text-white'
                    : 'bg-zinc-800/60 text-zinc-400',
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
