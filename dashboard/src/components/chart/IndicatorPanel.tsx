/**
 * IndicatorPanel — oscillator sub-chart (RSI and/or MACD).
 *
 * Rendered below the main TradingChart. Creates its own lightweight-charts
 * instance and recalculates when bars or selectedIndicators change.
 */
import React, { useEffect, useRef } from 'react'
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  ColorType,
  CrosshairMode,
  type LineSeriesOptions,
  type HistogramSeriesOptions,
} from 'lightweight-charts'
import clsx from 'clsx'
import { useMarketStore } from '@/store'
import { calcRSI, calcMACD, type LinePoint } from '@/utils/indicators'

// ── Sub-chart theme ───────────────────────────────────────────────────────────

const PANEL_OPTS = {
  layout: {
    background: { type: ColorType.Solid, color: '#080d18' },
    textColor:  '#5f7a9d',
    fontFamily: '"JetBrains Mono", ui-monospace, monospace',
    fontSize:   10,
  },
  grid: {
    vertLines: { color: '#111f35' },
    horzLines: { color: '#0d1a2e' },
  },
  crosshair: {
    mode:     CrosshairMode.Normal,
    vertLine: { color: '#2b4a7a', labelBackgroundColor: '#0e1726' },
    horzLine: { color: '#2b4a7a', labelBackgroundColor: '#0e1726' },
  },
  rightPriceScale: { borderColor: '#1c2e4a' },
  timeScale:       { borderColor: '#1c2e4a', timeVisible: true, secondsVisible: false, visible: false },
} as const

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyData = any

function toTV(pts: LinePoint[]): AnyData {
  return pts.map((p) => ({ time: p.time, value: p.value }))
}

// ── RSI Sub-chart ─────────────────────────────────────────────────────────────

interface RSIPanelProps { symbol: string; className?: string }

export function RSIPanel({ symbol, className }: RSIPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef     = useRef<IChartApi | null>(null)
  const rsiRef       = useRef<ISeriesApi<'Line'> | null>(null)
  const ob70Ref      = useRef<ISeriesApi<'Line'> | null>(null)
  const os30Ref      = useRef<ISeriesApi<'Line'> | null>(null)

  const bars = useMarketStore((s) => s.bars[symbol] ?? [])

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      ...PANEL_OPTS,
      width:  containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    })
    chartRef.current = chart

    // RSI line
    rsiRef.current = chart.addLineSeries({
      color:            '#f472b6',
      lineWidth:        2,
      priceLineVisible: false,
      lastValueVisible: true,
    } as Partial<LineSeriesOptions>)

    // Overbought / oversold reference lines
    const refOpts = {
      lineWidth:        1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    } as Partial<LineSeriesOptions>
    ob70Ref.current = chart.addLineSeries({ ...refOpts, color: '#ff3d5a44' })
    os30Ref.current = chart.addLineSeries({ ...refOpts, color: '#00e07a44' })

    const ro = new ResizeObserver(() => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.applyOptions({
          width:  containerRef.current.clientWidth,
          height: containerRef.current.clientHeight,
        })
      }
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = rsiRef.current = ob70Ref.current = os30Ref.current = null
    }
  }, [])

  useEffect(() => {
    if (!rsiRef.current || !bars.length) return
    const rsiData = calcRSI(bars, 14)
    try {
      rsiRef.current.setData(toTV(rsiData) as Parameters<typeof rsiRef.current.setData>[0])
      // Reference lines spanning same time range
      const ob70 = rsiData.map((p) => ({ time: p.time as unknown, value: 70 }))
      const os30 = rsiData.map((p) => ({ time: p.time as unknown, value: 30 }))
      ob70Ref.current?.setData(ob70 as Parameters<typeof ob70Ref.current.setData>[0])
      os30Ref.current?.setData(os30 as Parameters<typeof os30Ref.current.setData>[0])
      chartRef.current?.timeScale().fitContent()
    } catch { /* ignore */ }
  }, [bars])

  return (
    <div className={clsx('w-full', className)}>
      <div className="flex items-center gap-2 px-3 py-1 border-b border-terminal-border">
        <span className="text-[10px] font-mono text-terminal-ghost">RSI (14)</span>
        <span className="text-[10px] font-mono text-terminal-ghost ml-auto">
          <span className="text-terminal-red">70</span>
          <span className="text-terminal-ghost mx-1">/</span>
          <span className="text-terminal-green">30</span>
        </span>
      </div>
      <div ref={containerRef} className="w-full h-full" />
    </div>
  )
}

