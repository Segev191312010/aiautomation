/**
 * ChartView — picks between Lightweight Charts and TradingView embed
 * based on the user's saved settings.
 *
 * Defaults to lightweight when settings aren't loaded yet, so the chart
 * always renders.
 */
import React, { useEffect, useState } from 'react'
import TradingChart from './TradingChart'
import TradingViewChart from './TradingViewChart'
import { fetchSettings } from '@/services/api'
import type { AppSettings } from '@/services/mockBackend'

interface Props {
  symbol:      string
  barSeconds?: number
  className?:  string
}

export default function ChartView({ symbol, barSeconds, className }: Props) {
  const [settings, setSettings] = useState<AppSettings | null>(null)

  useEffect(() => {
    let mounted = true
    fetchSettings().then((s) => { if (mounted) setSettings(s) })
    // Re-poll settings every 10 s so a Settings page change is picked up
    const t = setInterval(() => {
      fetchSettings().then((s) => { if (mounted) setSettings(s) })
    }, 10_000)
    return () => { mounted = false; clearInterval(t) }
  }, [])

  if (settings?.chart_engine === 'tradingview') {
    return (
      <TradingViewChart
        symbol={symbol}
        theme={settings.tradingview_theme}
        className={className}
      />
    )
  }

  return <TradingChart symbol={symbol} barSeconds={barSeconds} className={className} />
}
