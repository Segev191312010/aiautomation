import React, { useEffect, useState } from 'react'

// Browser's beforeinstallprompt event type
interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

const DISMISS_KEY = 'pwa_prompt_dismissed_session'

/**
 * Subtle bottom banner that appears when the PWA install prompt is available.
 * Shows once per browser session; user can dismiss it permanently for the session.
 */
export default function PWAInstallPrompt() {
  const [promptEvent, setPromptEvent] = useState<BeforeInstallPromptEvent | null>(null)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    // Already dismissed this session
    if (sessionStorage.getItem(DISMISS_KEY)) return

    const handler = (e: Event) => {
      e.preventDefault()
      setPromptEvent(e as BeforeInstallPromptEvent)
      setVisible(true)
    }
    window.addEventListener('beforeinstallprompt', handler)
    return () => window.removeEventListener('beforeinstallprompt', handler)
  }, [])

  const handleInstall = async () => {
    if (!promptEvent) return
    await promptEvent.prompt()
    const { outcome } = await promptEvent.userChoice
    if (outcome === 'accepted') {
      setVisible(false)
    }
    setPromptEvent(null)
  }

  const handleDismiss = () => {
    sessionStorage.setItem(DISMISS_KEY, '1')
    setVisible(false)
  }

  if (!visible || !promptEvent) return null

  return (
    <div
      role="banner"
      className="fixed bottom-5 left-1/2 -translate-x-1/2 z-[100] w-full max-w-sm mx-4 animate-fade-in-up"
    >
      <div
        className="card rounded-2xl shadow-dropdown flex items-center gap-3 px-4 py-3"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        {/* Icon */}
        <div
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
          style={{ background: 'color-mix(in srgb, var(--accent) 12%, transparent)' }}
        >
          <svg viewBox="0 0 24 24" fill="none" className="w-4.5 h-4.5" aria-hidden="true">
            <path
              d="M12 2v13M8 11l4 4 4-4M3 17v2a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-2"
              stroke="var(--accent)"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>

        {/* Text */}
        <div className="flex-1 min-w-0">
          <p className="text-xs font-sans font-semibold truncate" style={{ color: 'var(--text-primary)' }}>
            Install Trading Platform
          </p>
          <p className="text-[10px] font-sans truncate" style={{ color: 'var(--text-muted)' }}>
            For a faster, native-like experience
          </p>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={handleInstall}
            className="rounded-lg px-3 py-1.5 text-[11px] font-sans font-semibold text-white transition-all hover:opacity-90"
            style={{ background: 'var(--accent)' }}
          >
            Install
          </button>
          <button
            onClick={handleDismiss}
            className="p-1.5 rounded-lg transition-colors hover:opacity-70"
            style={{ color: 'var(--text-muted)' }}
            aria-label="Dismiss install prompt"
          >
            <svg viewBox="0 0 24 24" fill="none" className="w-3.5 h-3.5" aria-hidden="true">
              <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  )
}
