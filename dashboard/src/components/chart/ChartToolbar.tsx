/**
 * ChartToolbar — compact toolbar above the chart.
 *
 * Features:
 *  • Timeframe buttons (1m, 5m, 15m, 30m, 1H, 4H, 1D, 1W, 1M)
 *  • Chart type dropdown (Candlestick / OHLC / Line / Area / Baseline / Heikin-Ashi)
 *  • Indicator dropdown with checkboxes
 *  • Fullscreen toggle
 *  • Screenshot button
 */
import { useState, useRef, useEffect } from 'react'
import clsx from 'clsx'
import type { IChartApi } from 'lightweight-charts'
import { useMarketStore } from '@/store'
import { INDICATOR_DEFS, type IndicatorId } from '@/utils/indicators'
import type { ChartType } from '@/types'

// ── Timeframe definitions ───────────────────────────────────────────────────

export const TOOLBAR_TIMEFRAMES = [
  { label: '1m',  period: '1d',   interval: '1m'  },
  { label: '5m',  period: '5d',   interval: '5m'  },
  { label: '15m', period: '5d',   interval: '15m' },
  { label: '30m', period: '5d',   interval: '30m' },
  { label: '1H',  period: '1mo',  interval: '1h'  },
  { label: '4H',  period: '3mo',  interval: '1h'  },  // 4H ≈ 1h bars, 3mo period
  { label: '1D',  period: '1y',   interval: '1d'  },
  { label: '1W',  period: '2y',   interval: '1wk' },
  { label: '1M',  period: '5y',   interval: '1mo' },
] as const

// ── Chart type options ──────────────────────────────────────────────────────

const CHART_TYPES: { value: ChartType; label: string; short: string }[] = [
  { value: 'candlestick', label: 'Candlestick', short: 'Candle' },
  { value: 'heikin-ashi', label: 'Heikin-Ashi', short: 'HA'     },
  { value: 'ohlc',        label: 'OHLC Bars',   short: 'OHLC'   },
  { value: 'line',        label: 'Line',         short: 'Line'   },
  { value: 'area',        label: 'Area',         short: 'Area'   },
  { value: 'baseline',    label: 'Baseline',     short: 'Base'   },
]

const OVERLAYS    = INDICATOR_DEFS.filter((d) => d.type === 'overlay')
const OSCILLATORS = INDICATOR_DEFS.filter((d) => d.type === 'oscillator')

// ── Props ───────────────────────────────────────────────────────────────────

interface Props {
  activeTfIdx:     number
  onTfChange:      (idx: number) => void
  chartContainer?: HTMLElement | null
  chartRef?:       IChartApi | null
  isLoading?:      boolean
  className?:      string
}

// ── Component ───────────────────────────────────────────────────────────────

