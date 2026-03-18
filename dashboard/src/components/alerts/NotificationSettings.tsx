/**
 * NotificationSettings — collapsible notification preferences panel.
 *
 * Can be embedded inside AlertForm (as a collapsible section) or used
 * standalone on the AlertsPage settings tab.
 *
 * Props:
 *   prefs        — current NotificationPrefs
 *   onChange     — called with a partial update whenever any pref changes
 *   onTestSound  — optional callback to play a preview of the selected sound
 *   compact      — when true renders as a collapsible card (default: false → flat)
 */
import { useState, useCallback } from 'react'
import type { NotificationPrefs, AlertSoundId } from '@/types'
import { useNotifications } from '@/hooks/useNotifications'

// ── Constants ─────────────────────────────────────────────────────────────────

const SOUND_OPTIONS: { id: AlertSoundId; label: string; description: string }[] = [
  { id: 'ding',          label: 'Ding',          description: 'Single high tone' },
  { id: 'chime',         label: 'Chime',         description: 'Three-note ascending' },
  { id: 'alarm',         label: 'Alarm',         description: 'Alternating pulse' },
  { id: 'cash_register', label: 'Cash Register', description: 'Classic ka-ching' },
]

// ── Style helpers ─────────────────────────────────────────────────────────────

const TOGGLE_BASE = 'relative inline-flex w-9 h-5 rounded-full transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-offset-1 focus:ring-offset-transparent shrink-0'
const TOGGLE_ON   = 'bg-indigo-500 focus:ring-indigo-300'
const TOGGLE_OFF  = 'bg-zinc-800 focus:ring-zinc-200'

const KNOB_BASE = 'absolute top-0.5 w-4 h-4 rounded-full shadow-md transition-transform duration-200'
const KNOB_ON   = 'translate-x-4 bg-zinc-900'
const KNOB_OFF  = 'translate-x-0.5 bg-zinc-600'

// ── Toggle component ──────────────────────────────────────────────────────────

function Toggle({
  checked,
  onChange,
  ariaLabel,
  disabled,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  ariaLabel: string
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={[
        TOGGLE_BASE,
        checked ? TOGGLE_ON : TOGGLE_OFF,
        disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer',
      ].join(' ')}
    >
      <span className={[KNOB_BASE, checked ? KNOB_ON : KNOB_OFF].join(' ')} />
    </button>
  )
}

// ── Row component ─────────────────────────────────────────────────────────────

