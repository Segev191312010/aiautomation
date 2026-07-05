import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Badge, StatusDot, EmptyState, Spinner, Skeleton } from '@/components/ui'

describe('ui atoms', () => {
  it('Badge renders its children', () => {
    render(<Badge tone="green">LIVE</Badge>)
    expect(screen.getByText('LIVE')).toBeInTheDocument()
  })

  it('StatusDot renders an optional label', () => {
    render(<StatusDot tone="green" label="Connected" />)
    expect(screen.getByText('Connected')).toBeInTheDocument()
  })

  it('EmptyState renders message and detail', () => {
    render(<EmptyState message="No rules yet" detail="seed one" />)
    expect(screen.getByText('No rules yet')).toBeInTheDocument()
    expect(screen.getByText('seed one')).toBeInTheDocument()
  })

  it('Spinner exposes a status role', () => {
    render(<Spinner label="Loading" />)
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('Skeleton exposes a loading label', () => {
    render(<Skeleton />)
    expect(screen.getByLabelText('Loading')).toBeInTheDocument()
  })
})
