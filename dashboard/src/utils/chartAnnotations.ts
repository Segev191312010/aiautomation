/**
 * Chart annotations — add/clear trade markers on a lightweight-charts series.
 *
 * Reused by Stage 4 (backtest trade markers) and Stage 5 (alert markers).
 */
import type { ISeriesApi, SeriesMarker, Time } from 'lightweight-charts'
import type { TradeMarker } from '@/types'

/**
 * Add buy/sell markers to a chart series.
 * Markers must be sorted by time (lightweight-charts requirement) — this function handles sorting.
 */
export function addTradeMarkers(
  series: ISeriesApi<'Candlestick'> | ISeriesApi<'Bar'> | ISeriesApi<'Line'> | ISeriesApi<'Area'> | ISeriesApi<'Baseline'>,
  markers: TradeMarker[],
): void {
  const sorted = [...markers].sort((a, b) => a.time - b.time)

  const tvMarkers: SeriesMarker<Time>[] = sorted.map((m) => ({
    time:     m.time as unknown as Time,
    position: m.action === 'BUY' ? 'belowBar' as const : 'aboveBar' as const,
    shape:    m.action === 'BUY' ? 'arrowUp' as const  : 'arrowDown' as const,
    color:    m.action === 'BUY' ? '#00e07a' : '#ff3d5a',
    text:     m.label ?? m.action,
    size:     1,
  }))

  series.setMarkers(tvMarkers)
}

/** Remove all markers from a series. */
export function clearTradeMarkers(
  series: ISeriesApi<'Candlestick'> | ISeriesApi<'Bar'> | ISeriesApi<'Line'> | ISeriesApi<'Area'> | ISeriesApi<'Baseline'>,
): void {
  series.setMarkers([])
}
