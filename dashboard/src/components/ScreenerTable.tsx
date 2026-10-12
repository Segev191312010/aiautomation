import { memo, useMemo, useState, type KeyboardEvent } from 'react'

import {
  directionClass,
  formatAge,
  formatCompact,
  formatNyTime,
  formatPercent,
  formatPrice,
  formatRatio,
  formatScore,
  sortScreenerRows,
  type SortDirection,
  type SortKey,
} from '../format'
import type { ScreenerRow, ScreenerSnapshot, SelectedSymbol } from '../types'

type ScreenerTableProps = {
  snapshot: ScreenerSnapshot | null
  error: string | null
  selectedSymbol: string | null
  onSelect: (selection: SelectedSymbol) => void
}

// Optional columns hide at medium widths so the ranking columns always fit.
type Column = { key: SortKey | null; label: string; title: string; optional?: boolean }

const COLUMNS: readonly Column[] = [
  { key: null, label: 'Symbol', title: 'Symbol (hot rank)' },
  { key: null, label: 'Last', title: 'Last trade price' },
  { key: 'change_pct', label: 'Chg %', title: 'Change from prior close' },
  { key: 'gap_pct', label: 'Gap %', title: 'Open versus prior close', optional: true },
  { key: 'rvol', label: 'RVOL', title: 'Relative volume' },
  { key: 'volume_shares', label: 'Volume', title: 'Shares traded today' },
  { key: 'pct_from_high', label: 'From high', title: 'Distance from the day high', optional: true },
  { key: 'score', label: 'Score', title: 'Momentum score' },
]

function rowKey(row: ScreenerRow, index: number): string {
  return `${row.symbol}:${row.con_id ?? index}`
}

function selectionOf(row: ScreenerRow): SelectedSymbol {
  return { symbol: row.symbol, exchange: row.primary_exchange ?? null }
}

function ScreenerTableView({ snapshot, error, selectedSymbol, onSelect }: ScreenerTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>('score')
  const [direction, setDirection] = useState<SortDirection>('desc')
  const [hotOnly, setHotOnly] = useState(false)
  const [query, setQuery] = useState('')

  const rows = snapshot?.rows
  const visible = useMemo(() => {
    const needle = query.trim().toUpperCase()
    const filtered = (rows ?? []).filter(
      (row) => (!hotOnly || row.hot === true) && (!needle || row.symbol.toUpperCase().includes(needle)),
    )
    return sortScreenerRows(filtered, sortKey, direction)
  }, [rows, hotOnly, query, sortKey, direction])

  const sortBy = (key: SortKey): void => {
    if (key === sortKey) {
      setDirection((current) => (current === 'desc' ? 'asc' : 'desc'))
    } else {
      setSortKey(key)
      setDirection('desc')
    }
  }

  const onRowKeyDown = (event: KeyboardEvent<HTMLTableRowElement>, row: ScreenerRow): void => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onSelect(selectionOf(row))
    }
  }

  const total = rows?.length ?? 0
  const hotCount = rows?.filter((row) => row.hot === true).length ?? 0

  return (
    <>
      <div className="panel-head">
        <div>
          <h2>Live screener</h2>
          <p className="panel-sub">
            {snapshot && !snapshot.empty ? (
              <>
                {snapshot.universe_size ?? '—'} watchlist · {snapshot.qualified ?? '—'} qualified ·{' '}
                {snapshot.quoted ?? '—'} quoted
                {snapshot.errors ? ` · ${snapshot.errors} errors` : ''}
                {typeof snapshot.sweep_seconds === 'number' ? ` · sweep ${snapshot.sweep_seconds.toFixed(1)} s` : ''}
                {' · '}as of {formatNyTime(snapshot.observed_at)} ET
              </>
            ) : (
              'Ranked by momentum score'
            )}
          </p>
        </div>
        <div className="panel-tools">
          <input
            type="search"
            className="search"
            placeholder="Symbol"
            aria-label="Filter symbols"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <label className="toggle">
            <input type="checkbox" checked={hotOnly} onChange={(event) => setHotOnly(event.target.checked)} />
            Hot only ({hotCount})
          </label>
        </div>
      </div>

      {error ? (
        <p className="inline-error" role="alert">
          Screener: {error}
        </p>
      ) : null}
      {snapshot?.stale ? (
        <p className="notice warn" role="status">
          No snapshot for today yet. Showing the last one from {snapshot.session_date ?? 'an earlier session'}
          {typeof snapshot.snapshot_age_seconds === 'number' ? ` (${formatAge(snapshot.snapshot_age_seconds)} old)` : ''}.
        </p>
      ) : null}

      <div className="table-scroll">
        <table className="screener">
          <thead>
            <tr>
              {COLUMNS.map((column) => {
                const key = column.key
                if (key === null) {
                  return (
                    <th key={column.label} scope="col" title={column.title}>
                      {column.label}
                    </th>
                  )
                }
                const active = key === sortKey
                return (
                  <th
                    key={column.label}
                    scope="col"
                    className={column.optional ? 'num optional' : 'num'}
                    aria-sort={active ? (direction === 'desc' ? 'descending' : 'ascending') : 'none'}
                  >
                    <button type="button" className="sort-button" title={column.title} onClick={() => sortBy(key)}>
                      {column.label}
                      <span aria-hidden="true">{active ? (direction === 'desc' ? ' ▼' : ' ▲') : ''}</span>
                    </button>
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, index) => {
              const selected = row.symbol === selectedSymbol
              return (
                <tr
                  key={rowKey(row, index)}
                  className={[row.hot ? 'hot' : '', selected ? 'selected' : ''].join(' ').trim() || undefined}
                  aria-current={selected ? 'true' : undefined}
                  tabIndex={0}
                  onClick={() => onSelect(selectionOf(row))}
                  onKeyDown={(event) => onRowKeyDown(event, row)}
                >
                  <td className="symbol-cell">
                    <span className="symbol">{row.symbol}</span>
                    {row.hot ? (
                      <span className="hot-badge" title="Hot rank">
                        #{row.hot_rank ?? '•'}
                      </span>
                    ) : null}
                  </td>
                  <td className="num">{formatPrice(row.last)}</td>
                  <td className={`num ${directionClass(row.change_pct)}`}>{formatPercent(row.change_pct)}</td>
                  <td className={`num optional ${directionClass(row.gap_pct)}`}>{formatPercent(row.gap_pct)}</td>
                  <td className="num">{formatRatio(row.rvol)}</td>
                  <td className="num">{formatCompact(row.volume_shares)}</td>
                  <td className="num optional">{formatPercent(row.pct_from_high)}</td>
                  <td className="num score">{formatScore(row.score)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {visible.length === 0 ? (
          <p className="empty">
            {snapshot === null
              ? 'Loading screener…'
              : total === 0
                ? 'No screener snapshot yet. Rows appear after the screener completes its first sweep.'
                : 'No rows match the current filter.'}
          </p>
        ) : null}
      </div>
      <p className="panel-foot">
        Showing {visible.length} of {total}
      </p>
    </>
  )
}

export const ScreenerTable = memo(ScreenerTableView)
