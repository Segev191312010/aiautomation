/**
 * TradingChart — wraps TradingView lightweight-charts.
 *
 * Features:
 *  • Multiple chart types: Candlestick, OHLC, Line, Area, Baseline, Heikin-Ashi
 *  • Live candle updates via /ws/market-data WebSocket
 *  • Overlay indicators: SMA 20/50, EMA 12/26, Bollinger Bands, VWAP
 *  • Optional comparison overlay (normalized %)
 *  • Replay bar injection (live bars from WebSocket during simulation)
 *  • Responsive resize via useChart hook
 *
 * Volume is rendered separately by VolumePanel (removed from this component).
 */
import { useEffect, useRef, useCallback } from 'react'
import {
  type IChartApi,
  type ISeriesApi,
  type CandlestickSeriesOptions,
  type BarSeriesOptions,
  type LineSeriesOptions,
  type AreaSeriesOptions,
  type BaselineSeriesOptions,
  LineStyle,
} from 'lightweight-charts'
import clsx from 'clsx'
import { useChart, CHART_THEME } from '@/hooks/useChart'
import { useMarketStore, useSimStore } from '@/store'
import { wsMdService } from '@/services/ws'
import {
  calcSMA, calcEMA, calcBB, calcVWAP,
  INDICATOR_DEFS,
  type IndicatorId,
  type LinePoint,
} from '@/utils/indicators'
import { toHeikinAshi } from '@/utils/heikinAshi'
import type { OHLCVBar, ChartType } from '@/types'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyData = any

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

// ── Series creation per chart type ──────────────────────────────────────────

function createMainSeries(chart: IChartApi, chartType: ChartType): ISeriesApi<AnyData> {
  switch (chartType) {
    case 'candlestick':
    case 'heikin-ashi':
      return chart.addCandlestickSeries({
        upColor:         '#00e07a',
        downColor:       '#ff3d5a',
        borderUpColor:   '#00e07a',
        borderDownColor: '#ff3d5a',
        wickUpColor:     '#00874a',
        wickDownColor:   '#992438',
      } as Partial<CandlestickSeriesOptions>)

    case 'ohlc':
      return chart.addBarSeries({
        upColor:   '#00e07a',
        downColor: '#ff3d5a',
      } as Partial<BarSeriesOptions>)

    case 'line':
      return chart.addLineSeries({
        color:            '#4f91ff',
        lineWidth:        2,
        priceLineVisible: true,
      } as Partial<LineSeriesOptions>)

    case 'area':
      return chart.addAreaSeries({
        topColor:    '#4f91ff33',
        bottomColor: '#4f91ff05',
        lineColor:   '#4f91ff',
        lineWidth:   2,
      } as Partial<AreaSeriesOptions>)

    case 'baseline':
      return chart.addBaselineSeries({
        topFillColor1:    '#00e07a33',
        topFillColor2:    '#00e07a05',
        topLineColor:     '#00e07a',
        bottomFillColor1: '#ff3d5a05',
        bottomFillColor2: '#ff3d5a33',
        bottomLineColor:  '#ff3d5a',
        baseValue:        { type: 'price', price: 0 },
      } as Partial<BaselineSeriesOptions>)

    default:
      return chart.addCandlestickSeries({
        upColor:         '#00e07a',
        downColor:       '#ff3d5a',
        borderUpColor:   '#00e07a',
        borderDownColor: '#ff3d5a',
        wickUpColor:     '#00874a',
        wickDownColor:   '#992438',
      } as Partial<CandlestickSeriesOptions>)
  }
}

// ── Data loading per chart type ─────────────────────────────────────────────

function loadDataIntoSeries(
  series: ISeriesApi<AnyData>,
  bars: OHLCVBar[],
  chartType: ChartType,
): void {
  const limited = bars.slice(-5000)
  const barsToUse = chartType === 'heikin-ashi' ? toHeikinAshi(limited) : limited

  if (chartType === 'line' || chartType === 'area') {
    const data = barsToUse.map((b) => ({ time: b.time as unknown, value: b.close }))
    try { series.setData(data as AnyData) } catch { /* stale */ }
  } else if (chartType === 'baseline') {
    const data = barsToUse.map((b) => ({ time: b.time as unknown, value: b.close }))
    try {
      series.applyOptions({
        baseValue: { type: 'price', price: barsToUse[0]?.close ?? 0 },
      } as AnyData)
      series.setData(data as AnyData)
    } catch { /* stale */ }
  } else {
    // OHLC data: candlestick, ohlc, heikin-ashi
    const data = barsToUse.map((b) => ({
      time: b.time as unknown, open: b.open, high: b.high, low: b.low, close: b.close,
    }))
    try { series.setData(data as AnyData) } catch { /* stale */ }
  }
}

// ── Is this chart type single-value (line/area/baseline)? ───────────────────

function isSingleValue(ct: ChartType): boolean {
  return ct === 'line' || ct === 'area' || ct === 'baseline'
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  symbol:       string
  className?:   string
  barSeconds?:  number
  onChartReady?: (chart: IChartApi) => void
}

interface LiveBar {
  time:  number
  open:  number
  high:  number
  low:   number
  close: number
}

