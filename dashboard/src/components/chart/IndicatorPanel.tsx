/**
 * IndicatorPanel — oscillator sub-chart (RSI and/or MACD).
 *
 * Rendered below the main TradingChart. Uses the shared useChart hook
 * for chart creation and cleanup.
 */
import { useEffect, useRef } from 'react'
import {
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type LineSeriesOptions,
  type HistogramSeriesOptions,
  type LogicalRange,
  type Time,
} from 'lightweight-charts'
import clsx from 'clsx'
import { useChart, PANEL_THEME } from '@/hooks/useChart'
import { useMarketStore } from '@/store'
import { calcRSI, calcMACD, type LinePoint } from '@/utils/indicators'

function toTV(pts: LinePoint[]): LineData<Time>[] {
  return pts.map((p) => ({ time: p.time as Time, value: p.value }))
}

// ── RSI Sub-chart ─────────────────────────────────────────────────────────────

interface RSIPanelProps {
  symbol:        string
  mainChart?:    IChartApi | null
  className?:    string
  onChartReady?: (chart: IChartApi, series: ISeriesApi<'Line'>) => void
}

export function RSIPanel({ symbol, mainChart, className, onChartReady }: RSIPanelProps) {
  const { containerRef, chartRef } = useChart({ options: PANEL_THEME })
  const rsiRef  = useRef<ISeriesApi<'Line'> | null>(null)
  const ob70Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const os30Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const fittedRef = useRef(false)
  const onChartReadyRef = useRef(onChartReady)
  onChartReadyRef.current = onChartReady

  const bars = useMarketStore((s) => s.bars[symbol] ?? [])

  // Create series once chart is ready
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    rsiRef.current = chart.addLineSeries({
      color:            '#f472b6',
      lineWidth:        2,
      priceLineVisible: false,
      lastValueVisible: true,
    } as Partial<LineSeriesOptions>)

    const refOpts = {
      lineWidth:        1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    } as Partial<LineSeriesOptions>
    ob70Ref.current = chart.addLineSeries({ ...refOpts, color: '#ff3d5a44' })
    os30Ref.current = chart.addLineSeries({ ...refOpts, color: '#00e07a44' })

    onChartReadyRef.current?.(chart, rsiRef.current)

    return () => {
      rsiRef.current = ob70Ref.current = os30Ref.current = null
    }
  }, [chartRef])

  useEffect(() => {
    fittedRef.current = false
  }, [symbol])

  // Set data when bars change
  useEffect(() => {
    if (!rsiRef.current || !bars.length) return
    const rsiData = calcRSI(bars, 14)
    try {
      rsiRef.current.setData(toTV(rsiData) as Parameters<typeof rsiRef.current.setData>[0])
      const ob70 = rsiData.map((p) => ({ time: p.time as unknown, value: 70 }))
      const os30 = rsiData.map((p) => ({ time: p.time as unknown, value: 30 }))
      ob70Ref.current?.setData(ob70 as Parameters<typeof ob70Ref.current.setData>[0])
      os30Ref.current?.setData(os30 as Parameters<typeof os30Ref.current.setData>[0])
      if (!fittedRef.current) {
        chartRef.current?.timeScale().fitContent()
        fittedRef.current = true
      }
    } catch { /* ignore */ }
  }, [bars, chartRef])

  // Sync time axis with main chart (bidirectional)
  useEffect(() => {
    if (!mainChart || !chartRef.current) return
    const panelChart = chartRef.current
    const syncingRef = { current: false }

    const onMainRangeChange = (range: LogicalRange | null) => {
      if (syncingRef.current || !range) return
      syncingRef.current = true
      setTimeout(() => {
        try { panelChart.timeScale().setVisibleLogicalRange(range) } catch { /* */ }
        syncingRef.current = false
      }, 0)
    }

    const onPanelRangeChange = (range: LogicalRange | null) => {
      if (syncingRef.current || !range) return
      syncingRef.current = true
      setTimeout(() => {
        try { mainChart.timeScale().setVisibleLogicalRange(range) } catch { /* */ }
        syncingRef.current = false
      }, 0)
    }

    mainChart.timeScale().subscribeVisibleLogicalRangeChange(onMainRangeChange)
    panelChart.timeScale().subscribeVisibleLogicalRangeChange(onPanelRangeChange)

    return () => {
      try {
        mainChart.timeScale().unsubscribeVisibleLogicalRangeChange(onMainRangeChange)
        panelChart.timeScale().unsubscribeVisibleLogicalRangeChange(onPanelRangeChange)
      } catch { /* chart may be removed */ }
    }
  }, [mainChart, chartRef])

  return (
    <div className={clsx('w-full', className)}>
      <div className="flex items-center gap-2 px-3 py-1 border-b border-zinc-800">
        <span className="text-[10px] font-mono text-zinc-500">RSI (14)</span>
        <span className="text-[10px] font-mono text-zinc-500 ml-auto">
          <span className="text-red-400">70</span>
          <span className="text-zinc-500 mx-1">/</span>
          <span className="text-emerald-400">30</span>
        </span>
      </div>
      <div ref={containerRef} className="w-full h-full" />
    </div>
  )
}

// ── MACD Sub-chart ────────────────────────────────────────────────────────────

interface MACDPanelProps {
  symbol:        string
  mainChart?:    IChartApi | null
  className?:    string
  onChartReady?: (chart: IChartApi, series: ISeriesApi<'Line'>) => void
}

