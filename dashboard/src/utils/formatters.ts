/**
 * Shared formatting utilities — extracted from pages where these were duplicated.
 * Used by AnalyticsPage, TradeBotPage, Dashboard, MarketRotationPage, SimulationPage,
 * and various components (PnLSummary, SectorExposure, PositionsTable, etc.).
 */

export function fmtUSD(v: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD',
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(v)
}

export function fmtUSDCompact(v: number): string {
  const abs  = Math.abs(v)
  const sign = v < 0 ? '-' : ''
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`
  if (abs >= 1_000)     return `${sign}$${(abs / 1_000).toFixed(1)}K`
  return fmtUSD(v)
}

export function fmtPct(v: number | null | undefined, decimals = 2): string {
  if (v == null) return '--'
  const s = v.toFixed(decimals)
  return v >= 0 ? `+${s}%` : `${s}%`
}

export function fmtPrice(v: number | null | undefined): string {
  if (v == null) return '--'
  return `$${v.toFixed(2)}`
}

export function fmtDate(ts: string): string {
  const d = new Date(ts)
  const today = new Date()
  const isToday =
    d.getFullYear() === today.getFullYear() &&
    d.getMonth() === today.getMonth() &&
    d.getDate() === today.getDate()
  if (isToday) {
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
  }
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export function fmtTimestamp(ts: string): string {
  const d = new Date(ts)
  const today = new Date()
  const isToday =
    d.getFullYear() === today.getFullYear() &&
    d.getMonth() === today.getMonth() &&
    d.getDate() === today.getDate()
  if (isToday) {
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  }
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
    ' ' +
    d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
}

export function pctColor(v: number): string {
  return v >= 0 ? 'text-emerald-400' : 'text-red-400'
}

export function heatmapCellColor(v: number): string {
  if (v >= 10)  return 'bg-emerald-500/30 text-emerald-300'
  if (v >= 5)   return 'bg-emerald-500/20 text-emerald-300'
  if (v >= 2)   return 'bg-emerald-500/10 text-emerald-400'
  if (v > 0)    return 'bg-emerald-500/5 text-emerald-400'
  if (v >= -2)  return 'bg-red-500/5 text-red-400'
  if (v >= -5)  return 'bg-red-500/10 text-red-400'
  if (v >= -10) return 'bg-red-500/20 text-red-300'
  return 'bg-red-500/30 text-red-300'
}
