import { fireEvent, render, screen, within } from '@testing-library/react'

import { ORDERS_FIXTURE } from '../testing/fixtures'
import type { BridgeOrder } from '../types'
import { OrdersPanel, sessionPnlTotal } from './OrdersPanel'

const BASE = ORDERS_FIXTURE[0]

const ORDERS: BridgeOrder[] = [
  {
    ...BASE,
    status: 'closed',
    exit_quantity: 5266,
    avg_exit_price: '19.4500',
    exit_reason: 'target',
    realized_pnl: '1790.44',
  },
  {
    ...BASE,
    decision_id: 'decision-skip',
    symbol: 'AMD',
    strategy: 'momo',
    status: 'skipped',
    parent_order_id: null,
    skip_reason: 'symbol already has an active bracket',
    sent_at: null,
    decided_at: '2026-10-12T13:59:00.000Z',
    filled_quantity: 0,
    avg_entry_price: null,
    realized_pnl: '0',
  },
  {
    ...BASE,
    decision_id: 'decision-attention',
    symbol: 'NVDA',
    status: 'needs_attention',
    run_id: 'shadow-9c33dc48-2026-10-09',
    filled_quantity: 0,
    avg_entry_price: null,
    realized_pnl: '-85.25',
  },
]

describe('OrdersPanel', () => {
  it('shows each bracket with its lifecycle, fills, exits, and realized P&L', () => {
    const onSelect = vi.fn()
    render(<OrdersPanel orders={ORDERS} error={null} bridgeState="ok" today="2026-10-12" onSelect={onSelect} />)

    const [closed, skipped, attention] = screen.getAllByRole('listitem')
    expect(within(closed).getByText('Closed')).toBeTruthy()
    expect(within(closed).getByText('+$1,790.44')).toBeTruthy()
    expect(within(closed).getByText('Filled 5266/5266 @ 19.1100 → exit 5266 @ 19.4500 (target)')).toBeTruthy()
    expect(within(closed).getByText('BUY 5266 @ 19.11 · stop 18.94 · target 19.45 · #101')).toBeTruthy()

    expect(within(skipped).getByText('Skipped')).toBeTruthy()
    expect(within(skipped).getByText('symbol already has an active bracket')).toBeTruthy()
    expect(within(skipped).getByText('Not sent: BUY 5266 @ 19.11 · stop 18.94 · target 19.45')).toBeTruthy()
    expect(within(skipped).getByText('MOMO')).toBeTruthy()
    expect(within(skipped).queryByText(/Filled/)).toBeNull()

    expect(within(attention).getByText('Needs attention')).toBeTruthy()
    expect(within(attention).getByText('-$85.25')).toBeTruthy()
    // An older session's order is labelled with its date.
    expect(within(attention).getByText('Fri, Oct 9')).toBeTruthy()

    fireEvent.click(within(skipped).getByRole('button', { name: 'AMD' }))
    expect(onSelect).toHaveBeenCalledWith({ symbol: 'AMD', exchange: 'NASDAQ' })
  })

  it('totals the realized P&L of today\'s session in the panel header', () => {
    render(<OrdersPanel orders={ORDERS} error={null} bridgeState="ok" today="2026-10-12" onSelect={vi.fn()} />)

    const total = screen.getByLabelText('Realized P&L total')
    // Today's closed +1,790.44 and skipped 0; Friday's -85.25 is another session.
    expect(within(total).getByText('Realized P&L today')).toBeTruthy()
    expect(within(total).getByText('+$1,790.44').className).toContain('up')
  })

  it('totals the newest listed session when none is from today', () => {
    const friday = ORDERS.filter((order) => order.run_id?.endsWith('2026-10-09'))
    const { rerender } = render(
      <OrdersPanel orders={friday} error={null} bridgeState="ok" today="2026-10-12" onSelect={vi.fn()} />,
    )
    const total = screen.getByLabelText('Realized P&L total')
    expect(within(total).getByText('Realized P&L Fri, Oct 9')).toBeTruthy()
    expect(within(total).getByText('-$85.25').className).toContain('down')

    rerender(<OrdersPanel orders={[]} error={null} bridgeState="ok" today="2026-10-12" onSelect={vi.fn()} />)
    expect(screen.queryByLabelText('Realized P&L total')).toBeNull()
  })

  it('sums in cents so decimal strings never drift', () => {
    const cents = ['0.10', '0.20', '-0.05'].map((pnl, index) => ({
      ...BASE,
      decision_id: `d${index}`,
      realized_pnl: pnl,
    }))
    expect(sessionPnlTotal(cents, '2026-10-12')).toEqual({ day: '2026-10-12', amount: 0.25 })
    expect(sessionPnlTotal([{ ...BASE, run_id: null, realized_pnl: '5' }], '2026-10-12')).toBeNull()
  })

  it('explains an empty or missing bridge state', () => {
    const { rerender } = render(
      <OrdersPanel orders={[]} error={null} bridgeState="missing" today="2026-10-12" onSelect={vi.fn()} />,
    )
    expect(screen.getByText('The PAPER bridge has not written any state yet.')).toBeTruthy()
    rerender(<OrdersPanel orders={[]} error={null} bridgeState="ok" today="2026-10-12" onSelect={vi.fn()} />)
    expect(screen.getByText('No orders sent yet.')).toBeTruthy()
  })
})
