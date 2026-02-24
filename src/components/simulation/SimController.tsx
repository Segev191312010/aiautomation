/**
 * SimController — floating bottom bar for historical replay control.
 *
 * Shows:
 *  • Load symbol + timeframe inputs
 *  • Play / Pause button
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
    <div className="fixed bottom-0 left-0 right-0 z-30 bg-terminal-elevated border-t border-terminal-border shadow-terminal">
      <div className="max-w-screen-2xl mx-auto px-4 py-2">
        {/* ── Progress bar ────────────────────────────────────── */}
        <div className="relative h-0.5 bg-terminal-muted rounded-full mb-2 overflow-hidden">
          <div
            className="absolute inset-y-0 left-0 bg-terminal-blue rounded-full transition-all"
            style={{ width: `${pctDone}%` }}
          />
        </div>

        <div className="flex items-center gap-3 flex-wrap">
          {/* ── Symbol / period loader ───────────────────────── */}
          <div className="flex items-center gap-2">
            <input
              value={sym}
              onChange={(e) => setSym(e.target.value.toUpperCase())}
              placeholder="AAPL"
              className="w-20 text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1 text-terminal-text focus:border-terminal-blue focus:outline-none"
            />
            <div className="flex gap-0.5">
              {PERIODS.map((p) => (
                <button
                  key={p.value}
                  onClick={() => setPeriod(p)}
                  className={clsx(
                    'text-[10px] font-mono px-1.5 py-0.5 rounded border transition-colors',
                    period.value === p.value
                      ? 'border-terminal-blue/50 text-terminal-blue bg-terminal-blue/10'
                      : 'border-terminal-border text-terminal-ghost hover:text-terminal-dim',
                  )}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <button
              onClick={handleLoad}
              disabled={loading || !sym}
              className="text-[11px] font-mono px-3 py-1 rounded bg-terminal-blue/20 border border-terminal-blue/40 text-terminal-blue hover:bg-terminal-blue/30 disabled:opacity-40 transition-colors"
            >
              {loading ? 'Loading…' : 'Load'}
            </button>
          </div>

          {/* ── Separator ───────────────────────────────────── */}
          <div className="h-5 w-px bg-terminal-border" />

          {/* ── Play / Pause / Stop ─────────────────────────── */}
          <div className="flex items-center gap-1">
            <button
              onClick={handlePlayPause}
              disabled={playback.total_bars === 0}
              className={clsx(
                'flex items-center gap-1.5 text-[11px] font-mono px-3 py-1 rounded border transition-colors disabled:opacity-40',
                playback.active
                  ? 'border-terminal-amber/40 text-terminal-amber bg-terminal-amber/5 hover:bg-terminal-amber/10'
                  : 'border-terminal-green/40 text-terminal-green bg-terminal-green/5 hover:bg-terminal-green/10',
              )}
            >
              {playback.active ? (
                <>
                  <svg viewBox="0 0 24 24" fill="currentColor" className="w-3 h-3">
                    <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" />
                  </svg>
                  Pause
                </>
              ) : (
                <>
                  <svg viewBox="0 0 24 24" fill="currentColor" className="w-3 h-3">
                    <path d="M8 5v14l11-7z" />
                  </svg>
                  Play
                </>
              )}
            </button>

            <button
              onClick={handleStop}
              disabled={playback.total_bars === 0}
              title="Reset to beginning"
              className="p-1.5 rounded border border-terminal-border text-terminal-dim hover:text-terminal-text hover:border-terminal-muted transition-colors disabled:opacity-40"
            >
              <svg viewBox="0 0 24 24" fill="currentColor" className="w-3 h-3">
                <path d="M6 6h2v12H6zm3.5 6 8.5 6V6z" />
              </svg>
            </button>
          </div>

          {/* ── Speed ───────────────────────────────────────── */}
          <div className="flex items-center gap-1">
            <span className="text-[10px] font-mono text-terminal-ghost">Speed:</span>
            {SPEEDS.map((s) => (
              <button
                key={s}
                onClick={() => handleSpeed(s)}
                className={clsx(
                  'text-[10px] font-mono w-7 py-0.5 rounded border transition-colors',
                  playback.speed === s
                    ? 'border-terminal-blue/50 text-terminal-blue bg-terminal-blue/10'
                    : 'border-terminal-border text-terminal-ghost hover:text-terminal-dim',
                )}
              >
                {s}×
              </button>
            ))}
          </div>

          {/* ── Progress info ────────────────────────────────── */}
          <div className="ml-auto text-right">
            {playback.total_bars > 0 && (
              <>
                <div className="text-[11px] font-mono text-terminal-text">{currentDate}</div>
                <div className="text-[10px] font-mono text-terminal-ghost">
                  {playback.current_index + 1} / {playback.total_bars} bars
                  {' '}({pctDone.toFixed(0)}%)
                </div>
              </>
            )}
            {playback.symbol && (
              <div className="text-[10px] font-mono text-terminal-blue">{playback.symbol}</div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