export default function TradingChart({ symbol, className, barSeconds = 86_400, onChartReady }: Props) {
  const { containerRef, chartRef } = useChart({ options: CHART_THEME })

  const mainSeriesRef = useRef<ISeriesApi<AnyData> | null>(null)
  const compRef       = useRef<ISeriesApi<'Line'> | null>(null)
  const liveBarRef    = useRef<LiveBar | null>(null)
  const indSeriesRef  = useRef<Map<string, ISeriesApi<'Line'>[]>>(new Map())
  const chartTypeRef  = useRef<ChartType>('candlestick')

  const bars               = useMarketStore((s) => s.bars[symbol] ?? [])
  const chartType          = useMarketStore((s) => s.chartType)
  const compSymbol         = useMarketStore((s) => s.compSymbol)
  const compBars           = useMarketStore((s) => s.compBars[compSymbol] ?? [])
  const compMode           = useMarketStore((s) => s.compMode)
  const selectedIndicators = useMarketStore((s) => s.selectedIndicators)
  const replayBars         = useSimStore((s) => s.replayBars)

  // Keep ref in sync for use in callbacks
  chartTypeRef.current = chartType

  // ── Notify parent of chart instance ─────────────────────────────────────

  const onChartReadyRef = useRef(onChartReady)
  onChartReadyRef.current = onChartReady

  useEffect(() => {
    if (chartRef.current && onChartReadyRef.current) {
      onChartReadyRef.current(chartRef.current)
    }
  }, [chartRef])

  // ── Create initial main series ──────────────────────────────────────────

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    mainSeriesRef.current = createMainSeries(chart, chartType)

    if (bars.length) {
      loadDataIntoSeries(mainSeriesRef.current, bars, chartType)
      chart.timeScale().fitContent()
    }

    // Notify parent after series creation
    if (onChartReadyRef.current) {
      onChartReadyRef.current(chart)
    }
  }, [chartRef]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Recreate main series when chartType changes ─────────────────────────

  const prevChartType = useRef(chartType)
  useEffect(() => {
    if (prevChartType.current === chartType) return
    prevChartType.current = chartType

    const chart = chartRef.current
    if (!chart) return

    // Remove old series
    if (mainSeriesRef.current) {
      try { chart.removeSeries(mainSeriesRef.current) } catch { /* */ }
      mainSeriesRef.current = null
    }

    // Create new series
    mainSeriesRef.current = createMainSeries(chart, chartType)

    // Reload data
    if (bars.length) {
      loadDataIntoSeries(mainSeriesRef.current, bars, chartType)
      chart.timeScale().fitContent()
    }
  }, [chartType, bars, chartRef])

  // ── Load bars ───────────────────────────────────────────────────────────

  useEffect(() => {
    if (!mainSeriesRef.current || !bars.length) return

    loadDataIntoSeries(mainSeriesRef.current, bars, chartType)
    chartRef.current?.timeScale().fitContent()

    // Seed live bar from last loaded bar
    const last = bars[bars.length - 1]
    liveBarRef.current = {
      time: last.time, open: last.open, high: last.high, low: last.low, close: last.close,
    }
  }, [bars]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Live candle via WebSocket ───────────────────────────────────────────

  useEffect(() => {
    if (!bars.length) return

    const unsub = wsMdService.subscribe(symbol, (msg) => {
      if (!mainSeriesRef.current) return
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
        if (isSingleValue(chartTypeRef.current)) {
          mainSeriesRef.current!.update({
            time:  liveBarRef.current!.time as unknown,
            value: liveBarRef.current!.close,
          } as AnyData)
        } else {
          mainSeriesRef.current!.update({
            time:  liveBarRef.current!.time  as unknown,
            open:  liveBarRef.current!.open,
            high:  liveBarRef.current!.high,
            low:   liveBarRef.current!.low,
            close: liveBarRef.current!.close,
          } as AnyData)
        }
      } catch { /* ignore */ }
    })

    return unsub
  }, [symbol, barSeconds, bars.length])

  // ── Replay bars (injected live) ─────────────────────────────────────────

  useEffect(() => {
    if (!mainSeriesRef.current || !replayBars.length) return
    const last = replayBars[replayBars.length - 1]
    try {
      if (isSingleValue(chartTypeRef.current)) {
        mainSeriesRef.current.update({
          time: last.time as unknown, value: last.close,
        } as AnyData)
      } else {
        mainSeriesRef.current.update({
          time: last.time as unknown, open: last.open, high: last.high, low: last.low, close: last.close,
        } as AnyData)
      }
    } catch { /* ignore */ }
  }, [replayBars])

  // ── Overlay indicators ──────────────────────────────────────────────────

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
  }, [bars, selectedIndicators, compMode]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Comparison overlay ──────────────────────────────────────────────────

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
      comp.setData(data as AnyData)
    } catch { /* */ }

    const normMain = normalizeBars(bars)
    if (mainSeriesRef.current) {
      if (isSingleValue(chartTypeRef.current)) {
        const mainData = normMain.map((b) => ({ time: b.time as unknown, value: b.close }))
        try { mainSeriesRef.current.setData(mainData as AnyData) } catch { /* */ }
      } else {
        const mainData = normMain.map((b) => ({
          time: b.time as unknown, open: b.open, high: b.high, low: b.low, close: b.close,
        }))
        try { mainSeriesRef.current.setData(mainData as AnyData) } catch { /* */ }
      }
    }
  }, [compMode, compBars, bars]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className={clsx('relative w-full h-full', className)} style={{ touchAction: 'none' }}>
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
