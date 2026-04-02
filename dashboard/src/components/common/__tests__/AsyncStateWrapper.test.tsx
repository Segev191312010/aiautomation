/**
 * Unit tests for AsyncStateWrapper.
 *
 * The three child components (SectionSkeleton, DegradedStateCard, EmptyState)
 * are mocked so each renders a unique data-testid.  This keeps the tests fast,
 * self-contained, and immune to cosmetic changes in those components.
 */

import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

// ── Mocks ────────────────────────────────────────────────────────────────────

vi.mock('../SectionSkeleton', () => ({
  default: ({ lines }: { lines?: number }) => (
    <div data-testid="section-skeleton" data-lines={lines} />
  ),
}))

vi.mock('../DegradedStateCard', () => ({
  default: ({ title, reason }: { title: string; reason: string }) => (
    <div data-testid="degraded-state-card">
      <span data-testid="degraded-title">{title}</span>
      <span data-testid="degraded-reason">{reason}</span>
    </div>
  ),
}))

vi.mock('../EmptyState', () => ({
  default: ({ title, message }: { title?: string; message?: string }) => (
    <div data-testid="empty-state">
      {title && <span data-testid="empty-title">{title}</span>}
      {message && <span data-testid="empty-message">{message}</span>}
    </div>
  ),
}))

// Import after mocks are registered
import AsyncStateWrapper from '../AsyncStateWrapper'

// ── Helpers ───────────────────────────────────────────────────────────────────

