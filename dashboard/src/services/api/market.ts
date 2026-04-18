import type { MarketQuote, OHLCVBar } from '@/types'
import { get, post } from './client'

// IBKR connection
export const connectIBKR    = () => post<{ connected: boolean }>('/api/ibkr/connect')
export const disconnectIBKR = () => post<{ connected: boolean }>('/api/ibkr/disconnect')

// Market data
export const fetchWatchlist = (symbols?: string) =>
  get<MarketQuote[]>(`/api/watchlist${symbols ? `?symbols=${encodeURIComponent(symbols)}` : ''}`)

export const fetchYahooBars = (symbol: string, period = '5d', interval = '5m') =>
  get<OHLCVBar[]>(`/api/yahoo/${encodeURIComponent(symbol)}/bars?period=${period}&interval=${interval}`)

export const fetchIBKRBars = (symbol: string, barSize = '1D', duration = '60 D') =>
  get<OHLCVBar[]>(`/api/market/${encodeURIComponent(symbol)}/bars?bar_size=${barSize}&duration=${encodeURIComponent(duration)}`)

export const fetchPrice = (symbol: string) =>
  get<{ symbol: string; price: number }>(`/api/market/${encodeURIComponent(symbol)}/price`)

export const subscribeRtBars   = (symbol: string) => post<{ subscribed: boolean }>(`/api/market/${encodeURIComponent(symbol)}/subscribe`)
export const unsubscribeRtBars = (symbol: string) => post<{ subscribed: boolean }>(`/api/market/${encodeURIComponent(symbol)}/unsubscribe`)
