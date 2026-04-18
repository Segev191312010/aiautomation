import { get } from './client'

export const fetchIndicatorData = (
  symbol: string,
  indicator: string,
  params: { length?: number; period?: string; interval?: string; fast?: number; slow?: number; signal?: number; band?: string } = {},
) => {
  const qs = new URLSearchParams({ indicator })
  if (params.length != null)   qs.set('length',   String(params.length))
  if (params.period)           qs.set('period',   params.period)
  if (params.interval)         qs.set('interval', params.interval)
  if (params.fast != null)     qs.set('fast',     String(params.fast))
  if (params.slow != null)     qs.set('slow',     String(params.slow))
  if (params.signal != null)   qs.set('signal',   String(params.signal))
  if (params.band)             qs.set('band',     params.band)
  return get<Array<{ time: number; value: number }>>(`/api/market/${encodeURIComponent(symbol)}/indicators?${qs}`)
}