function PrefRow({
  label,
  description,
  checked,
  onChange,
  disabled,
  ariaLabel,
  children,
}: {
  label: string
  description?: string
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
  ariaLabel: string
  children?: React.ReactNode
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-2.5">
      <div className="flex-1 min-w-0">
        <p className="text-xs font-sans font-semibold text-zinc-200">{label}</p>
        {description && (
          <p className="text-[11px] font-sans text-zinc-500 mt-0.5 leading-snug">{description}</p>
        )}
        {children}
      </div>
      <Toggle
        checked={checked}
        onChange={onChange}
        ariaLabel={ariaLabel}
        disabled={disabled}
      />
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  prefs:       NotificationPrefs
  onChange:    (partial: Partial<NotificationPrefs>) => void
  onTestSound?: () => void
  compact?:    boolean
}

export default function NotificationSettings({ prefs, onChange, onTestSound, compact = false }: Props) {
  const [open, setOpen]       = useState(!compact)
  const { permission, request } = useNotifications()

  const handleBrowserPushToggle = useCallback(
    async (enabled: boolean) => {
      if (enabled && permission !== 'granted') {
        const granted = await request()
        if (!granted) return
      }
      onChange({ browser_push: enabled })
    },
    [permission, request, onChange],
  )

  const inner = (
    <div className="space-y-1 divide-y divide-zinc-800">
      {/* In-app toast */}
      <PrefRow
        label="In-app toast"
        description="Show a toast notification inside the dashboard."
        checked={prefs.in_app}
        onChange={(v) => onChange({ in_app: v })}
        ariaLabel="Toggle in-app notifications"
      />

      {/* Sound */}
      <PrefRow
        label="Sound"
        description="Play a sound when an alert fires."
        checked={prefs.sound_enabled}
        onChange={(v) => onChange({ sound_enabled: v })}
        ariaLabel="Toggle sound notifications"
      >
        {/* Sound controls — only shown when sound is on */}
        {prefs.sound_enabled && (
          <div className="mt-2.5 space-y-2.5">
            {/* Sound picker */}
            <div className="flex items-center gap-2 flex-wrap">
              {SOUND_OPTIONS.map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  title={opt.description}
                  onClick={() => onChange({ sound: opt.id })}
                  className={[
                    'px-2.5 py-1 rounded-lg border text-[11px] font-sans font-semibold transition-all duration-100',
                    prefs.sound === opt.id
                      ? 'bg-indigo-100 text-indigo-600 border-indigo-200'
                      : 'border-zinc-800 text-zinc-400 hover:border-zinc-800 hover:text-zinc-200',
                  ].join(' ')}
                >
                  {opt.label}
                </button>
              ))}
              {onTestSound && (
                <button
                  type="button"
                  onClick={onTestSound}
                  title="Play preview"
                  className={[
                    'flex items-center gap-1 px-2.5 py-1 rounded-lg border border-zinc-800',
                    'text-[11px] font-sans font-medium text-zinc-500',
                    'hover:text-indigo-600 hover:border-indigo-200',
                    'transition-all duration-100',
                  ].join(' ')}
                >
                  <svg viewBox="0 0 24 24" fill="currentColor" className="w-3 h-3">
                    <path d="M8 5v14l11-7z" />
                  </svg>
                  Preview
                </button>
              )}
            </div>

            {/* Volume slider */}
            <div className="flex items-center gap-3">
              {/* Mute toggle */}
              <button
                type="button"
                title={prefs.muted ? 'Unmute' : 'Mute'}
                onClick={() => onChange({ muted: !prefs.muted })}
                className={[
                  'flex items-center justify-center w-6 h-6 rounded-md border transition-colors',
                  prefs.muted
                    ? 'border-red-300 text-red-400 bg-red-500/10'
                    : 'border-zinc-800 text-zinc-500 hover:text-zinc-400',
                ].join(' ')}
              >
                {prefs.muted ? (
                  <svg viewBox="0 0 24 24" fill="currentColor" className="w-3.5 h-3.5">
                    <path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z" />
                  </svg>
                ) : (
                  <svg viewBox="0 0 24 24" fill="currentColor" className="w-3.5 h-3.5">
                    <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02z" />
                  </svg>
                )}
              </button>

              {/* Slider */}
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={prefs.volume}
                disabled={prefs.muted}
                onChange={(e) => onChange({ volume: Number(e.target.value) })}
                className="flex-1 h-1.5 accent-indigo-500 disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed"
                aria-label="Volume"
              />
              <span className="text-[10px] font-mono text-zinc-500 w-7 text-right">
                {Math.round(prefs.volume * 100)}%
              </span>
            </div>
          </div>
        )}
      </PrefRow>

      {/* Browser push */}
      <PrefRow
        label="Browser push"
        description={
          permission === 'denied'
            ? 'Blocked by browser — change in site settings.'
            : 'Show OS-level notification even when tab is in background.'
        }
        checked={prefs.browser_push}
        onChange={(v) => { void handleBrowserPushToggle(v) }}
        disabled={permission === 'denied'}
        ariaLabel="Toggle browser push notifications"
      >
        {permission === 'default' && !prefs.browser_push && (
          <button
            type="button"
            onClick={() => { void handleBrowserPushToggle(true) }}
            className="mt-1.5 text-[11px] font-sans font-medium text-indigo-600 hover:underline"
          >
            Request permission
          </button>
        )}
        {permission === 'denied' && (
          <p className="mt-1 text-[10px] font-sans text-red-400">
            Permission denied. Enable notifications for this site in browser settings.
          </p>
        )}
      </PrefRow>
    </div>
  )

  if (!compact) {
    return inner
  }

  // Compact: collapsible card
  return (
    <div className="rounded-xl border border-zinc-800 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-3 bg-zinc-900 hover:bg-zinc-800 transition-colors"
      >
        <div className="flex items-center gap-2">
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-3.5 h-3.5 text-zinc-400">
            <path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.89 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z" />
          </svg>
          <span className="text-xs font-sans font-semibold text-zinc-400">Notification settings</span>
        </div>
        <svg
          viewBox="0 0 24 24"
          fill="currentColor"
          className={`w-3.5 h-3.5 text-zinc-500 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
        >
          <path d="M7 10l5 5 5-5z" />
        </svg>
      </button>
      {open && (
        <div className="px-4 pb-3 pt-1 bg-zinc-900">
          {inner}
        </div>
      )}
    </div>
  )
}