// ── MACD Sub-chart ────────────────────────────────────────────────────────────

interface MACDPanelProps { symbol: string; className?: string }

export function MACDPanel({ symbol, className }: MACDPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef     = useRef<IChartApi | null>(null)
  const macdRef      = useRef<ISeriesApi<'Line'> | null>(null)
  const sigRef       = useRef<ISeriesApi<'Line'> | null>(null)
  const histRef      = useRef<ISeriesApi<'Histogram'> | null>(null)

  const bars = useMarketStore((s) => s.bars[symbol] ?? [])

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      ...PANEL_OPTS,
      width:  containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    })
    chartRef.current = chart

    macdRef.current = chart.addLineSeries({
      color: '#38bdf8', lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
    } as Partial<LineSeriesOptions>)

    sigRef.current = chart.addLineSeries({
      color: '#fb923c', lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    } as Partial<LineSeriesOptions>)

    histRef.current = chart.addHistogramSeries({
      priceFormat:    { type: 'price' },
      priceScaleId:   'right',
    } as Partial<HistogramSeriesOptions>)

    const ro = new ResizeObserver(() => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.applyOptions({
          width:  containerRef.current.clientWidth,
          height: containerRef.current.clientHeight,
        })
      }
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = macdRef.current = sigRef.current = histRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!macdRef.current || !bars.length) return
    const { macd, signal, histogram } = calcMACD(bars)
    try {
      macdRef.current.setData(toTV(macd) as Parameters<typeof macdRef.current.setData>[0])
      sigRef.current?.setData(toTV(signal) as Parameters<typeof sigRef.current.setData>[0])
      histRef.current?.setData(
        histogram.map((p) => ({
          time:  p.time as unknown,
          value: p.value,
          color: p.value >= 0 ? '#00874a66' : '#99243866',
        })) as Parameters<typeof histRef.current.setData>[0],
      )
      chartRef.current?.timeScale().fitContent()
    } catch { /* ignore */ }
  }, [bars])

  return (
    <div className={clsx('w-full', className)}>
      <div className="flex items-center gap-3 px-3 py-1 border-b border-terminal-border">
        <span className="text-[10px] font-mono text-terminal-ghost">MACD (12,26,9)</span>
        <span className="inline-flex items-center gap-1.5 ml-auto">
          <span className="w-3 h-px bg-[#38bdf8] inline-block" />
          <span className="text-[9px] font-mono text-terminal-ghost">MACD</span>
          <span className="w-3 h-px bg-[#fb923c] inline-block ml-1" />
          <span className="text-[9px] font-mono text-terminal-ghost">Signal</span>
        </span>
      </div>
      <div ref={containerRef} className="w-full h-full" />
    </div>
  )
}

// ── Combined panel (renders whichever oscillators are selected) ───────────────

interface IndicatorPanelProps {
  symbol:    string
  className?: string
}

export default function IndicatorPanel({ symbol, className }: IndicatorPanelProps) {
  const selectedIndicators = useMarketStore((s) => s.selectedIndicators)
  const showRSI  = selectedIndicators.includes('rsi')
  const showMACD = selectedIndicators.includes('macd')

  if (!showRSI && !showMACD) return null

  const both = showRSI && showMACD

  return (
    <div className={clsx('flex gap-2', className)}>
      {showRSI && (
        <div
          className={clsx(
            'bg-terminal-surface border border-terminal-border rounded-lg overflow-hidden flex flex-col',
            both ? 'flex-1' : 'w-full',
          )}
        >
          <RSIPanel symbol={symbol} className="flex-1 min-h-0" />
        </div>
      )}
      {showMACD && (
        <div
          className={clsx(
            'bg-terminal-surface border border-terminal-border rounded-lg overflow-hidden flex flex-col',
            both ? 'flex-1' : 'w-full',
          )}
        >
          <MACDPanel symbol={symbol} className="flex-1 min-h-0" />
        </div>
      )}
    </div>
  )
}
