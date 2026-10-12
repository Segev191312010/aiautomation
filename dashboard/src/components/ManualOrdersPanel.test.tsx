import { fireEvent, render, screen, within } from '@testing-library/react'

import { MANUAL_ORDERS_FIXTURE } from '../testing/fixtures'
import type { ManualOrder } from '../types'
import { describeManualOrder, describeManualStatus, ManualOrdersPanel } from './ManualOrdersPanel'

const [REJECTED, FILLED] = MANUAL_ORDERS_FIXTURE

describe('ManualOrdersPanel', () => {
  it('lists each order with its status badge, fill, rejection and the original text', () => {
    const onSelect = vi.fn()
    render(<ManualOrdersPanel orders={MANUAL_ORDERS_FIXTURE} error={null} onSelect={onSelect} />)

    const [rejected, filled] = screen.getAllByRole('listitem')
    const rejectedBadge = within(rejected).getByText('Rejected')
    expect(rejectedBadge.className).toContain('badge-risk')
    expect(rejected.className).toContain('tone-risk')
    expect(within(rejected).getByText('SELL 1 LMT @ 250.00')).toBeTruthy()
    expect(within(rejected).getByText('naked_short: no TSLA shares outside bot brackets')).toBeTruthy()
    expect(within(rejected).getByText('“sell 1 $TSLA at 250”')).toBeTruthy()
    expect(within(rejected).getByText('TEXT')).toBeTruthy()
    const id = within(rejected).getByText('#c0ffee')
    expect(id.getAttribute('title')).toBe('Order c0ffee2222222222222222222222222a')
    expect(within(rejected).queryByText(/^Filled/)).toBeNull()

    expect(within(filled).getByText('Filled').className).toContain('badge-take')
    expect(within(filled).getByText('BUY 1 MKT')).toBeTruthy()
    expect(within(filled).getByText('Filled 1/1 @ 227.31')).toBeTruthy()
    expect(within(filled).getByText('CMD')).toBeTruthy()
    expect(within(filled).getByText('09:35:02')).toBeTruthy()

    fireEvent.click(within(rejected).getByRole('button', { name: 'TSLA' }))
    expect(onSelect).toHaveBeenCalledWith({ symbol: 'TSLA', exchange: null })
  })

  it('shows an unknown status verbatim and flagged', () => {
    const odd: ManualOrder = { ...FILLED, id: 'f'.repeat(32), short_id: 'ffffff', status: 'pending_cancel', filled_qty: null }
    render(<ManualOrdersPanel orders={[odd]} error={null} onSelect={vi.fn()} />)
    expect(screen.getByText('pending cancel').className).toContain('badge-warn')
    expect(describeManualStatus('SENT')).toEqual({ label: 'Sent', tone: 'take' })
    expect(describeManualStatus('partially_filled')).toEqual({ label: 'Partly filled', tone: 'take' })
    expect(describeManualStatus('')).toEqual({ label: 'unknown', tone: 'warn' })
  })

  it('describes order shapes without repeating the action', () => {
    const close: ManualOrder = { ...REJECTED, action: 'CLOSE', quantity: null, order_type: null, limit_price: null }
    const closeLimit: ManualOrder = { ...REJECTED, action: 'CLOSE', quantity: null, order_type: 'LMT' }
    const closeAll: ManualOrder = { ...close, action: 'CLOSE_ALL', symbol: null }
    const stop: ManualOrder = { ...FILLED, action: 'SELL_STOP', quantity: '50', order_type: 'STP', stop_price: '170.00' }
    expect(describeManualOrder(close)).toBe('CLOSE')
    expect(describeManualOrder(closeLimit)).toBe('CLOSE LMT @ 250.00')
    expect(describeManualOrder(closeAll)).toBe('CLOSE ALL')
    expect(describeManualOrder(stop)).toBe('SELL STOP 50 · stop 170.00')
  })

  it('explains loading, empty and failing states', () => {
    const { rerender } = render(<ManualOrdersPanel orders={null} error={null} onSelect={vi.fn()} />)
    expect(screen.getByText('Loading manual orders…')).toBeTruthy()
    rerender(<ManualOrdersPanel orders={[]} error={null} onSelect={vi.fn()} />)
    expect(screen.getByText('No manual orders today.')).toBeTruthy()
    rerender(<ManualOrdersPanel orders={null} error="Manual order ledger unreadable" onSelect={vi.fn()} />)
    expect(screen.getByRole('alert').textContent).toBe('Manual orders: Manual order ledger unreadable')
  })
})
