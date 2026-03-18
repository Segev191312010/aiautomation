/**
 * SimController — floating bottom bar for historical replay control.
 *
 * Shows:
 *  • Simulation Mode badge
 *  • Load symbol + timeframe inputs
 *  • Play / Pause button with status dot
 *  • Progress bar
 *  • Speed selector (1×, 2×, 5×, 10×, 20×)
 *  • Current sim timestamp
 */
import React, { useState } from 'react'
import clsx from 'clsx'
import { useSimStore, useMarketStore } from '@/store'
import { loadReplay, playReplay, pauseReplay, stopReplay, setReplaySpeed } from '@/services/api'
import { format, fromUnixTime } from 'date-fns'

const SPEEDS = [1, 2, 5, 10, 20]

const PERIODS = [
  { label: '1M',  value: '1mo',  interval: '1d' },
  { label: '3M',  value: '3mo',  interval: '1d' },
  { label: '6M',  value: '6mo',  interval: '1d' },
  { label: '1Y',  value: '1y',   interval: '1d' },
  { label: '2Y',  value: '2y',   interval: '1wk' },
  { label: '5Y',  value: '5y',   interval: '1mo' },
]

/** Thin icon components — inline SVG, no external deps */
function IconPlay() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className="w-3.5 h-3.5">
      <path d="M8 5v14l11-7z" />
    </svg>
  )
}

function IconPause() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className="w-3.5 h-3.5">
      <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" />
    </svg>
  )
}

function IconStop() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className="w-3.5 h-3.5">
      <path d="M6 6h2v12H6zm3.5 6 8.5 6V6z" />
    </svg>
  )
}

function IconClock() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="w-3.5 h-3.5">
      <circle cx="12" cy="12" r="9" />
      <path strokeLinecap="round" d="M12 7v5l3 3" />
    </svg>
  )
}

function IconBar() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="w-3.5 h-3.5">
      <path strokeLinecap="round" strokeLinejoin="round" d="M3 17l4-8 5 5 4-7 5 4" />
    </svg>
  )
}

