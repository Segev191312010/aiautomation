/**
 * ChartToolbar — compact toolbar above the chart.
 *
 * Features:
 *  • Timeframe buttons grouped in a segmented pill container
 *  • Chart type dropdown with SVG icons
 *  • Indicator dropdown with colored dots and on/off styling
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
  { label: '1D',  period: '1y',   interval: '1d'  },
  { label: '1W',  period: '2y',   interval: '1wk' },
  { label: '1M',  period: '5y',   interval: '1mo' },
] as const

// ── Chart type options ──────────────────────────────────────────────────────

const CHART_TYPES: { value: ChartType; label: string; short: string; icon: React.ReactNode }[] = [
  {
    value: 'candlestick',
    label: 'Candlestick',
    short: 'Candle',
    icon: (
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
        <rect x="4" y="4" width="3" height="6" rx="0.5" fill="currentColor" opacity="0.9" />
        <line x1="5.5" y1="1.5" x2="5.5" y2="4" stroke="currentColor" strokeWidth="1.2" />
        <line x1="5.5" y1="10" x2="5.5" y2="12.5" stroke="currentColor" strokeWidth="1.2" />
        <rect x="8" y="5.5" width="3" height="4" rx="0.5" fill="none" stroke="currentColor" strokeWidth="1.1" />
        <line x1="9.5" y1="2.5" x2="9.5" y2="5.5" stroke="currentColor" strokeWidth="1.2" />
        <line x1="9.5" y1="9.5" x2="9.5" y2="11.5" stroke="currentColor" strokeWidth="1.2" />
      </svg>
    ),
  },
  {
    value: 'heikin-ashi',
    label: 'Heikin-Ashi',
    short: 'HA',
    icon: (
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
        <rect x="3.5" y="4.5" width="3" height="5" rx="0.5" fill="currentColor" opacity="0.7" />
        <line x1="5" y1="2" x2="5" y2="4.5" stroke="currentColor" strokeWidth="1.2" />
        <line x1="5" y1="9.5" x2="5" y2="12" stroke="currentColor" strokeWidth="1.2" />
        <rect x="7.5" y="3.5" width="3" height="6" rx="0.5" fill="currentColor" opacity="0.9" />
        <line x1="9" y1="1.5" x2="9" y2="3.5" stroke="currentColor" strokeWidth="1.2" />
        <line x1="9" y1="9.5" x2="9" y2="11" stroke="currentColor" strokeWidth="1.2" />
      </svg>
    ),
  },
  {
    value: 'ohlc',
    label: 'OHLC Bars',
    short: 'OHLC',
    icon: (
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
        <line x1="4" y1="2.5" x2="4" y2="11.5" stroke="currentColor" strokeWidth="1.3" />
        <line x1="2" y1="5" x2="4" y2="5" stroke="currentColor" strokeWidth="1.3" />
        <line x1="4" y1="8.5" x2="6" y2="8.5" stroke="currentColor" strokeWidth="1.3" />
        <line x1="10" y1="3" x2="10" y2="12" stroke="currentColor" strokeWidth="1.3" />
        <line x1="8" y1="5.5" x2="10" y2="5.5" stroke="currentColor" strokeWidth="1.3" />
        <line x1="10" y1="9" x2="12" y2="9" stroke="currentColor" strokeWidth="1.3" />
      </svg>
    ),
  },
  {
    value: 'line',
    label: 'Line',
    short: 'Line',
    icon: (
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
        <polyline points="1.5,11 4.5,7 7.5,9 10.5,4 12.5,5.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    value: 'area',
    label: 'Area',
    short: 'Area',
    icon: (
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
        <path d="M1.5 11 L4.5 7 L7.5 9 L10.5 4 L12.5 5.5 L12.5 12.5 L1.5 12.5 Z" fill="currentColor" opacity="0.25" />
        <polyline points="1.5,11 4.5,7 7.5,9 10.5,4 12.5,5.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    value: 'baseline',
    label: 'Baseline',
    short: 'Base',
    icon: (
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
        <line x1="1" y1="7" x2="13" y2="7" stroke="currentColor" strokeWidth="0.8" strokeDasharray="2 1.5" opacity="0.5" />
        <path d="M1.5 7 L5 4 L8.5 7 L12.5 5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
  },
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
  onCreateAlert?:  () => void
}

// ── Tooltip wrapper ─────────────────────────────────────────────────────────

function Tooltip({ tip, children }: { tip: string; children: React.ReactNode }) {
  return (
    <div className="relative group/tip">
      {children}
      <div
        className="pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5
                   px-1.5 py-0.5 rounded-md bg-gray-50 border border-gray-200
                   text-[10px] font-sans text-gray-500 whitespace-nowrap
                   opacity-0 group-hover/tip:opacity-100 transition-opacity duration-150 z-50"
      >
        {tip}
      </div>
    </div>
  )
}

// ── Component ───────────────────────────────────────────────────────────────

export default function ChartToolbar({
  activeTfIdx,
  onTfChange,
  chartContainer,
  chartRef,
  isLoading,
  className,
  onCreateAlert,
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

  const currentType = CHART_TYPES.find((t) => t.value === chartType) ?? CHART_TYPES[0]

  return (
    <nav
      role="toolbar"
      aria-label="Chart controls"
      className={clsx(
        'flex items-center gap-2 flex-wrap rounded-lg border border-gray-200 bg-white px-3 py-2 shadow-card',
        className,
      )}
    >
      {/* ── Timeframe segmented pill ──────────────────────────────────── */}
      <div
        className={clsx(
          'flex items-center',
          'bg-gray-100/60 rounded-xl p-0.5 gap-px',
          isLoading && 'opacity-40 pointer-events-none',
        )}
      >
        {TOOLBAR_TIMEFRAMES.map((tf, i) => (
          <button
            key={tf.label}
            onClick={() => onTfChange(i)}
            aria-pressed={activeTfIdx === i}
            className={clsx(
              'text-[11px] font-sans px-2.5 py-1 rounded-lg transition-all duration-150',
              activeTfIdx === i
                ? 'bg-gray-900 text-white'
                : 'text-gray-500 hover:text-gray-900 hover:bg-white',
            )}
          >
            {tf.label}
          </button>
        ))}
      </div>

      {/* ── Divider ───────────────────────────────────────────────────── */}
      <div className="w-px h-5 bg-gray-200 self-center" />

      {/* ── Chart type dropdown ───────────────────────────────────────── */}
      <div ref={typeMenuRef} className="relative">
        <button
          onClick={() => setShowTypeMenu((v) => !v)}
          className={clsx(
            'flex items-center gap-1.5 text-[11px] font-sans px-2.5 py-1.5 rounded-lg',
            'border transition-all duration-150',
            showTypeMenu
              ? 'border-gray-900 text-gray-900 bg-gray-100'
              : 'border-gray-200 text-gray-500 hover:text-gray-800 hover:bg-gray-100',
          )}
        >
          <span className={clsx('transition-colors', showTypeMenu ? 'text-gray-900' : 'text-gray-400')}>
            {currentType.icon}
          </span>
          <span>{currentType.short}</span>
          <svg
            width="8" height="8" viewBox="0 0 8 8" fill="none" aria-hidden="true"
            className={clsx('transition-transform duration-150', showTypeMenu && 'rotate-180')}
          >
            <path d="M1.5 2.5L4 5.5L6.5 2.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>

        {showTypeMenu && (
          <div className="absolute top-full left-0 mt-1.5 z-50 card-elevated rounded-xl border border-gray-200 shadow-card min-w-[152px] overflow-hidden animate-fade-in">
            {CHART_TYPES.map((ct) => (
              <button
                key={ct.value}
                onClick={() => { setChartType(ct.value); setShowTypeMenu(false) }}
                className={clsx(
                  'w-full flex items-center gap-2.5 text-left text-[11px] font-sans px-3 py-2 transition-colors',
                  chartType === ct.value
                    ? 'text-gray-900 bg-gray-100'
                    : 'text-gray-500 hover:text-gray-800 hover:bg-gray-50',
                )}
              >
                <span className={clsx(chartType === ct.value ? 'text-gray-900' : 'text-gray-400')}>
                  {ct.icon}
                </span>
                <span>{ct.label}</span>
                {chartType === ct.value && (
                  <svg width="10" height="10" viewBox="0 0 10 10" fill="none" className="ml-auto text-gray-900" aria-hidden="true">
                    <path d="M2 5L4.2 7.5L8 3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* ── Indicator dropdown ────────────────────────────────────────── */}
      <div ref={indMenuRef} className="relative">
        <button
          onClick={() => setShowIndMenu((v) => !v)}
          className={clsx(
            'flex items-center gap-1.5 text-[11px] font-sans px-2.5 py-1.5 rounded-lg',
            'border transition-all duration-150',
            selectedInds.length > 0
              ? 'border-gray-900 text-gray-900 bg-gray-100'
              : showIndMenu
              ? 'border-gray-200 text-gray-800 bg-gray-100'
              : 'border-gray-200 text-gray-500 hover:text-gray-800 hover:bg-gray-100',
          )}
        >
          {/* Mini colored dot strip for active indicators */}
          {selectedInds.length > 0 && (
            <span className="flex items-center gap-0.5">
              {selectedInds.slice(0, 3).map((id) => {
                const def = INDICATOR_DEFS.find((d) => d.id === id)
                return def ? (
                  <span key={id} className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: def.color }} />
                ) : null
              })}
              {selectedInds.length > 3 && (
                <span className="text-[9px] text-gray-900 leading-none">+{selectedInds.length - 3}</span>
              )}
            </span>
          )}
          <span>
            Indicators{selectedInds.length > 0 ? ` (${selectedInds.length})` : ''}
          </span>
          <svg
            width="8" height="8" viewBox="0 0 8 8" fill="none" aria-hidden="true"
            className={clsx('transition-transform duration-150', showIndMenu && 'rotate-180')}
          >
            <path d="M1.5 2.5L4 5.5L6.5 2.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>

        {showIndMenu && (
          <div className="absolute top-full left-0 mt-1.5 z-50 card-elevated rounded-xl border border-gray-200 shadow-card min-w-[190px] max-h-72 overflow-y-auto animate-fade-in">
            {/* Overlays section */}
            <div className="px-3 py-1.5 text-[10px] font-sans font-semibold text-gray-400 tracking-widest uppercase sticky top-0 bg-white border-b border-gray-200">
              Overlays
            </div>
            {OVERLAYS.map((def) => {
              const active = selectedInds.includes(def.id)
              return (
                <label
                  key={def.id}
                  className={clsx(
                    'flex items-center gap-2.5 px-3 py-2 cursor-pointer transition-colors',
                    active ? 'bg-gray-50/60' : 'hover:bg-gray-50',
                  )}
                >
                  {/* Custom toggle pill */}
                  <span
                    className={clsx(
                      'relative flex-shrink-0 w-6 h-3.5 rounded-full transition-colors duration-200',
                      active ? 'bg-gray-200' : 'bg-gray-100',
                    )}
                  >
                    <span
                      className={clsx(
                        'absolute top-0.5 w-2.5 h-2.5 rounded-full transition-all duration-200 shadow-sm',
                        active ? 'left-[calc(100%-0.625rem-0.125rem)] bg-gray-900' : 'left-0.5 bg-gray-400',
                      )}
                    />
                  </span>
                  <input
                    type="checkbox"
                    checked={active}
                    onChange={() => toggleIndicator(def.id as IndicatorId)}
                    className="sr-only"
                  />
                  <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: def.color }} />
                  <span className={clsx('text-[11px] font-sans', active ? 'text-gray-800' : 'text-gray-500')}>
                    {def.label}
                  </span>
                </label>
              )
            })}

            {/* Oscillators section */}
            <div className="px-3 py-1.5 text-[10px] font-sans font-semibold text-gray-400 tracking-widest uppercase sticky top-0 bg-white border-y border-gray-200">
              Oscillators
            </div>
            {OSCILLATORS.map((def) => {
              const active = selectedInds.includes(def.id)
              return (
                <label
                  key={def.id}
                  className={clsx(
                    'flex items-center gap-2.5 px-3 py-2 cursor-pointer transition-colors',
                    active ? 'bg-gray-50/60' : 'hover:bg-gray-50',
                  )}
                >
                  <span
                    className={clsx(
                      'relative flex-shrink-0 w-6 h-3.5 rounded-full transition-colors duration-200',
                      active ? 'bg-gray-200' : 'bg-gray-100',
                    )}
                  >
                    <span
                      className={clsx(
                        'absolute top-0.5 w-2.5 h-2.5 rounded-full transition-all duration-200 shadow-sm',
                        active ? 'left-[calc(100%-0.625rem-0.125rem)] bg-gray-900' : 'left-0.5 bg-gray-400',
                      )}
                    />
                  </span>
                  <input
                    type="checkbox"
                    checked={active}
                    onChange={() => toggleIndicator(def.id as IndicatorId)}
                    className="sr-only"
                  />
                  <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: def.color }} />
                  <span className={clsx('text-[11px] font-sans', active ? 'text-gray-800' : 'text-gray-500')}>
                    {def.label}
                  </span>
                </label>
              )
            })}
          </div>
        )}
      </div>

      {/* ── Right side: alert + screenshot + fullscreen ───────────────── */}
      <div className="ml-auto flex items-center gap-1">
        {onCreateAlert && (
          <Tooltip tip="Create price alert">
            <button
              onClick={onCreateAlert}
              className="flex items-center gap-1.5 text-[11px] font-sans px-2.5 py-1.5 rounded-lg
                         border border-gray-200 text-gray-400
                         hover:text-amber-600 hover:border-amber-300 hover:bg-amber-50
                         transition-all duration-150"
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
                <path d="M6 1C4.34 1 3 2.34 3 4v2.5L2 8h8L9 6.5V4c0-1.66-1.34-3-3-3Z" stroke="currentColor" strokeWidth="1.1" strokeLinejoin="round" />
                <path d="M4.5 8.5C4.5 9.33 5.17 10 6 10s1.5-.67 1.5-1.5" stroke="currentColor" strokeWidth="1.1" />
              </svg>
              <span>Alert</span>
            </button>
          </Tooltip>
        )}

        <Tooltip tip="Screenshot chart">
          <button
            onClick={handleScreenshot}
            className="flex items-center gap-1.5 text-[11px] font-sans px-2.5 py-1.5 rounded-lg
                       border border-gray-200 text-gray-400
                       hover:text-gray-500 hover:bg-gray-100
                       transition-all duration-150"
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
              <rect x="1" y="2.5" width="10" height="7.5" rx="1.2" stroke="currentColor" strokeWidth="1.1" />
              <circle cx="6" cy="6.2" r="1.8" stroke="currentColor" strokeWidth="1.1" />
              <path d="M4 2.5L4.8 1h2.4L8 2.5" stroke="currentColor" strokeWidth="1.1" strokeLinejoin="round" />
            </svg>
            <span>Snap</span>
          </button>
        </Tooltip>

        <Tooltip tip={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}>
          <button
            onClick={handleFullscreen}
            className={clsx(
              'flex items-center gap-1.5 text-[11px] font-sans px-2.5 py-1.5 rounded-lg',
              'border transition-all duration-150',
              isFullscreen
                ? 'border-gray-900 text-gray-900 bg-gray-100'
                : 'border-gray-200 text-gray-400 hover:text-gray-500 hover:bg-gray-100',
            )}
          >
            {isFullscreen ? (
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
                <path d="M4.5 1.5V4.5H1.5M7.5 1.5V4.5H10.5M4.5 10.5V7.5H1.5M7.5 10.5V7.5H10.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            ) : (
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
                <path d="M1.5 4.5V1.5H4.5M7.5 1.5H10.5V4.5M10.5 7.5V10.5H7.5M4.5 10.5H1.5V7.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            )}
            <span>{isFullscreen ? 'Exit' : 'Max'}</span>
          </button>
        </Tooltip>
      </div>
    </nav>
  )
}
