import type { OHLCVBar } from '@/types'

export const CHART_TIMEFRAMES = [
  { label: '1m', period: '1d', interval: '1m', ibkrBarSize: '1 min', ibkrDuration: '1 D', seconds: 60 },
  { label: '5m', period: '5d', interval: '5m', ibkrBarSize: '5 mins', ibkrDuration: '5 D', seconds: 300 },
  { label: '15m', period: '5d', interval: '15m', ibkrBarSize: '15 mins', ibkrDuration: '5 D', seconds: 900 },
  { label: '30m', period: '5d', interval: '30m', ibkrBarSize: '30 mins', ibkrDuration: '5 D', seconds: 1800 },
  { label: '1H', period: '1mo', interval: '1h', ibkrBarSize: '1 hour', ibkrDuration: '30 D', seconds: 3600 },
  { label: '1D', period: '1y', interval: '1d', ibkrBarSize: '1 day', ibkrDuration: '1 Y', seconds: 86400 },
  { label: '1W', period: '2y', interval: '1wk', ibkrBarSize: '1 week', ibkrDuration: '2 Y', seconds: 604800 },
  { label: '1M', period: '5y', interval: '1mo', ibkrBarSize: '1 month', ibkrDuration: '5 Y', seconds: 2592000 },
] as const

export type ChartResolution = typeof CHART_TIMEFRAMES[number]['interval']
export type ChartTimeframe = typeof CHART_TIMEFRAMES[number]

export function getChartTimeframe(resolution: ChartResolution): ChartTimeframe {
  return CHART_TIMEFRAMES.find((timeframe) => timeframe.interval === resolution) ?? CHART_TIMEFRAMES[5]
}

export function chartBarsKey(symbol: string, resolution: ChartResolution): string {
  return `${symbol.trim().toUpperCase()}:${resolution}`
}

export function nextChartBarTime(barTime: number, resolution: ChartResolution): number {
  if (resolution !== '1mo') return barTime + getChartTimeframe(resolution).seconds
  const next = new Date(barTime * 1000)
  next.setUTCDate(1)
  next.setUTCMonth(next.getUTCMonth() + 1)
  return Math.floor(next.getTime() / 1000)
}

export function chartBarTimeForTimestamp(
  lastBarTime: number,
  incomingTime: number,
  resolution: ChartResolution,
): number {
  const nextBarTime = nextChartBarTime(lastBarTime, resolution)
  if (incomingTime < nextBarTime) return lastBarTime

  if (resolution === '1mo') {
    const last = new Date(lastBarTime * 1000)
    const incoming = new Date(incomingTime * 1000)
    const monthsElapsed = Math.max(
      1,
      (incoming.getUTCFullYear() - last.getUTCFullYear()) * 12
        + incoming.getUTCMonth()
        - last.getUTCMonth(),
    )
    return Math.floor(Date.UTC(
      last.getUTCFullYear(),
      last.getUTCMonth() + monthsElapsed,
      1,
      last.getUTCHours(),
      last.getUTCMinutes(),
      last.getUTCSeconds(),
    ) / 1000)
  }

  const width = getChartTimeframe(resolution).seconds
  return lastBarTime + Math.floor((incomingTime - lastBarTime) / width) * width
}

export function mergeRealtimeBar(
  series: OHLCVBar[],
  incoming: OHLCVBar,
  resolution: ChartResolution,
): OHLCVBar[] {
  if (!series.length) return series
  const last = series[series.length - 1]
  if (incoming.time < last.time) return series

  const barTime = chartBarTimeForTimestamp(last.time, incoming.time, resolution)
  if (barTime === last.time) {
    return [
      ...series.slice(0, -1),
      {
        time: last.time,
        open: last.volume === 0 ? incoming.open : last.open,
        high: Math.max(last.high, incoming.high),
        low: Math.min(last.low, incoming.low),
        close: incoming.close,
        volume: last.volume + incoming.volume,
      },
    ]
  }

  return [
    ...series,
    {
      time: barTime,
      open: incoming.open,
      high: incoming.high,
      low: incoming.low,
      close: incoming.close,
      volume: incoming.volume,
    },
  ].slice(-5000)
}

export function createLatestRequestGate() {
  let latest = 0
  return {
    issue(): number {
      latest += 1
      return latest
    },
    isCurrent(requestId: number): boolean {
      return requestId === latest
    },
  }
}
