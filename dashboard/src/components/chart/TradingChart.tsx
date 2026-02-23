/**
 * TradingChart — wraps TradingView lightweight-charts.
 *
 * Features:
 *  • Candlestick + volume histogram
 *  • Live candle updates via /ws/market-data WebSocket
 *  • Overlay indicators: SMA 20/50, EMA 12/26, Bollinger Bands, VWAP
 *  • Optional comparison overlay (normalized %)
 *  • Replay bar injection (live bars from WebSocket during simulation)
 *  • Responsive resize via ResizeObserver
 */
import React, { useEffect, useRef } from 'react'
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type CandlestickSeriesOptions,
  type HistogramSeriesOptions,
  type LineSeriesOptions,
  ColorType,
  CrosshairMode,
  LineStyle,
} from 'lightweight-charts'
import clsx from 'clsx'
import { useMarketStore, useSimStore } from '@/store'
import { wsMdService } from '@/services/ws'
import {
  calcSMA, calcEMA, calcBB, calcVWAP,
  INDICATOR_DEFS,
  type IndicatorId,
  type LinePoint,
} from '@/utils/indicators'
import type { OHLCVBar } from '@/types'

// ── Chart theme ───────────────────────────────────────────────────────────────

const CHART_OPTS = {
  layout: {
    background:  { type: ColorType.Solid, color: '#080d18' },
    textColor:   '#5f7a9d',
    fontFamily:  '"JetBrains Mono", ui-monospace, monospace',
    fontSize:    11,
  },
  grid: {
    vertLines: { color: '#111f35' },
    horzLines: { color: '#111f35' },
  },
  crosshair: {
    mode:     CrosshairMode.Normal,
    vertLine: { color: '#2b4a7a', labelBackgroundColor: '#0e1726' },
    horzLine: { color: '#2b4a7a', labelBackgroundColor: '#0e1726' },
  },
  rightPriceScale: { borderColor: '#1c2e4a' },
  timeScale:       { borderColor: '#1c2e4a', timeVisible: true, secondsVisible: false },
} as const

// ── Normalization for comparison overlay ──────────────────────────────────────

function normalizeBars(bars: OHLCVBar[]): OHLCVBar[] {
  if (!bars.length) return []
  const base = bars[0].close
  return bars.map((b) => ({
    ...b,
    open:  +((b.open  / base - 1) * 100).toFixed(4),
    high:  +((b.high  / base - 1) * 100).toFixed(4),
    low:   +((b.low   / base - 1) * 100).toFixed(4),
    close: +((b.close / base - 1) * 100).toFixed(4),
  }))
}

// ── Helper: convert LinePoint to lightweight-charts time format ───────────────

function toTV(pts: LinePoint[]) {
  return pts.map((p) => ({ time: p.time as unknown, value: p.value }))
}

// ── Indicator series helpers ──────────────────────────────────────────────────

function makeLineSeries(
  chart: IChartApi,
  color: string,
  lineWidth: 1 | 2 = 1,
  style: LineStyle = LineStyle.Solid,
): ISeriesApi<'Line'> {
  return chart.addLineSeries({
    color,
    lineWidth,
    lineStyle:        style,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  } as Partial<LineSeriesOptions>)
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  symbol:      string
  className?:  string
  /** Seconds per bar — used to slot live WS quotes into the correct candle. Default 86400 (1 day). */
  barSeconds?: number
}

interface LiveBar {
  time:  number
  open:  number
  high:  number
  low:   number
  close: number
}