export default function ChartToolbar({
  activeTfIdx,
  onTfChange,
  chartContainer,
  chartRef,
  isLoading,
  className,
}: Props) {
  const chartType       = useMarketStore((s) => s.chartType)
  const setChartType    = useMarketStore((s) => s.setChartType)
  const selectedInds    = useMarketStore((s) => s.selectedIndicators)
  const toggleIndicator = useMarketStore((s) => s.toggleIndicator)

  const [showTypeMenu, setShowTypeMenu] = useState(false)
  const [showIndMenu, setShowIndMenu]   = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const typeMenuRef = useRef<HTMLDivElement>(null)
  const indMenuRef  = useRef<HTMLDivElement>(null)

  // Close dropdowns on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (typeMenuRef.current && !typeMenuRef.current.contains(e.target as Node)) setShowTypeMenu(false)
      if (indMenuRef.current && !indMenuRef.current.contains(e.target as Node)) setShowIndMenu(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // Track fullscreen state changes
  useEffect(() => {
    const handler = () => setIsFullscreen(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', handler)
    return () => document.removeEventListener('fullscreenchange', handler)
  }, [])

  // Escape key exits fullscreen
  useEffect(() => {
    if (!isFullscreen) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isFullscreen && chartContainer) {
        // Exit custom fullscreen if using CSS approach
        chartContainer.classList.remove('chart-fullscreen')
        setIsFullscreen(false)
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [isFullscreen, chartContainer])

  const handleFullscreen = () => {
    if (!chartContainer) return
    if (document.fullscreenElement) {
      document.exitFullscreen()
    } else {
      const el = chartContainer as HTMLElement & { webkitRequestFullscreen?: () => void }
      if (el.requestFullscreen) {
        el.requestFullscreen()
      } else if (el.webkitRequestFullscreen) {
        el.webkitRequestFullscreen()
      } else {
        // CSS fallback
        el.classList.toggle('chart-fullscreen')
        setIsFullscreen(!isFullscreen)
      }
    }
  }

  const handleScreenshot = () => {
    if (!chartRef) return
    try {
      const canvas = (chartRef as IChartApi & { takeScreenshot: () => HTMLCanvasElement }).takeScreenshot()
      canvas.toBlob((blob) => {
        if (!blob) return
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        const tf = TOOLBAR_TIMEFRAMES[activeTfIdx]?.label ?? 'chart'
        a.href = url
        a.download = `chart_${tf}_${Date.now()}.png`
        a.click()
        URL.revokeObjectURL(url)
      }, 'image/png')
    } catch {
      // takeScreenshot may not be available
    }
  }

  const currentTypeLabel = CHART_TYPES.find((t) => t.value === chartType)?.short ?? 'Candle'

  return (
    <nav
      role="toolbar"
      aria-label="Chart controls"
      className={clsx('flex items-center gap-2 flex-wrap', className)}
    >
      {/* Timeframe buttons */}
      <div className={clsx('flex gap-0.5', isLoading && 'opacity-50 pointer-events-none')}>
        {TOOLBAR_TIMEFRAMES.map((tf, i) => (
          <button
            key={tf.label}
            onClick={() => onTfChange(i)}
            aria-pressed={activeTfIdx === i}
            className={clsx(
              'text-[11px] font-mono px-2 py-1 rounded border transition-colors',
              activeTfIdx === i
                ? 'border-terminal-blue/50 text-terminal-blue bg-terminal-blue/10'
                : 'border-terminal-border text-terminal-ghost hover:text-terminal-dim hover:border-terminal-muted',
            )}
          >
            {tf.label}
          </button>
        ))}
      </div>

      {/* Divider */}
      <div className="w-px h-5 bg-terminal-border" />

      {/* Chart type dropdown */}
      <div ref={typeMenuRef} className="relative">
        <button
          onClick={() => setShowTypeMenu((v) => !v)}
          className="text-[11px] font-mono px-2 py-1 rounded border border-terminal-border text-terminal-dim hover:text-terminal-text hover:border-terminal-muted transition-colors"
        >
          {currentTypeLabel} ▾
        </button>
        {showTypeMenu && (
          <div className="absolute top-full left-0 mt-1 z-50 bg-terminal-elevated border border-terminal-border rounded shadow-lg min-w-[140px]">
            {CHART_TYPES.map((ct) => (
              <button
                key={ct.value}
                onClick={() => { setChartType(ct.value); setShowTypeMenu(false) }}
                className={clsx(
                  'w-full text-left text-[11px] font-mono px-3 py-1.5 transition-colors',
                  chartType === ct.value
                    ? 'text-terminal-blue bg-terminal-blue/10'
                    : 'text-terminal-dim hover:text-terminal-text hover:bg-terminal-muted/20',
                )}
              >
                {ct.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Indicator dropdown */}
      <div ref={indMenuRef} className="relative">
        <button
          onClick={() => setShowIndMenu((v) => !v)}
          className={clsx(
            'text-[11px] font-mono px-2 py-1 rounded border transition-colors',
            selectedInds.length > 0
              ? 'border-terminal-blue/40 text-terminal-blue bg-terminal-blue/5'
              : 'border-terminal-border text-terminal-ghost hover:text-terminal-dim',
          )}
        >
          Indicators{selectedInds.length > 0 ? ` (${selectedInds.length})` : ''} ▾
        </button>
        {showIndMenu && (
          <div className="absolute top-full left-0 mt-1 z-50 bg-terminal-elevated border border-terminal-border rounded shadow-lg min-w-[180px] max-h-64 overflow-y-auto">
            <div className="px-3 py-1.5 text-[9px] font-mono text-terminal-ghost uppercase tracking-wider border-b border-terminal-border">
              Overlays
            </div>
            {OVERLAYS.map((def) => (
              <label
                key={def.id}
                className="flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-terminal-muted/20 transition-colors"
              >
                <input
                  type="checkbox"
                  checked={selectedInds.includes(def.id)}
                  onChange={() => toggleIndicator(def.id as IndicatorId)}
                  className="accent-terminal-blue w-3 h-3"
                />
                <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: def.color }} />
                <span className="text-[11px] font-mono text-terminal-dim">{def.label}</span>
              </label>
            ))}
            <div className="px-3 py-1.5 text-[9px] font-mono text-terminal-ghost uppercase tracking-wider border-b border-t border-terminal-border">
              Oscillators
            </div>
            {OSCILLATORS.map((def) => (
              <label
                key={def.id}
                className="flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-terminal-muted/20 transition-colors"
              >
                <input
                  type="checkbox"
                  checked={selectedInds.includes(def.id)}
                  onChange={() => toggleIndicator(def.id as IndicatorId)}
                  className="accent-terminal-blue w-3 h-3"
                />
                <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: def.color }} />
                <span className="text-[11px] font-mono text-terminal-dim">{def.label}</span>
              </label>
            ))}
          </div>
        )}
      </div>

      {/* Right side: screenshot + fullscreen */}
      <div className="ml-auto flex items-center gap-1">
        <button
          onClick={handleScreenshot}
          title="Download chart screenshot"
          className="text-[11px] font-mono px-2 py-1 rounded border border-terminal-border text-terminal-ghost hover:text-terminal-dim transition-colors"
        >
          ⊞ Snap
        </button>
        <button
          onClick={handleFullscreen}
          title="Toggle fullscreen"
          className={clsx(
            'text-[11px] font-mono px-2 py-1 rounded border transition-colors',
            isFullscreen
              ? 'border-terminal-blue/50 text-terminal-blue bg-terminal-blue/10'
              : 'border-terminal-border text-terminal-ghost hover:text-terminal-dim',
          )}
        >
          {isFullscreen ? '⊟ Exit' : '⊞ Max'}
        </button>
      </div>
    </nav>
  )
}
