import { render, screen } from '@testing-library/react'

import { MARKET_FIXTURE } from '../testing/fixtures'
import type { MarketContextView } from '../types'
import { MarketRegimeBadge, summaryDetail } from './MarketRegime'

function badge(): HTMLElement {
  const item = screen.getByLabelText('Market regime')
  const element = item.querySelector('.regime-badge')
  if (!(element instanceof HTMLElement)) throw new Error('no regime badge')
  return element
}

describe('MarketRegimeBadge', () => {
  it('shows a green risk-on badge with the summary and the full picture on hover', () => {
    render(<MarketRegimeBadge market={MARKET_FIXTURE} ageSeconds={8} />)

    expect(badge().textContent).toBe('Risk-on')
    expect(badge().className).toContain('regime-risk-on')
    // The badge already says Risk-on, so the summary drops its leading label.
    expect(
      screen.getByText('S&P +0.5% · Nasdaq +0.8% · 250↑/150↓ · Leading: Technology +1.2% · Lagging: Utilities -0.4%'),
    ).toBeTruthy()
    const title = screen.getByLabelText('Market regime').getAttribute('title') ?? ''
    expect(title).toContain('Indexes: SPY +0.45% · QQQ +0.80% · IWM — · DIA +0.12%')
    expect(title).toContain('Breadth: 61.20% up (250↑/150↓) · median +0.31%')
    expect(title).toContain('Leaders: Technology +1.20% · Energy +0.80%')
    expect(title).toContain('Laggards: Utilities -0.40%')
    expect(title).toContain('As of 10:04:52 ET')
  })

  it('colors neutral grey and risk-off red', () => {
    const neutral = { ...MARKET_FIXTURE, regime: 'neutral', summary: 'Neutral · S&P n/a' } as MarketContextView
    const { rerender } = render(<MarketRegimeBadge market={neutral} ageSeconds={8} />)
    expect(badge().textContent).toBe('Neutral')
    expect(badge().className).toContain('regime-neutral')
    expect(screen.getByText('S&P n/a')).toBeTruthy()

    const riskOff = { ...MARKET_FIXTURE, regime: 'risk_off', summary: 'Risk-off · S&P -1.1%' } as MarketContextView
    rerender(<MarketRegimeBadge market={riskOff} ageSeconds={8} />)
    expect(badge().textContent).toBe('Risk-off')
    expect(badge().className).toContain('regime-risk-off')
  })

  it('says when there is no context yet and flags a stale one', () => {
    const { rerender } = render(<MarketRegimeBadge market={null} ageSeconds={null} />)
    expect(screen.getByText('loading…')).toBeTruthy()

    rerender(<MarketRegimeBadge market={{ available: false }} ageSeconds={null} />)
    expect(badge().textContent).toBe('No regime')
    expect(badge().className).toContain('regime-unknown')
    expect(screen.getByText('no market context yet today')).toBeTruthy()

    rerender(<MarketRegimeBadge market={MARKET_FIXTURE} ageSeconds={400} />)
    expect(screen.getByLabelText('Market regime').className).toContain('stale')
    expect(screen.getByText(/stale \(6m 40s old\)/)).toBeTruthy()
  })

  it('strips only a matching leading label', () => {
    expect(summaryDetail('Risk-on · S&P +0.5%', 'Risk-on')).toBe('S&P +0.5%')
    expect(summaryDetail('risk-on · S&P +0.5%', 'Risk-on')).toBe('S&P +0.5%')
    expect(summaryDetail('S&P +0.5% · Risk-on', 'Risk-on')).toBe('S&P +0.5% · Risk-on')
  })
})
