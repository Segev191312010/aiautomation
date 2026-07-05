import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QuickOrderForm } from '../QuickOrderForm'
import { placeManualOrder } from '@/services/api'

const toastMocks = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}))

vi.mock('@/services/api', () => ({
  placeManualOrder: vi.fn(),
}))

vi.mock('@/components/ui/ToastProvider', () => ({
  useToast: () => ({
    success: toastMocks.success,
    error: toastMocks.error,
    warning: vi.fn(),
    info: vi.fn(),
  }),
}))

describe('QuickOrderForm', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(placeManualOrder).mockResolvedValue({ message: 'Order placed' })
  })

  it('blocks invalid symbols before opening confirmation', () => {
    render(<QuickOrderForm />)

    fireEvent.change(screen.getByLabelText(/symbol/i), { target: { value: 'AA PL' } })

    expect(screen.getByRole('alert')).toHaveTextContent('Use uppercase letters, digits, "-" or "." only')
    expect(screen.getByRole('button', { name: /place buy order/i })).toBeDisabled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(placeManualOrder).not.toHaveBeenCalled()
  })

  it('requires typed confirmation before placing a normalized manual order', async () => {
    render(<QuickOrderForm />)

    fireEvent.change(screen.getByLabelText(/symbol/i), { target: { value: 'msft' } })
    fireEvent.change(screen.getByLabelText(/quantity/i), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: 'SELL' }))
    fireEvent.click(screen.getByRole('button', { name: /place sell order/i }))

    const dialog = screen.getByRole('dialog', { name: /confirm sell order/i })
    expect(within(dialog).getByText('MSFT')).toBeInTheDocument()
    expect(within(dialog).getByText('SELL')).toBeInTheDocument()
    expect(within(dialog).getByText('5')).toBeInTheDocument()

    const confirmButton = within(dialog).getByRole('button', { name: /place sell/i })
    expect(confirmButton).toBeDisabled()

    fireEvent.change(within(dialog).getByLabelText(/type confirm to confirm/i), {
      target: { value: 'CONFIRM' },
    })
    fireEvent.click(confirmButton)

    await waitFor(() => {
      expect(placeManualOrder).toHaveBeenCalledWith({
        symbol: 'MSFT',
        action: 'SELL',
        quantity: 5,
      })
    })
    expect(toastMocks.success).toHaveBeenCalledWith('Order placed')
    expect(await screen.findByText('Order placed')).toBeInTheDocument()
  })

  it('cancels confirmation without placing the order', () => {
    render(<QuickOrderForm />)

    fireEvent.change(screen.getByLabelText(/symbol/i), { target: { value: 'AAPL' } })
    fireEvent.click(screen.getByRole('button', { name: /place buy order/i }))

    const dialog = screen.getByRole('dialog', { name: /confirm buy order/i })
    fireEvent.click(within(dialog).getByRole('button', { name: /cancel/i }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(placeManualOrder).not.toHaveBeenCalled()
  })
})