const CHILD_TEXT = 'child content'
const Child = () => <div data-testid="child">{CHILD_TEXT}</div>

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('AsyncStateWrapper', () => {
  it("status='loading' renders SectionSkeleton, not children", () => {
    render(
      <AsyncStateWrapper status="loading">
        <Child />
      </AsyncStateWrapper>,
    )
    expect(screen.getByTestId('section-skeleton')).toBeInTheDocument()
    expect(screen.queryByTestId('child')).not.toBeInTheDocument()
  })

  it("status='idle' renders SectionSkeleton, not children", () => {
    render(
      <AsyncStateWrapper status="idle">
        <Child />
      </AsyncStateWrapper>,
    )
    expect(screen.getByTestId('section-skeleton')).toBeInTheDocument()
    expect(screen.queryByTestId('child')).not.toBeInTheDocument()
  })

  it('passes skeletonLines prop through to SectionSkeleton', () => {
    render(
      <AsyncStateWrapper status="loading" skeletonLines={6}>
        <Child />
      </AsyncStateWrapper>,
    )
    expect(screen.getByTestId('section-skeleton')).toHaveAttribute('data-lines', '6')
  })

  it("status='success' renders children", () => {
    render(
      <AsyncStateWrapper status="success">
        <Child />
      </AsyncStateWrapper>,
    )
    expect(screen.getByTestId('child')).toBeInTheDocument()
    expect(screen.queryByTestId('section-skeleton')).not.toBeInTheDocument()
    expect(screen.queryByTestId('degraded-state-card')).not.toBeInTheDocument()
    expect(screen.queryByTestId('empty-state')).not.toBeInTheDocument()
  })

  it("status='error' renders DegradedStateCard, not children", () => {
    render(
      <AsyncStateWrapper status="error" error="Something went wrong">
        <Child />
      </AsyncStateWrapper>,
    )
    expect(screen.getByTestId('degraded-state-card')).toBeInTheDocument()
    expect(screen.queryByTestId('child')).not.toBeInTheDocument()
  })

  it("status='error' shows error message as the card reason", () => {
    render(
      <AsyncStateWrapper status="error" error="Request timed out">
        <Child />
      </AsyncStateWrapper>,
    )
    expect(screen.getByTestId('degraded-reason')).toHaveTextContent('Request timed out')
  })

  it("status='error' falls back to default reason when no error prop supplied", () => {
    render(
      <AsyncStateWrapper status="error">
        <Child />
      </AsyncStateWrapper>,
    )
    expect(screen.getByTestId('degraded-reason')).toHaveTextContent('Unable to load this section.')
  })

  it("status='unavailable' renders DegradedStateCard, not children", () => {
    render(
      <AsyncStateWrapper status="unavailable">
        <Child />
      </AsyncStateWrapper>,
    )
    expect(screen.getByTestId('degraded-state-card')).toBeInTheDocument()
    expect(screen.queryByTestId('child')).not.toBeInTheDocument()
  })

  it("status='unavailable' with degradedReason shows that custom reason", () => {
    render(
      <AsyncStateWrapper
        status="unavailable"
        degradedReason="Market data feed offline"
      >
        <Child />
      </AsyncStateWrapper>,
    )
    expect(screen.getByTestId('degraded-reason')).toHaveTextContent('Market data feed offline')
  })

  it("status='unavailable' passes custom degradedTitle to DegradedStateCard", () => {
    render(
      <AsyncStateWrapper
        status="unavailable"
        degradedTitle="Feed unavailable"
        degradedReason="Upstream error"
      >
        <Child />
      </AsyncStateWrapper>,
    )
    expect(screen.getByTestId('degraded-title')).toHaveTextContent('Feed unavailable')
  })

  it("status='unavailable' uses default degradedTitle when none supplied", () => {
    render(
      <AsyncStateWrapper status="unavailable">
        <Child />
      </AsyncStateWrapper>,
    )
    expect(screen.getByTestId('degraded-title')).toHaveTextContent('Section unavailable')
  })

  it("status='success' + isEmpty=true renders EmptyState, not children", () => {
    render(
      <AsyncStateWrapper status="success" isEmpty>
        <Child />
      </AsyncStateWrapper>,
    )
    expect(screen.getByTestId('empty-state')).toBeInTheDocument()
    expect(screen.queryByTestId('child')).not.toBeInTheDocument()
  })

  it("status='success' + isEmpty=true passes emptyTitle and emptyMessage through", () => {
    render(
      <AsyncStateWrapper
        status="success"
        isEmpty
        emptyTitle="No results"
        emptyMessage="Try adjusting your filters"
      >
        <Child />
      </AsyncStateWrapper>,
    )
    expect(screen.getByTestId('empty-title')).toHaveTextContent('No results')
    expect(screen.getByTestId('empty-message')).toHaveTextContent('Try adjusting your filters')
  })

  it("status='success' + isEmpty=false renders children, not EmptyState", () => {
    render(
      <AsyncStateWrapper status="success" isEmpty={false}>
        <Child />
      </AsyncStateWrapper>,
    )
    expect(screen.getByTestId('child')).toBeInTheDocument()
    expect(screen.queryByTestId('empty-state')).not.toBeInTheDocument()
  })

  it("status='degraded' renders children (data available despite errors)", () => {
    render(
      <AsyncStateWrapper status="degraded">
        <Child />
      </AsyncStateWrapper>,
    )
    expect(screen.getByTestId('child')).toBeInTheDocument()
    expect(screen.queryByTestId('degraded-state-card')).not.toBeInTheDocument()
    expect(screen.queryByTestId('section-skeleton')).not.toBeInTheDocument()
  })

  it("status='degraded' + isEmpty=true still shows EmptyState", () => {
    render(
      <AsyncStateWrapper status="degraded" isEmpty>
        <Child />
      </AsyncStateWrapper>,
    )
    expect(screen.getByTestId('empty-state')).toBeInTheDocument()
    expect(screen.queryByTestId('child')).not.toBeInTheDocument()
  })

  it('degradedReason takes precedence over error prop in card reason', () => {
    render(
      <AsyncStateWrapper
        status="error"
        error="generic error"
        degradedReason="specific reason"
      >
        <Child />
      </AsyncStateWrapper>,
    )
    expect(screen.getByTestId('degraded-reason')).toHaveTextContent('specific reason')
  })

  it('renders multiple children correctly on success', () => {
    render(
      <AsyncStateWrapper status="success">
        <span data-testid="child-a">A</span>
        <span data-testid="child-b">B</span>
      </AsyncStateWrapper>,
    )
    expect(screen.getByTestId('child-a')).toBeInTheDocument()
    expect(screen.getByTestId('child-b')).toBeInTheDocument()
  })
})
