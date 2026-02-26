/**
 * VolumePanel — separate volume histogram pane below main chart.
 *
 * Time-axis synced with the main chart via subscribeVisibleLogicalRangeChange.
 * Height: ~60-80px. Color-coded bars (green = up, red = down).
 */
import { useEffect, useRef } from 'react'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'
import clsx from 'clsx'
import { useChart, PANEL_THEME } from '@/hooks/useChart'
import { useMarketStore } from '@/store'
import type { OHLCVBar } from '@/types'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyData = any

interface Props {
  symbol:       string
  mainChart?:   IChartApi | null
  className?:   string
  onChartReady?: (chart: IChartApi, series: ISeriesApi<'Histogram'>) => void
}

export default function VolumePanel({ symbol, mainChart, className, onChartReady }: Props) {
  const { containerRef, chartRef } = useChart({
    options: {
      ...PANEL_THEME,
      rightPriceScale: {
        borderColor: '#1c2e4a',
        scaleMargins: { top: 0.1, bottom: 0 },
      },
    },
  })
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const onChartReadyRef = useRef(onChartReady)
  onChartReadyRef.current = onChartReady
  const bars = useMarketStore((s) => s.bars[symbol] ?? [])

  // Create volume series once
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    const vol = chart.addHistogramSeries({
      color:       '#1c2e4a',
      priceFormat: { type: 'volume' },
    } as AnyData)
    volumeRef.current = vol
    onChartReadyRef.current?.(chart, vol)

    return () => {
      volumeRef.current = null
    }
  }, [chartRef])

  // Set data when bars change
  useEffect(() => {
    if (!volumeRef.current || !bars.length) return
    const data = bars.slice(-5000).map((b: OHLCVBar) => ({
      time:  b.time as unknown,
      value: b.volume,
      color: b.close >= b.open ? '#00874a55' : '#99243855',
    }))
    try {
      volumeRef.current.setData(data as AnyData)
      chartRef.current?.timeScale().fitContent()
    } catch { /* ignore stale */ }
  }, [bars, chartRef])

  // Sync time axis with main chart (bidirectional)
  useEffect(() => {
    if (!mainChart || !chartRef.current) return
    const volChart = chartRef.current
    const syncingRef = { current: false }

    const onMainRangeChange = (range: AnyData) => {
      if (syncingRef.current || !range) return
      syncingRef.current = true
      setTimeout(() => {
        try { volChart.timeScale().setVisibleLogicalRange(range) } catch { /* */ }
        syncingRef.current = false
      }, 0)
    }

    const onVolRangeChange = (range: AnyData) => {
      if (syncingRef.current || !range) return
      syncingRef.current = true
      setTimeout(() => {
        try { mainChart.timeScale().setVisibleLogicalRange(range) } catch { /* */ }
        syncingRef.current = false
      }, 0)
    }

    mainChart.timeScale().subscribeVisibleLogicalRangeChange(onMainRangeChange)
    volChart.timeScale().subscribeVisibleLogicalRangeChange(onVolRangeChange)

    return () => {
      try {
        mainChart.timeScale().unsubscribeVisibleLogicalRangeChange(onMainRangeChange)
        volChart.timeScale().unsubscribeVisibleLogicalRangeChange(onVolRangeChange)
      } catch { /* chart may be removed */ }
    }
  }, [mainChart, chartRef])

  return (
    <div className={clsx('w-full', className)} style={{ touchAction: 'none' }}>
      <div className="flex items-center px-3 py-0.5">
        <span className="text-[10px] font-mono text-terminal-ghost">Volume</span>
      </div>
      <div ref={containerRef} className="w-full" style={{ height: 'calc(100% - 20px)' }} />
    </div>
  )
}
