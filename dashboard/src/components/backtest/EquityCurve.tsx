import { useEffect, useRef } from 'react'
import { createChart, type IChartApi, type ISeriesApi, type LineData } from 'lightweight-charts'
import type { BacktestResult } from '@/types'

interface Props {
  result: BacktestResult
}

export function EquityCurve({ result }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 320,
      layout: {
        background: { color: '#0a0a0f' },
        textColor: '#9ca3af',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#1f2937' },
        horzLines: { color: '#1f2937' },
      },
      crosshair: { mode: 0 },
      rightPriceScale: {
        borderColor: '#374151',
      },
      timeScale: {
        borderColor: '#374151',
        timeVisible: false,
      },
    })
    chartRef.current = chart

    // Strategy equity line (blue)
    const strategySeries = chart.addLineSeries({
      color: '#3b82f6',
      lineWidth: 2,
      title: 'Strategy',
    })

    const strategyData: LineData[] = result.equity_curve.map((e) => ({
      time: e.time as number,
      value: e.equity,
    }) as LineData)
    strategySeries.setData(strategyData)

    // Buy-and-hold line (gray)
    const bhSeries = chart.addLineSeries({
      color: '#6b7280',
      lineWidth: 1,
      lineStyle: 2,
      title: 'Buy & Hold',
    })

    const bhData: LineData[] = result.buy_hold_curve.map((e) => ({
      time: e.time as number,
      value: e.equity,
    }) as LineData)
    bhSeries.setData(bhData)

    // Trade markers on strategy line
    if (result.trades.length > 0) {
      const markers = result.trades.flatMap((t) => {
        const entries: any[] = []
        const entryTime = Math.floor(new Date(t.entry_date).getTime() / 1000)
        const exitTime = Math.floor(new Date(t.exit_date).getTime() / 1000)

        entries.push({
          time: entryTime,
          position: 'belowBar',
          color: '#22c55e',
          shape: 'arrowUp',
          text: 'BUY',
        })
        entries.push({
          time: exitTime,
          position: 'aboveBar',
          color: '#ef4444',
          shape: 'arrowDown',
          text: t.exit_reason === 'stop_loss' ? 'SL' : t.exit_reason === 'take_profit' ? 'TP' : 'SELL',
        })
        return entries
      })

      // Sort markers by time (required by lightweight-charts)
      markers.sort((a: any, b: any) => a.time - b.time)
      strategySeries.setMarkers(markers)
    }

    chart.timeScale().fitContent()

    // Resize observer
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        chart.applyOptions({ width: entry.contentRect.width })
      }
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [result])

  return (
    <div>
      <h3 className="text-sm font-semibold text-gray-200 mb-2">Equity Curve</h3>
      <div ref={containerRef} className="rounded border border-gray-700 overflow-hidden" />
    </div>
  )
}
