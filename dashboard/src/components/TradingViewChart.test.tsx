import { render, screen, waitFor } from '@testing-library/react'

import { TRADINGVIEW_SCRIPT_SRC, TradingViewChart } from './TradingViewChart'

function widgetConfig(host: HTMLElement): Record<string, unknown> {
  return JSON.parse(host.querySelector('script')?.textContent ?? '{}') as Record<string, unknown>
}

describe('TradingViewChart', () => {
  it('injects one advanced-chart widget for the selected symbol and replaces it on change', async () => {
    const { rerender, unmount } = render(<TradingViewChart symbol="BRK B" exchange="NYSE" theme="dark" />)
    const host = screen.getByTestId('tradingview-host')

    await waitFor(() => expect(host.querySelector('script')).not.toBeNull())
    const script = host.querySelector('script') as HTMLScriptElement
    expect(script.src).toBe(TRADINGVIEW_SCRIPT_SRC)
    expect(script.async).toBe(true)
    expect(widgetConfig(host)).toMatchObject({
      symbol: 'NYSE:BRK.B',
      interval: '5',
      timezone: 'America/New_York',
      theme: 'dark',
      autosize: true,
      allow_symbol_change: true,
    })
    expect(host.querySelector('.tradingview-widget-container__widget')).not.toBeNull()

    rerender(<TradingViewChart symbol="NVDA" exchange="NASDAQ" theme="light" />)
    await waitFor(() => expect(widgetConfig(host).symbol).toBe('NASDAQ:NVDA'))
    expect(widgetConfig(host).theme).toBe('light')
    expect(host.querySelectorAll('script')).toHaveLength(1)
    expect(host.querySelectorAll('.tradingview-widget-container__widget')).toHaveLength(1)

    unmount()
    expect(host.childElementCount).toBe(0)
  })

  it('waits for a selection before loading anything', () => {
    render(<TradingViewChart symbol={null} exchange={null} theme="light" />)
    expect(screen.getByText(/Pick a row in the screener/)).toBeTruthy()
    expect(screen.getByTestId('tradingview-host').childElementCount).toBe(0)
  })
})
