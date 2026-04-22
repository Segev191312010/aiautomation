/**
 * AlertBell — header bell icon with unread badge and fired-alert dropdown.
 *
 * Data:  useAlertStore  (unreadCount, recentFired, markRead)
 * Nav:   navigateToRoute('alerts') from @/utils/routes
 */
import { useRef, useState, useEffect, useCallback } from 'react'
import { useAlertStore } from '@/store'
import type { AlertFiredEvent } from '@/types'
import { navigateToRoute } from '@/utils/routes'

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
    <div className="group px-4 py-3 border-b border-zinc-800 last:border-0 hover:bg-zinc-800/60 cursor-pointer transition-colors">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          {/* Amber trigger dot */}
          <span className="w-1.5 h-1.5 rounded-full bg-amber-600 shrink-0 mt-0.5" />
          <span className="text-xs font-sans font-semibold text-zinc-100 truncate">
            {event.name}
          </span>
        </div>
        {/* timestamp — keep font-mono */}
        <span className="text-[10px] font-mono text-zinc-500 shrink-0">
          {timeAgo(event.timestamp)}
        </span>
      </div>
      <div className="flex items-center gap-2 mt-1 ml-3.5 flex-wrap">
        {/* ticker — keep font-mono */}
        <span className="px-1.5 py-0.5 rounded bg-amber-50 text-[10px] font-mono font-semibold text-amber-600">
          {event.symbol}
        </span>
        {/* price — keep font-mono */}
        <span className="text-[10px] font-mono text-emerald-400 font-semibold">
          {fmtPrice(event.price)}
        </span>
        <span className="text-[10px] font-sans text-zinc-500 truncate">
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
    navigateToRoute('alerts')
  }, [])

  const recent5 = recentFired.slice(0, 5)
  const hasPending = unreadCount > 0

  return (
    <div ref={containerRef} className="relative">
      {/* ── Bell button ─────────────────────────────────────────────── */}
      <button
        onClick={handleBellClick}
        aria-label="Alert notifications"
        className={[
          'relative p-1.5 rounded-lg transition-all duration-150',
          open
            ? 'text-amber-600 bg-amber-50'
            : 'text-zinc-500 hover:text-amber-600 hover:bg-zinc-800',
        ].join(' ')}
      >
        {/* Bell SVG — shakes when unread */}
        <svg
          viewBox="0 0 24 24"
          fill="currentColor"
          className={`w-5 h-5 ${hasPending ? 'animate-pulse-slow' : ''}`}
        >
          <path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.89 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z" />
        </svg>

        {/* Unread badge with pulsing ring */}
        {hasPending && (
          <>
            {/* Pulsing outer ring */}
            <span className="absolute -top-0.5 -right-0.5 w-4 h-4 rounded-full bg-red-500/30 animate-ping" />
            {/* Solid badge */}
            <span className="absolute -top-0.5 -right-0.5 flex items-center justify-center min-w-[16px] h-4 px-0.5 rounded-full bg-gradient-to-br from-red-400 to-red-600 text-white text-[9px] font-bold leading-none shadow-glow-red">
              {unreadCount > 99 ? '99' : unreadCount}
            </span>
          </>
        )}
      </button>

      {/* ── Dropdown panel ──────────────────────────────────────────── */}
      {open && (
        <div className="absolute top-full right-0 mt-2 w-80 card-elevated rounded-2xl -lg z-50 overflow-hidden border border-zinc-800 animate-fade-in">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
            <div className="flex items-center gap-2">
              <svg viewBox="0 0 24 24" fill="currentColor" className="w-3.5 h-3.5 text-amber-600">
                <path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.89 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z" />
              </svg>
              <span className="text-xs font-sans font-semibold text-zinc-100">
                Recent Alerts
              </span>
              {recent5.length > 0 && (
                <span className="px-1.5 py-0.5 rounded bg-amber-50 text-amber-600 text-[10px] font-mono font-semibold">
                  {recent5.length}
                </span>
              )}
            </div>
            <button
              onClick={handleViewAll}
              className="text-[11px] font-sans font-medium text-indigo-600 hover:text-indigo-600 transition-colors"
            >
              View All
            </button>
          </div>

          {/* Fired items */}
          {recent5.length === 0 ? (
            <div className="flex flex-col items-center justify-center px-4 py-7 gap-2">
              <svg viewBox="0 0 24 24" fill="currentColor" className="w-6 h-6 text-zinc-500/40">
                <path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.89 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z" />
              </svg>
              <p className="text-xs font-sans text-zinc-500 text-center">
                No recent alerts
              </p>
            </div>
          ) : (
            <div>
              {recent5.map((event) => (
                <FiredItem key={`${event.alert_id}-${event.timestamp}`} event={event} />
              ))}
            </div>
          )}

          {/* Footer link */}
          <div className="px-4 py-2.5 border-t border-zinc-800 bg-zinc-900">
            <button
              onClick={handleViewAll}
              className="w-full text-center text-[11px] font-sans text-zinc-500 hover:text-zinc-400 transition-colors"
            >
              Open Alerts Manager
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
