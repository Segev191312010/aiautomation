import { memo } from 'react'

import { directionClass, formatNyDate, formatNyTime, formatSignedCurrency, toNumber } from '../format'
import type { BridgeOrder, BridgeOrderStatus, LiveStatus, SelectedSymbol } from '../types'

type OrdersPanelProps = {
  orders: BridgeOrder[] | null
  error: string | null
  bridgeState: LiveStatus['bridge_state'] | null
  today: string | null
  onSelect: (selection: SelectedSymbol) => void
}

const STATUS: Record<BridgeOrderStatus, { label: string; tone: string }> = {
  pending: { label: 'Pending', tone: 'rules' },
  sent: { label: 'Working', tone: 'take' },
  open: { label: 'Open', tone: 'take' },
  closed: { label: 'Closed', tone: 'rules' },
  cancelled: { label: 'Cancelled', tone: 'rules' },
  skipped: { label: 'Skipped', tone: 'rules' },
  needs_attention: { label: 'Needs attention', tone: 'risk' },
  unknown: { label: 'Unknown', tone: 'warn' },
}

function sessionDay(order: BridgeOrder): string | null {
  const match = order.run_id?.match(/\d{4}-\d{2}-\d{2}/)
  return match ? match[0] : null
}

export type SessionPnl = { day: string; amount: number }

/**
 * Realized P&L of one session, summed in cents: today's when any listed order is
 * from today, else the newest session listed. Orders with no known session are
 * left out, as in the header's count.
 */
export function sessionPnlTotal(orders: readonly BridgeOrder[], today: string | null): SessionPnl | null {
  const cents = new Map<string, number>()
  for (const order of orders) {
    const day = sessionDay(order)
    const amount = toNumber(order.realized_pnl)
    if (day === null || amount === null) continue
    cents.set(day, (cents.get(day) ?? 0) + Math.round(amount * 100))
  }
  const days = [...cents.keys()].sort()
  if (days.length === 0) return null
  const day = today !== null && cents.has(today) ? today : days[days.length - 1]
  return { day, amount: (cents.get(day) ?? 0) / 100 }
}

function fillSummary(order: BridgeOrder): string | null {
  const filled = order.filled_quantity ?? 0
  if (filled <= 0) return null
  const ordered = order.intent ? `/${order.intent.quantity}` : ''
  const entry = `Filled ${filled}${ordered} @ ${order.avg_entry_price ?? '—'}`
  const exited = order.exit_quantity ?? 0
  if (exited <= 0) return entry
  const via = order.exit_reason ? ` (${order.exit_reason})` : ''
  return `${entry} → exit ${exited} @ ${order.avg_exit_price ?? '—'}${via}`
}

/** Only a bracket with a broker parent order id ever reached the PAPER account. */
function transmitted(order: BridgeOrder): boolean {
  return order.parent_order_id !== null && order.status !== 'skipped' && order.status !== 'unknown'
}

function showsPnl(order: BridgeOrder): boolean {
  const amount = toNumber(order.realized_pnl)
  return amount !== null && (amount !== 0 || (order.exit_quantity ?? 0) > 0)
}

function OrdersPanelView({ orders, error, bridgeState, today, onSelect }: OrdersPanelProps) {
  const list = orders ?? []
  const total = sessionPnlTotal(list, today)
  return (
    <>
      <div className="panel-head">
        <div>
          <h2>PAPER orders</h2>
          <p className="panel-sub">Brackets from the temporary PAPER bridge (read-only)</p>
        </div>
        {total ? (
          <div
            className="panel-total"
            aria-label="Realized P&L total"
            title="Realized P&L the PAPER bridge computed from broker fills"
          >
            <span className="label">
              Realized P&amp;L {total.day === today ? 'today' : formatNyDate(`${total.day}T16:00:00Z`)}
            </span>
            <strong className={`mono ${directionClass(total.amount)}`}>{formatSignedCurrency(total.amount)}</strong>
          </div>
        ) : null}
      </div>
      {error ? (
        <p className="inline-error" role="alert">
          Orders: {error}
        </p>
      ) : null}
      {bridgeState === 'unreadable' && !error ? (
        <p className="notice warn" role="status">
          The bridge state file could not be read; retrying.
        </p>
      ) : null}
      <ol className="feed orders" aria-label="PAPER orders">
        {list.map((order) => {
          const status = STATUS[order.status] ?? STATUS.unknown
          const day = sessionDay(order)
          const symbol = order.symbol
          const fills = fillSummary(order)
          return (
            <li key={order.decision_id} className={`order tone-${status.tone}`}>
              <span className="signal-line">
                <span className="mono muted">{formatNyTime(order.sent_at ?? order.decided_at)}</span>
                {day && today && day !== today ? (
                  <span className="muted small">{formatNyDate(`${day}T16:00:00Z`)}</span>
                ) : null}
                {order.strategy ? (
                  <span className={`strategy-tag strategy-${order.strategy}`}>{order.strategy.toUpperCase()}</span>
                ) : null}
                {symbol ? (
                  <button
                    type="button"
                    className="link-button symbol"
                    onClick={() => onSelect({ symbol, exchange: order.primary_exchange })}
                  >
                    {symbol}
                  </button>
                ) : (
                  <span className="muted">unknown symbol</span>
                )}
                <span className={`badge badge-${status.tone}`}>{status.label}</span>
                {showsPnl(order) ? (
                  <strong className={`mono pnl ${directionClass(order.realized_pnl)}`}>
                    {formatSignedCurrency(order.realized_pnl)}
                  </strong>
                ) : null}
              </span>
              {order.intent ? (
                <span className="signal-plan mono">
                  {transmitted(order) ? '' : 'Not sent: '}
                  BUY {order.intent.quantity} @ {order.intent.entry_limit} · stop {order.intent.stop_price} · target{' '}
                  {order.intent.target_price}
                  {order.parent_order_id !== null ? ` · #${order.parent_order_id}` : ''}
                </span>
              ) : null}
              {fills ? <span className="signal-plan mono">{fills}</span> : null}
              {order.status === 'skipped' && order.skip_reason ? (
                <span className="signal-reason muted">{order.skip_reason}</span>
              ) : null}
            </li>
          )
        })}
      </ol>
      {orders !== null && orders.length === 0 ? (
        <p className="empty">
          {bridgeState === 'missing' ? 'The PAPER bridge has not written any state yet.' : 'No orders sent yet.'}
        </p>
      ) : null}
      {orders === null && !error ? <p className="empty">Loading orders…</p> : null}
    </>
  )
}

export const OrdersPanel = memo(OrdersPanelView)
