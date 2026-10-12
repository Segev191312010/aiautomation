import { useEffect, useRef } from 'react'

import { tradingViewSymbol } from '../format'
import type { ColorScheme } from '../hooks/useColorScheme'

export const TRADINGVIEW_SCRIPT_SRC =
  'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js'

// Rapid selection changes (and React StrictMode's double effect) collapse into one widget load.
const MOUNT_DELAY_MS = 120

type TradingViewChartProps = {
  symbol: string | null
  exchange: string | null
  theme: ColorScheme
}

export function chartConfig(tvSymbol: string, theme: ColorScheme): Record<string, unknown> {
  return {
    autosize: true,
    symbol: tvSymbol,
    interval: '5',
    timezone: 'America/New_York',
    theme,
    style: '1',
    locale: 'en',
    allow_symbol_change: true,
    calendar: false,
    support_host: 'https://www.tradingview.com',
  }
}

/** TradingView Advanced Chart embed; the previous widget is torn down on every change. */
export function TradingViewChart({ symbol, exchange, theme }: TradingViewChartProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const tvSymbol = symbol ? tradingViewSymbol(symbol, exchange) : null

  useEffect(() => {
    const host = hostRef.current
    if (!host || !tvSymbol) return undefined
    const timer = setTimeout(() => {
      const widget = document.createElement('div')
      widget.className = 'tradingview-widget-container__widget'
      const script = document.createElement('script')
      script.type = 'text/javascript'
      script.src = TRADINGVIEW_SCRIPT_SRC
      script.async = true
      // The embed script reads its JSON configuration from its own body.
      script.textContent = JSON.stringify(chartConfig(tvSymbol, theme))
      host.replaceChildren(widget, script)
    }, MOUNT_DELAY_MS)
    return () => {
      clearTimeout(timer)
      host.replaceChildren()
    }
  }, [tvSymbol, theme])

  return (
    <>
      <div className="panel-head">
        <div>
          <h2>Chart</h2>
          <p className="panel-sub">{tvSymbol ? `${tvSymbol} · 5 min · New York time` : 'Select a symbol'}</p>
        </div>
      </div>
      <div className="chart-frame">
        <div
          ref={hostRef}
          className="tradingview-widget-container chart-host"
          data-testid="tradingview-host"
          data-symbol={tvSymbol ?? ''}
        />
        {tvSymbol ? null : <p className="empty chart-empty">Pick a row in the screener or a signal to load its chart.</p>}
      </div>
      <p className="panel-foot tradingview-widget-copyright">
        <a href="https://www.tradingview.com/" rel="noopener nofollow noreferrer" target="_blank">
          Chart by TradingView
        </a>
      </p>
    </>
  )
}
