import { memo } from 'react'

import { formatNyTime, toNumber } from '../format'
import type { ManualOrder, SelectedSymbol } from '../types'

type ManualOrdersPanelProps = {
  orders: ManualOrder[] | null
  error: string | null
  onSelect: (selection: SelectedSymbol) => void
}

type StatusTone = 'take' | 'rules' | 'risk' | 'warn'

// The manual-order ledger's status vocabulary (migration 12).
const STATUS: Record<string, { label: string; tone: StatusTone }> = {
  queued: { label: 'Queued', tone: 'rules' },
  sending: { label: 'Sending', tone: 'warn' },
  sent: { label: 'Sent', tone: 'take' },
  partially_filled: { label: 'Partly filled', tone: 'take' },
  filled: { label: 'Filled', tone: 'take' },
  cancelled: { label: 'Cancelled', tone: 'rules' },
  expired: { label: 'Expired', tone: 'rules' },
  rejected: { label: 'Rejected', tone: 'risk' },
  error: { label: 'Error', tone: 'risk' },
  unknown: { label: 'Unknown', tone: 'warn' },
}

const SOURCES: Record<string, string> = { command: 'CMD', text: 'TEXT' }

/** A status the dashboard does not know is shown verbatim and flagged, never hidden. */
export function describeManualStatus(status: string): { label: string; tone: StatusTone } {
  const key = status.trim().toLowerCase()
  return STATUS[key] ?? { label: key.replace(/_/g, ' ') || 'unknown', tone: 'warn' }
}

/** "BUY 100 LMT @ 180.00", "SELL STOP 50 · stop 170.00", "CLOSE ALL". */
export function describeManualOrder(order: ManualOrder): string {
  const action = order.action?.replace(/_/g, ' ') ?? null
  const words = [action, order.quantity]
  // SELL_STOP is always STP and CLOSE may carry its own type; never say a type twice.
  const implied = order.action === 'SELL_STOP' && order.order_type === 'STP'
  if (order.order_type && order.order_type !== order.action && !implied) words.push(order.order_type)
  let text = words.filter((word): word is string => Boolean(word)).join(' ')
  if (order.limit_price) text += ` @ ${order.limit_price}`
  if (order.stop_price) text += ` · stop ${order.stop_price}`
  return text
}

function fillSummary(order: ManualOrder): string | null {
  const filled = toNumber(order.filled_qty)
  if (filled === null || filled <= 0) return null
  const ordered = order.quantity ? `/${order.quantity}` : ''
  return `Filled ${order.filled_qty}${ordered} @ ${order.avg_fill_price ?? '—'}`
}

function problem(order: ManualOrder): string | null {
  if (order.reject_code && order.error) return `${order.reject_code}: ${order.error}`
  return order.reject_code ?? order.error
}

function ManualOrdersPanelView({ orders, error, onSelect }: ManualOrdersPanelProps) {
  const list = orders ?? []
  return (
    <>
      <div className="panel-head">
        <div>
          <h2>Manual orders</h2>
          <p className="panel-sub">Your Telegram orders, sent by the PAPER bridge (read-only)</p>
        </div>
      </div>
      {error ? (
        <p className="inline-error" role="alert">
          Manual orders: {error}
        </p>
      ) : null}
      <ol className="feed orders" aria-label="Manual orders">
        {list.map((order) => {
          const status = describeManualStatus(order.status)
          const symbol = order.symbol
          const fills = fillSummary(order)
          const issue = problem(order)
          const source = order.source ? (SOURCES[order.source] ?? order.source.toUpperCase()) : null
          return (
            <li key={order.id} className={`order tone-${status.tone}`}>
              <span className="signal-line">
                <span className="mono muted">{formatNyTime(order.created_at)}</span>
                {source ? <span className="strategy-tag">{source}</span> : null}
                {symbol ? (
                  <button
                    type="button"
                    className="link-button symbol"
                    onClick={() => onSelect({ symbol, exchange: null })}
                  >
                    {symbol}
                  </button>
                ) : null}
                <span className={`badge badge-${status.tone}`}>{status.label}</span>
                <span className="mono muted small order-id" title={`Order ${order.id}`}>
                  #{order.short_id}
                </span>
              </span>
              <span className="signal-plan mono">{describeManualOrder(order)}</span>
              {fills ? <span className="signal-plan mono">{fills}</span> : null}
              {issue ? <span className={`signal-reason${status.tone === 'risk' ? ' danger' : ' muted'}`}>{issue}</span> : null}
              {order.raw_text ? <span className="signal-reason muted quote">“{order.raw_text}”</span> : null}
            </li>
          )
        })}
      </ol>
      {orders !== null && orders.length === 0 && !error ? <p className="empty">No manual orders today.</p> : null}
      {orders === null && !error ? <p className="empty">Loading manual orders…</p> : null}
    </>
  )
}

export const ManualOrdersPanel = memo(ManualOrdersPanelView)