export default function TradingChart({ symbol, className, barSeconds = 86_400 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef     = useRef<IChartApi | null>(null)
  const candleRef    = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeRef    = useRef<ISeriesApi<'Histogram'> | null>(null)
  const compRef      = useRef<ISeriesApi<'Line'> | null>(null)
  const liveBarRef   = useRef<LiveBar | null>(null)

  // Map: indicatorId → array of series (BB has 3, others have 1)
  const indSeriesRef = useRef<Map<string, ISeriesApi<'Line'>[]>>(new Map())

  const bars              = useMarketStore((s) => s.bars[symbol] ?? [])
  const compSymbol        = useMarketStore((s) => s.compSymbol)
  const compBars          = useMarketStore((s) => s.compBars[compSymbol] ?? [])
  const compMode          = useMarketStore((s) => s.compMode)
  const selectedIndicators= useMarketStore((s) => s.selectedIndicators)
  const replayBars        = useSimStore((s) => s.replayBars)

  // ── Init chart ────────────────────────────────────────────────────────────

  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      ...CHART_OPTS,
      width:  containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    })
    chartRef.current = chart

    // Candlestick series
    const candle = chart.addCandlestickSeries({
      upColor:         '#00e07a',
      downColor:       '#ff3d5a',
      borderUpColor:   '#00e07a',
      borderDownColor: '#ff3d5a',
      wickUpColor:     '#00874a',
      wickDownColor:   '#992438',
    } as Partial<CandlestickSeriesOptions>)
    candleRef.current = candle

    // Volume histogram
    const volume = chart.addHistogramSeries({
      color:        '#1c2e4a',
      priceFormat:  { type: 'volume' },
      priceScaleId: 'volume',
    } as Partial<HistogramSeriesOptions>)
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })
    volumeRef.current = volume

    // ResizeObserver
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
      indSeriesRef.current.clear()
      chart.remove()
      chartRef.current    = null
      candleRef.current   = null
      volumeRef.current   = null
      compRef.current     = null
      liveBarRef.current  = null
    }
  }, [])

  // ── Load bars ─────────────────────────────────────────────────────────────

  useEffect(() => {
    if (!candleRef.current || !volumeRef.current || !bars.length) return

    const candleData = bars.map((b) => ({
      time: b.time as unknown, open: b.open, high: b.high, low: b.low, close: b.close,
    }))
    const volumeData = bars.map((b) => ({
      time:  b.time as unknown,
      value: b.volume,
      color: b.close >= b.open ? '#00874a33' : '#99243833',
    }))

    try {
      candleRef.current.setData(candleData as Parameters<typeof candleRef.current.setData>[0])
      volumeRef.current.setData(volumeData as Parameters<typeof volumeRef.current.setData>[0])
      chartRef.current?.timeScale().fitContent()
    } catch { /* ignore stale */ }

    // Seed live bar from last loaded bar
    const last = bars[bars.length - 1]
    liveBarRef.current = {
      time: last.time, open: last.open, high: last.high, low: last.low, close: last.close,
    }
  }, [bars])

  // ── Live candle via WebSocket ─────────────────────────────────────────────

  useEffect(() => {
    if (!bars.length) return

    const unsub = wsMdService.subscribe(symbol, (msg) => {
      if (!candleRef.current) return
      const price   = msg.price
      const nowSecs = msg.time ?? Math.floor(Date.now() / 1000)
      const barTime = Math.floor(nowSecs / barSeconds) * barSeconds

      const lb = liveBarRef.current
      if (lb && lb.time === barTime) {
        lb.high  = Math.max(lb.high, price)
        lb.low   = Math.min(lb.low, price)
        lb.close = price
      } else {
        liveBarRef.current = { time: barTime, open: price, high: price, low: price, close: price }
      }

      try {
        candleRef.current.update({
          time:  liveBarRef.current!.time  as unknown,
          open:  liveBarRef.current!.open,
          high:  liveBarRef.current!.high,
          low:   liveBarRef.current!.low,
          close: liveBarRef.current!.close,
        } as Parameters<typeof candleRef.current.update>[0])
      } catch { /* ignore */ }
    })

    return unsub
  }, [symbol, barSeconds, bars.length])

  // ── Replay bars (injected live) ───────────────────────────────────────────

  useEffect(() => {
    if (!candleRef.current || !replayBars.length) return
    const last = replayBars[replayBars.length - 1]
    try {
      candleRef.current.update({
        time: last.time as unknown, open: last.open, high: last.high, low: last.low, close: last.close,
      } as Parameters<typeof candleRef.current.update>[0])
    } catch { /* ignore */ }
  }, [replayBars])

  // ── Overlay indicators ────────────────────────────────────────────────────

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    // Remove series for deselected / all indicators (in comparison mode hide them)
    for (const [key, seriesList] of indSeriesRef.current.entries()) {
      if (compMode || !selectedIndicators.includes(key as IndicatorId)) {
        seriesList.forEach((s) => { try { chart.removeSeries(s) } catch { /* */ } })
        indSeriesRef.current.delete(key)
      }
    }

    if (compMode || !bars.length) return

    for (const id of selectedIndicators) {
      const def = INDICATOR_DEFS.find((d) => d.id === id)
      if (!def || def.type !== 'overlay') continue

      // Create series if not yet present
      if (!indSeriesRef.current.has(id)) {
        const list = _createOverlaySeries(chart, id, def.color)
        indSeriesRef.current.set(id, list)
      }
      // Always update data when bars change
      _setOverlayData(id, indSeriesRef.current.get(id)!, bars)
    }
  }, [bars, selectedIndicators, compMode])

  // ── Comparison overlay ────────────────────────────────────────────────────

  useEffect(() => {
    if (!chartRef.current) return

    if (compRef.current) {
      try { chartRef.current.removeSeries(compRef.current) } catch { /* */ }
      compRef.current = null
    }

    if (!compMode || !compBars.length || !bars.length) return

    const comp = chartRef.current.addLineSeries({
      color:       '#f59e0b',
      lineWidth:   2,
      priceFormat: { type: 'percent' },
    } as Partial<LineSeriesOptions>)
    compRef.current = comp

    const normComp = normalizeBars(compBars)
    const data = normComp.map((b) => ({ time: b.time as unknown, value: b.close }))
    try {
      comp.setData(data as Parameters<typeof comp.setData>[0])
    } catch { /* */ }

    const normMain = normalizeBars(bars)
    const mainData = normMain.map((b) => ({
      time: b.time as unknown, open: b.open, high: b.high, low: b.low, close: b.close,
    }))
    try {
      candleRef.current?.setData(mainData as Parameters<typeof candleRef.current.setData>[0])
    } catch { /* */ }
  }, [compMode, compBars, bars])

  return (
    <div className={clsx('relative w-full h-full', className)}>
      <div ref={containerRef} className="w-full h-full" />
    </div>
  )
}

