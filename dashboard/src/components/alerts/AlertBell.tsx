/**
 * AlertBell — header bell icon with unread badge and fired-alert dropdown.
 *
 * Data:  useAlertStore  (unreadCount, recentFired, markRead)
 * Nav:   useUIStore     (setRoute → 'alerts')
 */
import { useRef, useState, useEffect, useCallback } from 'react'
import { useAlertStore, useUIStore } from '@/store'
import type { AlertFiredEvent } from '@/types'

// ── Helpers ──────────────────────────────────────────────────────────────────

function timeAgo(isoStr: string): string {
  const diff = Date.now() - new Date(isoStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function fmtPrice(price: number): string {
  return '$' + price.toFixed(2)
}

// ── Sub-components ────────────────────────────────────────────────────────────

interface FiredItemProps {
  event: AlertFiredEvent
}

function FiredItem({ event }: FiredItemProps) {
  return (
    <div className="px-3 py-2.5 border-b border-white/[0.06] last:border-0 hover:bg-white/[0.04] cursor-pointer transition-colors">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-sans font-semibold text-terminal-text truncate">
          {event.name}
        </span>
        {/* timestamp — keep font-mono */}
        <span className="text-[10px] font-mono text-terminal-ghost shrink-0">
          {timeAgo(event.timestamp)}
        </span>
      </div>
      <div className="flex items-center gap-2 mt-0.5">
        {/* ticker — keep font-mono */}
        <span className="text-[10px] font-mono text-terminal-amber">{event.symbol}</span>
        {/* price — keep font-mono */}
        <span className="text-[10px] font-mono text-terminal-green">{fmtPrice(event.price)}</span>
        <span className="text-[10px] font-sans text-terminal-dim truncate">
          {event.condition_summary}
        </span>
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function AlertBell() {
  const unreadCount = useAlertStore((s) => s.unreadCount)
  const recentFired = useAlertStore((s) => s.recentFired)
  const markRead    = useAlertStore((s) => s.markRead)
  const setRoute    = useUIStore((s) => s.setRoute)

  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  // Close on outside click
  useEffect(() => {
    if (!open) return

    function handleMouseDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }

    document.addEventListener('mousedown', handleMouseDown)
    return () => document.removeEventListener('mousedown', handleMouseDown)
  }, [open])

  const handleBellClick = useCallback(() => {
    setOpen((prev) => {
      if (!prev) markRead()
      return !prev
    })
  }, [markRead])

  const handleViewAll = useCallback(() => {
    setOpen(false)
    setRoute('alerts')
  }, [setRoute])

  const recent5 = recentFired.slice(0, 5)

  return (
    <div ref={containerRef} className="relative">
      {/* Bell button */}
      <button
        onClick={handleBellClick}
        aria-label="Alert notifications"
        className="relative text-terminal-ghost hover:text-terminal-amber transition-colors p-1.5 rounded-lg hover:bg-white/[0.06]"
      >
        <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
          <path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.89 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z" />
        </svg>

        {/* Unread badge — gradient background */}
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 bg-gradient-to-br from-red-400 to-red-600 text-white text-[9px] font-bold min-w-[16px] h-4 px-0.5 rounded-full flex items-center justify-center leading-none">
            {unreadCount > 99 ? '99' : unreadCount}
          </span>
        )}
      </button>

      {/* Dropdown panel */}
      {open && (
        <div className="absolute top-full right-0 mt-2 w-80 glass-elevated rounded-2xl shadow-glass-lg z-50 overflow-hidden">
          {/* Header */}
          <div className="px-3 py-2.5 border-b border-white/[0.06] flex items-center justify-between">
            <span className="text-xs font-sans font-semibold text-terminal-text">
              Recent Alerts
            </span>
            <button
              onClick={handleViewAll}
              className="text-indigo-400 text-[10px] font-sans font-medium hover:text-indigo-300 hover:underline transition-colors"
            >
              View All
            </button>
          </div>

          {/* Fired items */}
          {recent5.length === 0 ? (
            <div className="px-3 py-5 text-center text-xs font-sans text-terminal-ghost">
              No recent alerts
            </div>
          ) : (
            <div>
              {recent5.map((event) => (
                <FiredItem key={`${event.alert_id}-${event.timestamp}`} event={event} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