export default function SimController() {
  const { playback, setPlayback, resetReplayBars } = useSimStore()
  const selectedSymbol = useMarketStore((s) => s.selectedSymbol)

  const [sym, setSym]       = useState(selectedSymbol)
  const [period, setPeriod] = useState(PERIODS[3])
  const [loading, setLoading] = useState(false)

  const pctDone = playback.progress * 100
  const currentDate =
    playback.current_ts
      ? format(fromUnixTime(playback.current_ts), 'MMM d, yyyy')
      : '—'

  const handleLoad = async () => {
    setLoading(true)
    resetReplayBars()
    try {
      const state = await loadReplay(sym.toUpperCase(), period.value, period.interval)
      setPlayback(state)
    } catch (e) {
      console.error('Failed to load replay:', e)
    } finally {
      setLoading(false)
    }
  }

  const handlePlayPause = async () => {
    try {
      if (playback.active) {
        const state = await pauseReplay()
        setPlayback(state)
      } else {
        const state = await playReplay()
        setPlayback(state)
      }
    } catch (e) {
      console.error(e)
    }
  }

  const handleStop = async () => {
    try {
      const state = await stopReplay()
      setPlayback(state)
      resetReplayBars()
    } catch (e) {
      console.error(e)
    }
  }

  const handleSpeed = async (speed: number) => {
    try {
      await setReplaySpeed(speed)
      setPlayback({ ...playback, speed })
    } catch (e) {
      console.error(e)
    }
  }

  return (
    <div className="fixed bottom-0 left-0 right-0 z-30 card-elevated border-t border-zinc-800 -lg rounded-t-2xl">
      {/* ── Progress bar ────────────────────────────────────────────────── */}
      <div className="relative h-[3px] bg-zinc-800/60 rounded-t-2xl overflow-hidden">
        <div
          className={clsx(
            'absolute inset-y-0 left-0 rounded-full transition-all duration-500',
            playback.active ? 'bg-amber-600 shadow-[0_0_8px_rgba(245,158,11,0.5)]' : 'bg-indigo-500',
          )}
          style={{ width: `${pctDone}%` }}
        />
      </div>

      <div className="max-w-screen-2xl mx-auto px-5 py-2.5">
        <div className="flex items-center gap-4 flex-wrap">

          {/* ── Simulation Mode badge + status dot ──────────────────────── */}
          <div className="flex items-center gap-2 shrink-0">
            <span className="inline-flex items-center gap-1.5 text-[10px] font-sans font-semibold tracking-widest uppercase px-2.5 py-1 rounded-lg bg-amber-500/15 border border-amber-500/30 text-amber-600">
              {/* status dot */}
              <span
                className={clsx(
                  'inline-block w-1.5 h-1.5 rounded-full',
                  playback.active
                    ? 'bg-emerald-600 animate-pulse shadow-[0_0_6px_rgba(16,185,129,0.8)]'
                    : 'bg-zinc-600',
                )}
              />
              Sim Mode
            </span>
          </div>

          {/* ── Divider ─────────────────────────────────────────────────── */}
          <div className="h-5 w-px bg-zinc-900/[0.07] shrink-0" />

          {/* ── Symbol / period loader ───────────────────────────────────── */}
          <div className="flex items-center gap-2">
            <input
              value={sym}
              onChange={(e) => setSym(e.target.value.toUpperCase())}
              placeholder="AAPL"
              className={clsx(
                'w-20 text-xs font-mono bg-zinc-900 border rounded-xl px-2.5 py-1.5',
                'text-zinc-100 placeholder:text-zinc-500',
                'border-zinc-800 focus:border-indigo-600/60 focus:outline-none',
                'transition-colors',
              )}
            />
            <div className="flex gap-0.5">
              {PERIODS.map((p) => (
                <button
                  key={p.value}
                  onClick={() => setPeriod(p)}
                  className={clsx(
                    'text-[10px] font-sans px-1.5 py-1 rounded-lg border transition-colors',
                    period.value === p.value
                      ? 'border-indigo-600/50 text-indigo-600 bg-indigo-50 font-semibold'
                      : 'border-zinc-800 text-zinc-500 hover:text-zinc-400 hover:border-white/[0.12]',
                  )}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <button
              onClick={handleLoad}
              disabled={loading || !sym}
              className={clsx(
                'text-[11px] font-sans font-medium px-3 py-1.5 rounded-xl border transition-colors',
                'bg-indigo-50 border-indigo-100 text-indigo-600',
                'hover:bg-indigo-100 hover:border-indigo-600/50',
                'disabled:opacity-40 disabled:cursor-not-allowed',
              )}
            >
              {loading ? (
                <span className="flex items-center gap-1">
                  <svg className="animate-spin w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <circle cx="12" cy="12" r="10" strokeOpacity="0.25" />
                    <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
                  </svg>
                  Loading
                </span>
              ) : (
                'Load'
              )}
            </button>
          </div>

          {/* ── Divider ─────────────────────────────────────────────────── */}
          <div className="h-5 w-px bg-zinc-900/[0.07] shrink-0" />

          {/* ── Play / Pause / Stop ──────────────────────────────────────── */}
          <div className="flex items-center gap-1.5">
            <button
              onClick={handlePlayPause}
              disabled={playback.total_bars === 0}
              className={clsx(
                'flex items-center gap-1.5 text-[11px] font-sans font-semibold px-3.5 py-1.5 rounded-xl border transition-all disabled:opacity-40 disabled:cursor-not-allowed',
                playback.active
                  ? 'border-amber-300/50 text-amber-600 bg-amber-600/10 hover:bg-amber-600/18 shadow-[0_0_12px_rgba(245,158,11,0.12)]'
                  : 'border-emerald-300/40 text-emerald-400 bg-emerald-600/10 hover:bg-emerald-600/18 shadow-[0_0_12px_rgba(16,185,129,0.1)]',
              )}
            >
              {playback.active ? <><IconPause /> Pause</> : <><IconPlay /> Play</>}
            </button>

            <button
              onClick={handleStop}
              disabled={playback.total_bars === 0}
              title="Reset to beginning"
              className={clsx(
                'p-1.5 rounded-xl border border-zinc-800 text-zinc-500',
                'hover:text-zinc-400 hover:border-white/[0.14] hover:bg-zinc-900',
                'transition-colors disabled:opacity-40 disabled:cursor-not-allowed',
              )}
            >
              <IconStop />
            </button>
          </div>

          {/* ── Divider ─────────────────────────────────────────────────── */}
          <div className="h-5 w-px bg-zinc-900/[0.07] shrink-0" />

          {/* ── Speed ────────────────────────────────────────────────────── */}
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] font-sans font-medium text-zinc-500 tracking-wide uppercase">
              Speed
            </span>
            {SPEEDS.map((s) => (
              <button
                key={s}
                onClick={() => handleSpeed(s)}
                className={clsx(
                  'text-[10px] font-mono w-7 py-1 rounded-lg border transition-colors',
                  playback.speed === s
                    ? 'border-indigo-600/50 text-indigo-600 bg-indigo-50 font-bold'
                    : 'border-zinc-800 text-zinc-500 hover:text-zinc-400 hover:border-white/[0.12]',
                )}
              >
                {s}×
              </button>
            ))}
          </div>

          {/* ── Progress info ─────────────────────────────────────────────── */}
          <div className="ml-auto flex items-center gap-4 shrink-0">
            {playback.total_bars > 0 && (
              <>
                <div className="flex items-center gap-1.5 text-[11px] font-mono text-zinc-400">
                  <IconBar />
                  <span className="tabular-nums">
                    {playback.current_index + 1}
                    <span className="text-zinc-500"> / </span>
                    {playback.total_bars}
                    <span className="text-zinc-500 ml-1">bars</span>
                  </span>
                </div>
                <div className="flex items-center gap-1.5 text-[11px] font-mono text-zinc-100">
                  <IconClock />
                  <span>{currentDate}</span>
                  <span className="text-zinc-500 text-[10px]">({pctDone.toFixed(0)}%)</span>
                </div>
              </>
            )}
            {playback.symbol && (
              <span className="text-[11px] font-mono font-semibold text-indigo-600 tracking-wide">
                {playback.symbol}
              </span>
            )}
          </div>

        </div>
      </div>
    </div>
  )
}
