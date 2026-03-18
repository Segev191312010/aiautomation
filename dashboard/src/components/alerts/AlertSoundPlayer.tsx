/**
 * AlertSoundPlayer — invisible component that plays tones when alerts fire.
 *
 * Uses Web Audio API only (no audio files needed). Subscribes to the alert
 * store's recentFired list and plays the configured sound whenever a new event
 * arrives. Also exposes a test-play function via the imperative ref pattern so
 * NotificationSettings can trigger a preview.
 *
 * Exported helpers:
 *   useAlertSoundPlayer() — hook that returns { testPlay }
 */
import {
  useEffect,
  useRef,
  useCallback,
  forwardRef,
  useImperativeHandle,
} from 'react'
import { useAlertStore } from '@/store'
import type { NotificationPrefs, AlertSoundId } from '@/types'

// ── Sound synthesis definitions ───────────────────────────────────────────────

interface ToneStep {
  freq:      number
  duration:  number   // seconds
  type?:     OscillatorType
  gainStart?: number
  gainEnd?:  number
}

const SOUND_SEQUENCES: Record<AlertSoundId, ToneStep[]> = {
  ding: [
    { freq: 880, duration: 0.08, type: 'sine', gainStart: 0.6, gainEnd: 0.0 },
  ],
  chime: [
    { freq: 523.25, duration: 0.12, type: 'sine', gainStart: 0.5, gainEnd: 0.3 },
    { freq: 659.25, duration: 0.12, type: 'sine', gainStart: 0.5, gainEnd: 0.3 },
    { freq: 783.99, duration: 0.18, type: 'sine', gainStart: 0.5, gainEnd: 0.0 },
  ],
  alarm: [
    { freq: 440, duration: 0.1, type: 'sawtooth', gainStart: 0.3, gainEnd: 0.3 },
    { freq: 220, duration: 0.1, type: 'sawtooth', gainStart: 0.3, gainEnd: 0.3 },
    { freq: 440, duration: 0.1, type: 'sawtooth', gainStart: 0.3, gainEnd: 0.3 },
    { freq: 220, duration: 0.1, type: 'sawtooth', gainStart: 0.3, gainEnd: 0.0 },
  ],
  cash_register: [
    { freq: 1318.5, duration: 0.06, type: 'square', gainStart: 0.25, gainEnd: 0.25 },
    { freq: 1046.5, duration: 0.06, type: 'square', gainStart: 0.25, gainEnd: 0.25 },
    { freq: 1318.5, duration: 0.10, type: 'square', gainStart: 0.25, gainEnd: 0.0 },
  ],
}

// ── Audio engine ──────────────────────────────────────────────────────────────

function playSequence(ctx: AudioContext, steps: ToneStep[], volume: number): void {
  let startTime = ctx.currentTime + 0.01
  for (const step of steps) {
    const osc  = ctx.createOscillator()
    const gain = ctx.createGain()

    osc.type      = step.type ?? 'sine'
    osc.frequency.setValueAtTime(step.freq, startTime)

    const gStart = (step.gainStart ?? 0.5) * volume
    const gEnd   = (step.gainEnd ?? 0.0) * volume
    gain.gain.setValueAtTime(gStart, startTime)
    gain.gain.linearRampToValueAtTime(gEnd, startTime + step.duration)

    osc.connect(gain)
    gain.connect(ctx.destination)

    osc.start(startTime)
    osc.stop(startTime + step.duration + 0.01)

    startTime += step.duration
  }
}

// ── Imperative handle shape ───────────────────────────────────────────────────

export interface AlertSoundPlayerHandle {
  testPlay: (soundId?: AlertSoundId) => void
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  prefs: NotificationPrefs
}

// ── Component ────────────────────────────────────────────────────────────────

const AlertSoundPlayer = forwardRef<AlertSoundPlayerHandle, Props>(
  function AlertSoundPlayer({ prefs }, ref) {
    const recentFired  = useAlertStore((s) => s.recentFired)
    const prevCountRef = useRef(recentFired.length)
    const audioCtxRef  = useRef<AudioContext | null>(null)

    // Lazily create AudioContext on first interaction to satisfy autoplay policy
    const getCtx = useCallback((): AudioContext | null => {
      if (typeof AudioContext === 'undefined' && typeof (window as unknown as { webkitAudioContext?: unknown }).webkitAudioContext === 'undefined') {
        return null
      }
      if (!audioCtxRef.current) {
        const Ctor = (
          window.AudioContext ??
          (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
        )
        audioCtxRef.current = new Ctor()
      }
      // Resume if suspended (browser autoplay policy)
      if (audioCtxRef.current.state === 'suspended') {
        void audioCtxRef.current.resume()
      }
      return audioCtxRef.current
    }, [])

    const play = useCallback(
      (soundId: AlertSoundId) => {
        if (prefs.muted) return
        const ctx = getCtx()
        if (!ctx) return
        const steps = SOUND_SEQUENCES[soundId]
        if (!steps) return
        playSequence(ctx, steps, prefs.volume)
      },
      [prefs.muted, prefs.volume, getCtx],
    )

    // Expose testPlay to parent via ref
    useImperativeHandle(
      ref,
      () => ({
        testPlay: (soundId?: AlertSoundId) => play(soundId ?? prefs.sound),
      }),
      [play, prefs.sound],
    )

    // Fire sound whenever a new alert arrives
    useEffect(() => {
      const newCount = recentFired.length
      if (newCount > prevCountRef.current && prefs.sound_enabled) {
        play(prefs.sound)
      }
      prevCountRef.current = newCount
    }, [recentFired.length, prefs.sound_enabled, prefs.sound, play])

    // Cleanup AudioContext on unmount
    useEffect(() => {
      return () => {
        audioCtxRef.current?.close().catch(() => { /* ignore */ })
      }
    }, [])

    // Render nothing — purely behavioural
    return null
  },
)

export default AlertSoundPlayer
