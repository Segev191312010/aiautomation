/**
 * SignalsTable tests — column rendering, row-click selection, empty state.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import type { SignalRow } from '@/types/signals'

const fetchSignals = vi.fn()

vi.mock('@/services/api/signals', () => ({
  fetchSignals: (...args: unknown[]) => fetchSignals(...args),
}))

const FIXTURE_ROWS: SignalRow[] = [
  {
    id: 'sig-1',
    symbol: 'AAPL',
    action: 'BUY',
    source: 'tv_webhook',
    status: 'pending_review',
    queued_at: '2026-05-29T14:00:00Z',
    payload: { strategy: 'breakout', price: 198.4 },
  },
  {
    id: 'sig-2',
    symbol: 'TSLA',
    action: 'SELL',
    source: 'tv_webhook',
    status: 'applied',
    queued_at: '2026-05-29T13:55:00Z',
    payload: { strategy: 'reversal' },
  },
]

describe('SignalsTable', () => {
  beforeEach(() => {
    fetchSignals.mockReset()
  })

  it('renders columns and fixture rows', async () => {
    fetchSignals.mockResolvedValue(FIXTURE_ROWS)
    const { default: SignalsTable } = await import('../SignalsTable')
    render(<SignalsTable source="tv_webhook" refreshMs={0} />)

    expect(await screen.findByText('AAPL')).toBeInTheDocument()
    expect(screen.getByText('TSLA')).toBeInTheDocument()
    expect(screen.getByText('Symbol')).toBeInTheDocument()
    expect(screen.getByText('Action')).toBeInTheDocument()
    expect(screen.getByText('Source')).toBeInTheDocument()
    expect(screen.getByText('Status')).toBeInTheDocument()
    expect(screen.getByText('Queued')).toBeInTheDocument()
    expect(screen.getByText('pending_review')).toBeInTheDocument()
  })

  it('invokes onSelect with the clicked row', async () => {
    fetchSignals.mockResolvedValue(FIXTURE_ROWS)
    const onSelect = vi.fn()
    const { default: SignalsTable } = await import('../SignalsTable')
    render(<SignalsTable source="tv_webhook" onSelect={onSelect} refreshMs={0} />)

    const cell = await screen.findByText('AAPL')
    fireEvent.click(cell)
    expect(onSelect).toHaveBeenCalledWith(FIXTURE_ROWS[0])
  })

  it('shows empty state pointing at the alert template doc', async () => {
    fetchSignals.mockResolvedValue([])
    const { default: SignalsTable } = await import('../SignalsTable')
    render(<SignalsTable source="tv_webhook" refreshMs={0} />)

    await waitFor(() => expect(screen.getByText(/No signals received yet/i)).toBeInTheDocument())
    expect(screen.getByText(/tv_alert_template\.pine/i)).toBeInTheDocument()
  })
})
