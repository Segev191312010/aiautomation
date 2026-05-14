/**
 * TradingViewChart — iframe-based TradingView widget embed.
 *
 * Uses the public tv.js widget. The symbol is mapped from Yahoo-style format
 * (e.g. BTC-USD) to a TradingView symbol (BINANCE:BTCUSDT) when possible.
 *
 * Falls back to a friendly placeholder if tradingview.com is unreachable
 * (offline, blocked by CSP, etc.).
 */
import React, { useEffect, useRef, useState } from 'react'

const SYMBOL_MAP: Record<string, string> = {
  'BTC-USD': 'BINANCE:BTCUSDT',
  'ETH-USD': 'BINANCE:ETHUSDT',
  'SOL-USD': 'BINANCE:SOLUSDT',
  'BNB-USD': 'BINANCE:BNBUSDT',
}

function toTVSymbol(sym: string): string {
  if (SYMBOL_MAP[sym]) return SYMBOL_MAP[sym]
  if (sym.endsWith('-USD')) return `BINANCE:${sym.replace('-USD', 'USDT')}`
  // Default to NASDAQ-prefixed equity
  return `NASDAQ:${sym}`
}

interface Props {
  symbol:    string
  theme?:    'dark' | 'light'
  interval?: string
  className?: string
}

export default function TradingViewChart({
  symbol,
  theme = 'dark',
  interval = 'D',
  className,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    setFailed(false)
    if (!containerRef.current) return
    const container = containerRef.current
    container.innerHTML = ''

    // Wrap in an iframe-style container — using the legacy widget script
    const id = `tv_${Math.random().toString(36).slice(2)}`
    const div = document.createElement('div')
    div.id = id
    div.style.height = '100%'
    div.style.width  = '100%'
    container.appendChild(div)

    const script = document.createElement('script')
    script.src = 'https://s3.tradingview.com/tv.js'
    script.async = true
    script.onload = () => {
      // @ts-expect-error — TradingView global injected by the script
      if (!window.TradingView) { setFailed(true); return }
      try {
        // @ts-expect-error — TradingView global API
        new window.TradingView.widget({
          autosize: true,
          symbol: toTVSymbol(symbol),
          interval,
          timezone: 'Etc/UTC',
          theme,
          style: '1',
          locale: 'en',
          toolbar_bg: '#080d18',
          enable_publishing: false,
          allow_symbol_change: true,
          container_id: id,
          studies: ['RSI@tv-basicstudies', 'MACD@tv-basicstudies'],
        })
      } catch {
        setFailed(true)
      }
    }
    script.onerror = () => setFailed(true)
    container.appendChild(script)

    return () => {
      container.innerHTML = ''
    }
  }, [symbol, theme, interval])

  return (
    <div className={className}>
      <div ref={containerRef} className="w-full h-full relative" />
      {failed && (
        <div className="absolute inset-0 flex flex-col items-center justify-center text-center px-4 bg-terminal-bg/95">
          <span className="text-xs font-mono text-terminal-amber mb-1">⚠ TradingView widget could not load</span>
          <span className="text-[11px] font-mono text-terminal-dim">
            tradingview.com is unreachable (offline / blocked / CSP).<br />
            Switch back to Lightweight Charts in Settings.
          </span>
        </div>
      )}
    </div>
  )
}
