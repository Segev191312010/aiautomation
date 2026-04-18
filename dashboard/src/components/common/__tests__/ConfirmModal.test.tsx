import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import ConfirmModal from '../ConfirmModal'

const baseProps = {
  open: true,
  title: 'Confirm order',
  summary: [
    { label: 'Symbol', value: 'AAPL' },
    { label: 'Qty',    value: 10 },
  ],
  onConfirm: vi.fn(),
  onCancel:  vi.fn(),
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('ConfirmModal', () => {
  it('renders title and summary items', () => {
    render(<ConfirmModal {...baseProps} />)
    expect(screen.getByText('Confirm order')).toBeInTheDocument()
    expect(screen.getByText('AAPL')).toBeInTheDocument()
    expect(screen.getByText('10')).toBeInTheDocument()
  })

  it('returns null when open is false', () => {
    const { container } = render(<ConfirmModal {...baseProps} open={false} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('confirm button is disabled until the phrase matches', () => {
    render(<ConfirmModal {...baseProps} />)
    const confirmBtn = screen.getByRole('button', { name: /^confirm$/i })
    expect(confirmBtn).toBeDisabled()

    const input = screen.getByLabelText(/type confirm to confirm/i)
    fireEvent.change(input, { target: { value: 'confirm' } })
    expect(confirmBtn).toBeDisabled() // case-sensitive

    fireEvent.change(input, { target: { value: 'CONFIRM' } })
    expect(confirmBtn).not.toBeDisabled()
  })

  it('accepts a custom requirePhrase', () => {
    render(<ConfirmModal {...baseProps} requirePhrase="SEND IT" />)
    const input = screen.getByLabelText(/type send it to confirm/i)
    const confirmBtn = screen.getByRole('button', { name: /^confirm$/i })

    fireEvent.change(input, { target: { value: 'CONFIRM' } })
    expect(confirmBtn).toBeDisabled()

    fireEvent.change(input, { target: { value: 'SEND IT' } })
    expect(confirmBtn).not.toBeDisabled()
  })

  it('clicking Confirm with matching phrase fires onConfirm', () => {
    const onConfirm = vi.fn()
    render(<ConfirmModal {...baseProps} onConfirm={onConfirm} />)
    fireEvent.change(screen.getByLabelText(/type confirm to confirm/i), { target: { value: 'CONFIRM' } })
    fireEvent.click(screen.getByRole('button', { name: /^confirm$/i }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('clicking Cancel fires onCancel', () => {
    const onCancel = vi.fn()
    render(<ConfirmModal {...baseProps} onCancel={onCancel} />)
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('Escape key fires onCancel', () => {
    const onCancel = vi.fn()
    render(<ConfirmModal {...baseProps} onCancel={onCancel} />)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('Enter key on input does nothing while phrase does not match', () => {
    const onConfirm = vi.fn()
    render(<ConfirmModal {...baseProps} onConfirm={onConfirm} />)
    fireEvent.keyDown(screen.getByLabelText(/type confirm to confirm/i), { key: 'Enter' })
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('Enter key on input submits when phrase matches', () => {
    const onConfirm = vi.fn()
    render(<ConfirmModal {...baseProps} onConfirm={onConfirm} />)
    const input = screen.getByLabelText(/type confirm to confirm/i)
    fireEvent.change(input, { target: { value: 'CONFIRM' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('Enter key on window does NOT submit (only input-scoped)', () => {
    const onConfirm = vi.fn()
    render(<ConfirmModal {...baseProps} onConfirm={onConfirm} />)
    fireEvent.change(screen.getByLabelText(/type confirm to confirm/i), { target: { value: 'CONFIRM' } })
    fireEvent.keyDown(window, { key: 'Enter' })
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('restores focus to opener on close', () => {
    const opener = document.createElement('button')
    opener.textContent = 'Open'
    document.body.appendChild(opener)
    opener.focus()
    expect(document.activeElement).toBe(opener)

    const { rerender } = render(<ConfirmModal {...baseProps} />)
    // Close the modal
    rerender(<ConfirmModal {...baseProps} open={false} />)
    expect(document.activeElement).toBe(opener)

    document.body.removeChild(opener)
  })

  it('backdrop click fires onCancel; inner card click does not', () => {
    const onCancel = vi.fn()
    render(<ConfirmModal {...baseProps} onCancel={onCancel} />)
    const dialog = screen.getByRole('dialog')
    fireEvent.click(dialog) // backdrop click
    expect(onCancel).toHaveBeenCalledTimes(1)

    onCancel.mockClear()
    fireEvent.click(screen.getByText('Confirm order')) // inner content
    expect(onCancel).not.toHaveBeenCalled()
  })

  it('resets typed text when reopened', () => {
    const { rerender } = render(<ConfirmModal {...baseProps} />)
    const input = screen.getByLabelText(/type confirm to confirm/i) as HTMLInputElement
    fireEvent.change(input, { target: { value: 'CONFIRM' } })
    expect(input.value).toBe('CONFIRM')

    rerender(<ConfirmModal {...baseProps} open={false} />)
    rerender(<ConfirmModal {...baseProps} open={true} />)
    const reopenedInput = screen.getByLabelText(/type confirm to confirm/i) as HTMLInputElement
    expect(reopenedInput.value).toBe('')
  })
})
