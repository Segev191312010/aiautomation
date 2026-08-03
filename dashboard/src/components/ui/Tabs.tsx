import React from 'react'
import { cn } from '@/utils/cn'

export interface TabItem {
  id: string
  label: string
  count?: number
}

export interface TabsProps {
  items: TabItem[]
  activeId: string
  onChange: (id: string) => void
  className?: string
}

export function Tabs({ items, activeId, onChange, className }: TabsProps) {
  const refs = React.useRef<Record<string, HTMLButtonElement | null>>({})

  const handleKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>, id: string) => {
    const idx = items.findIndex((t) => t.id === id)
    if (idx === -1) return
    let nextIdx: number | null = null
    if (e.key === 'ArrowRight') nextIdx = (idx + 1) % items.length
    if (e.key === 'ArrowLeft') nextIdx = (idx - 1 + items.length) % items.length
    if (nextIdx !== null) {
      e.preventDefault()
      const nextId = items[nextIdx].id
      onChange(nextId)
      refs.current[nextId]?.focus()
    }
  }

  return (
    <div
      role="tablist"
      aria-orientation="horizontal"
      className={cn(
        'flex items-center gap-1 border-b border-theme-border overflow-x-auto',
        className,
      )}
    >
      {items.map((item) => {
        const isActive = item.id === activeId
        return (
          <button
            key={item.id}
            ref={(el) => { refs.current[item.id] = el }}
            role="tab"
            aria-selected={isActive}
            tabIndex={isActive ? 0 : -1}
            onClick={() => onChange(item.id)}
            onKeyDown={(e) => handleKeyDown(e, item.id)}
            className={cn(
              'relative px-3 py-2 text-sm font-sans font-medium transition-colors whitespace-nowrap',
              'focus-visible:outline-none focus-visible:text-theme-accent',
              isActive
                ? 'text-theme-text'
                : 'text-theme-muted hover:text-theme-dim',
            )}
          >
            {item.label}
            {item.count !== undefined && (
              <span className="ml-1.5 text-xs text-theme-muted">{item.count}</span>
            )}
            {isActive && (
              <span
                className="absolute bottom-0 left-0 right-0 h-0.5 rounded-full bg-theme-accent"
                aria-hidden="true"
              />
            )}
          </button>
        )
      })}
    </div>
  )
}