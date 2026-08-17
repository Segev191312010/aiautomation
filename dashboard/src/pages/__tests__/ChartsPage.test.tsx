import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import ChartsPage from '@/pages/ChartsPage'

vi.mock('@/pages/MarketPage', () => ({
  default: () => <div data-testid="market-workspace">market workspace</div>,
}))

describe('ChartsPage', () => {
  it('uses the in-app TradingView chart workspace instead of the dead sidecar iframe', () => {
    const { container } = render(<ChartsPage />)

    expect(screen.getByTestId('market-workspace')).toBeInTheDocument()
    expect(screen.getByText('TradingView chart workspace')).toBeInTheDocument()
    expect(screen.getByText(/latency follows the connected IBKR/i)).toBeInTheDocument()
    expect(container.querySelector('iframe')).toBeNull()
  })

  it('links to TradingView data-plan guidance', () => {
    expect(render(<ChartsPage />).getByRole('link', { name: 'TradingView data policy' })).toHaveAttribute(
      'href',
      'https://www.tradingview.com/widget-docs/faq/data/',
    )
  })
})
