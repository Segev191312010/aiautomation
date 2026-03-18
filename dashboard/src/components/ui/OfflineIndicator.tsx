import React, { useEffect, useState } from 'react'

/**
 * Thin banner shown at the top of the viewport when the browser goes offline.
 * Disappears automatically when connectivity is restored.
 */
export default function OfflineIndicator() {
  const [offline, setOffline] = useState(!navigator.onLine)

  useEffect(() => {
    const handleOffline = () => setOffline(true)
    const handleOnline  = () => setOffline(false)
    window.addEventListener('offline', handleOffline)
    window.addEventListener('online',  handleOnline)
    return () => {
      window.removeEventListener('offline', handleOffline)
      window.removeEventListener('online',  handleOnline)
    }
  }, [])

  if (!offline) return null

  return (
    <div
      role="status"
      aria-live="assertive"
      className="fixed top-0 inset-x-0 z-[150] flex items-center justify-center gap-2 py-2 px-4 text-xs font-sans font-medium text-white"
      style={{ background: 'var(--warning)' }}
    >
      <svg viewBox="0 0 24 24" fill="none" className="w-3.5 h-3.5 shrink-0" aria-hidden="true">
        <path
          d="M1 1l22 22M16.72 11.06A10.94 10.94 0 0 1 19 12.55M5 12.55a10.94 10.94 0 0 1 5.17-2.39M10.71 5.05A16 16 0 0 1 22.56 9M1.42 9a15.91 15.91 0 0 1 4.7-2.88M8.53 16.11a6 6 0 0 1 6.95 0M12 20h.01"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      No internet connection — data may be stale
    </div>
  )
}