export function MACDPanel({ symbol, mainChart, className, onChartReady }: MACDPanelProps) {
  const { containerRef, chartRef } = useChart({ options: PANEL_THEME })
  const macdRef = useRef<ISeriesApi<'Line'> | null>(null)
  const sigRef  = useRef<ISeriesApi<'Line'> | null>(null)
  const histRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const fittedRef = useRef(false)
  const onChartReadyRef = useRef(onChartReady)
  onChartReadyRef.current = onChartReady

  const bars = useMarketStore((s) => s.bars[symbol] ?? [])

  // Create series once chart is ready
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    macdRef.current = chart.addLineSeries({
      color: '#38bdf8', lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
    } as Partial<LineSeriesOptions>)

    sigRef.current = chart.addLineSeries({
      color: '#fb923c', lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    } as Partial<LineSeriesOptions>)

    histRef.current = chart.addHistogramSeries({
      priceFormat:  { type: 'price' },
      priceScaleId: 'right',
    } as Partial<HistogramSeriesOptions>)

    onChartReadyRef.current?.(chart, macdRef.current)

    return () => {
      macdRef.current = sigRef.current = histRef.current = null
    }
  }, [chartRef])

  useEffect(() => {
    fittedRef.current = false
  }, [symbol])

  // Set data when bars change
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
          color: p.value >= 0 ? 'rgba(22, 163, 74, 0.4)' : 'rgba(220, 38, 38, 0.4)',
        })) as Parameters<typeof histRef.current.setData>[0],
      )
      if (!fittedRef.current) {
        chartRef.current?.timeScale().fitContent()
        fittedRef.current = true
      }
    } catch { /* ignore */ }
  }, [bars, chartRef])

  // Sync time axis with main chart (bidirectional)
  useEffect(() => {
    if (!mainChart || !chartRef.current) return
    const panelChart = chartRef.current
    const syncingRef = { current: false }

    const onMainRangeChange = (range: LogicalRange | null) => {
      if (syncingRef.current || !range) return
      syncingRef.current = true
      setTimeout(() => {
        try { panelChart.timeScale().setVisibleLogicalRange(range) } catch { /* */ }
        syncingRef.current = false
      }, 0)
    }

    const onPanelRangeChange = (range: LogicalRange | null) => {
      if (syncingRef.current || !range) return
      syncingRef.current = true
      setTimeout(() => {
        try { mainChart.timeScale().setVisibleLogicalRange(range) } catch { /* */ }
        syncingRef.current = false
      }, 0)
    }

    mainChart.timeScale().subscribeVisibleLogicalRangeChange(onMainRangeChange)
    panelChart.timeScale().subscribeVisibleLogicalRangeChange(onPanelRangeChange)

    return () => {
      try {
        mainChart.timeScale().unsubscribeVisibleLogicalRangeChange(onMainRangeChange)
        panelChart.timeScale().unsubscribeVisibleLogicalRangeChange(onPanelRangeChange)
      } catch { /* chart may be removed */ }
    }
  }, [mainChart, chartRef])

  return (
    <div className={clsx('w-full', className)}>
      <div className="flex items-center gap-3 px-3 py-1 border-b border-zinc-800">
        <span className="text-[10px] font-mono text-zinc-500">MACD (12,26,9)</span>
        <span className="inline-flex items-center gap-1.5 ml-auto">
          <span className="w-3 h-px bg-[#38bdf8] inline-block" />
          <span className="text-[9px] font-mono text-zinc-500">MACD</span>
          <span className="w-3 h-px bg-[#fb923c] inline-block ml-1" />
          <span className="text-[9px] font-mono text-zinc-500">Signal</span>
        </span>
      </div>
      <div ref={containerRef} className="w-full h-full" />
    </div>
  )
}

// ── Combined panel (renders whichever oscillators are selected) ───────────────

interface IndicatorPanelProps {
  symbol:          string
  mainChart?:      IChartApi | null
  className?:      string
  style?:          React.CSSProperties
  onRSIReady?:     (chart: IChartApi, series: ISeriesApi<'Line'>) => void
  onMACDReady?:    (chart: IChartApi, series: ISeriesApi<'Line'>) => void
}

export default function IndicatorPanel({ symbol, mainChart, className, style, onRSIReady, onMACDReady }: IndicatorPanelProps) {
  const selectedIndicators = useMarketStore((s) => s.selectedIndicators)
  const showRSI  = selectedIndicators.includes('rsi')
  const showMACD = selectedIndicators.includes('macd')

  if (!showRSI && !showMACD) return null

  const both = showRSI && showMACD

  return (
    <div className={clsx('flex gap-2', className)} style={style}>
      {showRSI && (
        <div
          className={clsx(
            'bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden flex flex-col',
            both ? 'flex-1' : 'w-full',
          )}
        >
          <RSIPanel symbol={symbol} mainChart={mainChart} onChartReady={onRSIReady} className="flex-1 min-h-0" />
        </div>
      )}
      {showMACD && (
        <div
          className={clsx(
            'bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden flex flex-col',
            both ? 'flex-1' : 'w-full',
          )}
        >
          <MACDPanel symbol={symbol} mainChart={mainChart} onChartReady={onMACDReady} className="flex-1 min-h-0" />
        </div>
      )}
    </div>
  )
}