// ── Private helpers ───────────────────────────────────────────────────────────

function _createOverlaySeries(
  chart: IChartApi,
  id: string,
  color: string,
): ISeriesApi<'Line'>[] {
  if (id === 'bb') {
    return [
      makeLineSeries(chart, color, 1, LineStyle.Dashed),  // upper
      makeLineSeries(chart, color, 1, LineStyle.Solid),   // middle
      makeLineSeries(chart, color, 1, LineStyle.Dashed),  // lower
    ]
  }
  return [makeLineSeries(chart, color, id.startsWith('sma') ? 1 : 2)]
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyData = any

function _setOverlayData(
  id: string,
  series: ISeriesApi<'Line'>[],
  bars: OHLCVBar[],
): void {
  try {
    if (id === 'sma20') series[0].setData(toTV(calcSMA(bars, 20)) as AnyData)
    else if (id === 'sma50') series[0].setData(toTV(calcSMA(bars, 50)) as AnyData)
    else if (id === 'ema12') series[0].setData(toTV(calcEMA(bars, 12)) as AnyData)
    else if (id === 'ema26') series[0].setData(toTV(calcEMA(bars, 26)) as AnyData)
    else if (id === 'vwap')  series[0].setData(toTV(calcVWAP(bars)) as AnyData)
    else if (id === 'bb') {
      const bb = calcBB(bars)
      series[0].setData(toTV(bb.upper)  as AnyData)
      series[1].setData(toTV(bb.middle) as AnyData)
      series[2].setData(toTV(bb.lower)  as AnyData)
    }
  } catch { /* ignore stale data */ }
}
