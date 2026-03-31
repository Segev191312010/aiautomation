import { useEffect, useRef } from 'react'
import { SECTOR_ETFS } from './constants'

export function TvTickerTape() {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    el.innerHTML = ''
    const wrapper = document.createElement('div')
    wrapper.className = 'tradingview-widget-container'
    const widgetDiv = document.createElement('div')
    widgetDiv.className = 'tradingview-widget-container__widget'
    wrapper.appendChild(widgetDiv)
    const script = document.createElement('script')
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-ticker-tape.js'
    script.async = true
    script.type = 'text/javascript'
    script.textContent = JSON.stringify({
      symbols: SECTOR_ETFS.map(s => ({ proName: `AMEX:${s.symbol}`, title: s.name })),
      showSymbolLogo: true,
      isTransparent: true,
      displayMode: 'adaptive',
      colorTheme: 'dark',
      locale: 'en',
    })
    wrapper.appendChild(script)
    el.appendChild(wrapper)
    return () => { el.innerHTML = '' }
  }, [])

  return <div ref={containerRef} className="w-full overflow-hidden" />
}
