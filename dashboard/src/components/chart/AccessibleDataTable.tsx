/**
 * AccessibleDataTable — screen-reader-only tabular view of the chart's bar data.
 *
 * lightweight-charts renders to a <canvas>, which is opaque to assistive tech.
 * This component emits a visually-hidden (`sr-only`) <table> of the same OHLCV
 * data so screen-reader users can navigate it cell-by-cell, and exposes a
 * stable id (`titleId`) that the chart canvas references via aria-labelledby.
 *
 * Rendered alongside TradingChart; never visible to sighted users.
 */
import { useMemo } from 'react'
import type { OHLCVBar } from '@/types'

interface Props {
  symbol:    string
  bars:      OHLCVBar[]
  timeframe?: string
  /** Id applied to the heading so the chart canvas can aria-labelledby it. */
  titleId:   string
  /** Cap rows emitted to the a11y tree (most-recent first). Default 500. */
  maxRows?:  number
}

function fmtBarTime(timeSec: number): string {
  const d = new Date(timeSec * 1000)
  // ISO-like, locale-stable label that reads cleanly in a screen reader.
  return d.toLocaleString('en-US', {
    timeZone: 'America/New_York',
    year:   'numeric',
    month:  'short',
    day:    'numeric',
    hour:   '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function fmtNum(v: number): string {
  return v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtVol(v: number): string {
  return Math.round(v).toLocaleString('en-US')
}

export default function AccessibleDataTable({
  symbol,
  bars,
  timeframe = '1d',
  titleId,
  maxRows = 500,
}: Props) {
  // Most-recent bars first, capped — a screen reader user wants latest data up top.
  const rows = useMemo(() => bars.slice(-maxRows).reverse(), [bars, maxRows])
  const captionId = `${titleId}-caption`

  return (
    <div className="sr-only">
      <h2 id={titleId}>
        {symbol} price chart, {timeframe} timeframe
      </h2>
      <table aria-labelledby={titleId} aria-describedby={captionId}>
        <caption id={captionId}>
          {rows.length === 0
            ? `No price data available for ${symbol}.`
            : `Open-high-low-close-volume data for ${symbol} (${timeframe}), ` +
              `${rows.length} most recent bar${rows.length === 1 ? '' : 's'}, newest first. ` +
              `Times in US Eastern.`}
        </caption>
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Open</th>
            <th scope="col">High</th>
            <th scope="col">Low</th>
            <th scope="col">Close</th>
            <th scope="col">Volume</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((b) => (
            <tr key={b.time}>
              <th scope="row">{fmtBarTime(b.time)}</th>
              <td>{fmtNum(b.open)}</td>
              <td>{fmtNum(b.high)}</td>
              <td>{fmtNum(b.low)}</td>
              <td>{fmtNum(b.close)}</td>
              <td>{fmtVol(b.volume)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
