import { render, screen } from '@testing-library/react'

import App from './App'

describe('Autotrader V1 shell', () => {
  it('shows the current non-executing authority boundary', () => {
    render(<App />)

    expect(screen.getByRole('heading', { name: 'Autotrader V1' })).toBeTruthy()
    expect(screen.getByText('Backtest + Shadow')).toBeTruthy()
    expect(screen.getByText('Hard NO-GO')).toBeTruthy()
    expect(screen.getByText('Order execution disabled')).toBeTruthy()
  })
})
