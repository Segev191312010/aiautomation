import React, { useEffect, useRef } from 'react'
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type HistogramData,
} from 'lightweight-charts'
import type { DailyPnL } from '@/types'

interface DailyPnLChartProps {
  data: DailyPnL[]
}

export function DailyPnLChart({ data }: DailyPnLChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef     = useRef<IChartApi | null>(null)
  const seriesRef    = useRef<ISeriesApi<'Histogram'> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      width:  containerRef.current.clientWidth,
      height: 200,
      layout: { background: { color: '#FFFFFF' }, textColor: '#6B7280', fontSize: 11 },
      grid: { vertLines: { color: '#F0EDE8' }, horzLines: { color: '#F0EDE8' } },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#E8E4DF' },
      timeScale: { borderColor: '#E8E4DF', timeVisible: true, secondsVisible: false },
    })
    chartRef.current  = chart
    seriesRef.current = chart.addHistogramSeries({ color: '#10B981', priceLineVisible: false })

    const ro = new ResizeObserver((entries) => {
      for (const e of entries) chart.applyOptions({ width: e.contentRect.width })
    })
    ro.observe(containerRef.current)
    return () => { ro.disconnect(); chart.remove(); chartRef.current = seriesRef.current = null }
  }, [])

  useEffect(() => {
    if (!seriesRef.current || !chartRef.current) return
    const bars: HistogramData[] = data.map((d) => ({
      time:  (new Date(d.date).getTime() / 1000) as number,
      value: d.pnl,
      color: d.pnl >= 0 ? '#10B981' : '#EF4444',
    }) as HistogramData)
    seriesRef.current.setData(bars)
    chartRef.current.timeScale().fitContent()
  }, [data])

  const wins   = data.filter((d) => d.pnl >= 0).length
  const losses = data.filter((d) => d.pnl < 0).length
  const winPct = data.length > 0 ? Math.round((wins / data.length) * 100) : 0

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-4 text-[11px] font-mono">
        <span className="text-emerald-600 font-semibold">{wins} up days</span>
        <span className="text-red-400 font-semibold">{losses} down days</span>
        <span className="text-zinc-400">{winPct}% win rate</span>
      </div>
      <div ref={containerRef} className="rounded-xl overflow-hidden" />
    </div>
  )
}
