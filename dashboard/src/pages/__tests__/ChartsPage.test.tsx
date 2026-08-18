import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import ChartsPage from '@/pages/ChartsPage'

describe('ChartsPage', () => {
  it('uses the TradingView hosted widget for a single symbol', () => {
    const { container } = render(<ChartsPage />)

    expect(container.querySelector('iframe')).toHaveAttribute('src', expect.stringContaining('tradingview.com/widgetembed'))
    expect(screen.getByText(/TradingView's hosted widget/i)).toBeInTheDocument()
  })

  it('links to TradingView data-plan guidance', () => {
    expect(render(<ChartsPage />).getByRole('link', { name: 'TradingView data policy' })).toHaveAttribute(
      'href',
      'https://www.tradingview.com/widget-docs/faq/data/',
    )
  })
})
