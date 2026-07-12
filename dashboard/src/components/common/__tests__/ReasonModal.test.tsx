import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import ReasonModal from '../ReasonModal'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('ReasonModal', () => {
  it('submits a trimmed operator reason', () => {
    const onConfirm = vi.fn()
    render(
      <ReasonModal
        open
        title="Pause rule"
        description="Record why this rule is paused."
        defaultReason="Paused by operator"
        confirmLabel="Pause rule"
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    )

    fireEvent.change(screen.getByRole('textbox', { name: /operator reason/i }), {
      target: { value: '  Market anomaly  ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Pause rule' }))
    expect(onConfirm).toHaveBeenCalledWith('Market anomaly')
  })

  it('requires a non-empty reason and supports cancellation', () => {
    const onCancel = vi.fn()
    render(
      <ReasonModal
        open
        title="Retire rule"
        description="Record why this rule is retired."
        defaultReason=""
        confirmLabel="Retire rule"
        destructive
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    )

    expect(screen.getByRole('button', { name: 'Retire rule' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalledOnce()
  })

  it('closes on Escape and restores focus to the opener', () => {
    const opener = document.createElement('button')
    opener.textContent = 'Open reason'
    document.body.appendChild(opener)
    opener.focus()
    const onCancel = vi.fn()
    const props = {
      title: 'Pause rule',
      description: 'Record why.',
      defaultReason: 'Paused by operator',
      confirmLabel: 'Pause rule',
      onConfirm: vi.fn(),
      onCancel,
    }
    const { rerender } = render(<ReasonModal {...props} open />)

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onCancel).toHaveBeenCalledOnce()
    rerender(<ReasonModal {...props} open={false} />)
    expect(document.activeElement).toBe(opener)
    document.body.removeChild(opener)
  })

  it('keeps Tab navigation inside the dialog', () => {
    render(
      <ReasonModal
        open
        title="Pause rule"
        description="Record why."
        defaultReason="Paused by operator"
        confirmLabel="Pause rule"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    const textbox = screen.getByRole('textbox', { name: /operator reason/i })
    const confirm = screen.getByRole('button', { name: 'Pause rule' })
    for (const element of [textbox, screen.getByRole('button', { name: 'Cancel' }), confirm]) {
      Object.defineProperty(element, 'offsetParent', { configurable: true, value: document.body })
    }

    confirm.focus()
    fireEvent.keyDown(window, { key: 'Tab' })
    expect(document.activeElement).toBe(textbox)
    textbox.focus()
    fireEvent.keyDown(window, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(confirm)
  })
})
