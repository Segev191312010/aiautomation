/**
 * useChart — reusable hook for lightweight-charts creation, resize, and cleanup.
 *
 * Exports shared theme constants (CHART_THEME, PANEL_THEME) so all chart
 * components use the same terminal dark palette.
 *
 * Used by: TradingChart, VolumePanel, RSIPanel, MACDPanel
 */
import { useEffect, useRef } from 'react'
import {
  createChart,
  type IChartApi,
  type DeepPartial,
  type ChartOptions,
  ColorType,
  CrosshairMode,
} from 'lightweight-charts'

// ── Shared chart themes ─────────────────────────────────────────────────────

export const CHART_THEME: DeepPartial<ChartOptions> = {
  layout: {
    background:  { type: ColorType.Solid, color: '#FFFFFF' },
    textColor:   '#6B7280',
    fontFamily:  '"JetBrains Mono", ui-monospace, monospace',
    fontSize:    11,
  },
  grid: {
    vertLines: { color: '#F0EDE8' },
    horzLines: { color: '#F0EDE8' },
  },
  crosshair: {
    mode:     CrosshairMode.Normal,
    vertLine: { color: '#D1D5DB', labelBackgroundColor: '#F3F4F6' },
    horzLine: { color: '#D1D5DB', labelBackgroundColor: '#F3F4F6' },
  },
  rightPriceScale: { borderColor: '#E5E7EB' },
  timeScale:       { borderColor: '#E5E7EB', timeVisible: true, secondsVisible: false },
}

export const PANEL_THEME: DeepPartial<ChartOptions> = {
  layout: {
    background:  { type: ColorType.Solid, color: '#FFFFFF' },
    textColor:   '#6B7280',
    fontFamily:  '"JetBrains Mono", ui-monospace, monospace',
    fontSize:    10,
  },
  grid: {
    vertLines: { color: '#F0EDE8' },
    horzLines: { color: '#F5F3F0' },
  },
  crosshair: {
    mode:     CrosshairMode.Normal,
    vertLine: { color: '#D1D5DB', labelBackgroundColor: '#F3F4F6' },
    horzLine: { visible: false },
  },
  rightPriceScale: { borderColor: '#E5E7EB' },
  timeScale:       { borderColor: '#E5E7EB', timeVisible: true, secondsVisible: false, visible: false },
}

// ── Hook ────────────────────────────────────────────────────────────────────

interface UseChartOptions {
  /** Chart options merged on top of the base theme. */
  options?: DeepPartial<ChartOptions>
}

interface UseChartReturn {
  containerRef: React.RefObject<HTMLDivElement>
  chartRef:     React.MutableRefObject<IChartApi | null>
}

export function useChart(opts?: UseChartOptions): UseChartReturn {
  const containerRef = useRef<HTMLDivElement>(null) as React.RefObject<HTMLDivElement>
  const chartRef     = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    let chart: IChartApi
    try {
      chart = createChart(containerRef.current, {
        ...(opts?.options ?? CHART_THEME),
        width:  containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      })
    } catch (err) {
      console.error('useChart: chart init failed', err)
      return
    }
    chartRef.current = chart

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
      chartRef.current = null
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return { containerRef, chartRef }
}
