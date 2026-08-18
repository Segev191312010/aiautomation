/** URL contract for the official TradingView Advanced Chart embed. */

export type TradingViewInterval = '1' | '5' | '15' | '30' | '60' | 'D' | 'W' | 'M'

const DEFAULT_WIDGET_URL = 'https://www.tradingview.com/widgetembed/'
const DEFAULT_IB_CHART_BASE = 'http://127.0.0.1:5001'

function configuredIbChartBase(): string {
  const configured = import.meta.env.VITE_IB_CHART_BASE || DEFAULT_IB_CHART_BASE
  const url = new URL(configured)
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error('VITE_IB_CHART_BASE must use http or https')
  }
  return url.toString().replace(/\/$/, '')
}

/** Build the legacy sidecar URL used only for multi-symbol grids. */
export function buildIbMultiChartUrl(symbols: string[], timeframe: string): string {
  const normalized = symbols
    .map(symbol => symbol.trim().toUpperCase())
    .filter(Boolean)
    .slice(0, 9)
  const url = new URL(`${configuredIbChartBase()}/ib_multichart.html`)
  url.searchParams.set('symbols', normalized.join(','))
  url.searchParams.set('tf', timeframe)
  return url.toString()
}

export function tradingViewInterval(timeframe: string): TradingViewInterval {
  const intervals: Record<string, TradingViewInterval> = {
    '1m': '1',
    '5m': '5',
    '15m': '15',
    '30m': '30',
    '1h': '60',
    '1d': 'D',
    '1wk': 'W',
    '1mo': 'M',
  }
  return intervals[timeframe] ?? 'D'
}

/**
 * Build a URL for TradingView's hosted widget. The URL contains no user
 * supplied origin; only symbol and interval are encoded as query values.
 */
export function buildTradingViewUrl(symbol: string, timeframe = '1d'): string {
  const normalized = symbol.trim().toUpperCase()
  const widgetUrl = import.meta.env.VITE_TRADINGVIEW_WIDGET_URL || DEFAULT_WIDGET_URL
  const url = new URL(widgetUrl)
  url.searchParams.set('symbol', normalized.includes(':') ? normalized : `NASDAQ:${normalized}`)
  url.searchParams.set('interval', tradingViewInterval(timeframe))
  url.searchParams.set('hidetoptoolbar', '1')
  url.searchParams.set('symboledit', '1')
  url.searchParams.set('saveimage', '0')
  url.searchParams.set('toolbarbg', 'f1f3f6')
  url.searchParams.set('theme', 'light')
  url.searchParams.set('style', '1')
  url.searchParams.set('timezone', 'exchange')
  return url.toString()
}
