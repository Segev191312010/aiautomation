import type {
  AccountSummary,
  BotStatus,
  OpenOrder,
  Position,
  SimAccountState,
  SimPosition,
  SystemStatus,
  Trade,
} from '@/types'
import { get, post, del } from './client'

// Status
export const fetchStatus    = () => get<SystemStatus>('/api/status')
export const fetchBotStatus = () => get<BotStatus>('/api/bot/status')

// Account
export const fetchAccountSummary = () => get<AccountSummary | SimAccountState>('/api/account/summary')
export const fetchPositions      = () => get<(Position | SimPosition)[]>('/api/positions')
export const fetchOrders         = () => get<OpenOrder[]>('/api/orders')
export const fetchTrades         = (limit = 200) => get<Trade[]>(`/api/trades?limit=${limit}`)
export const cancelOrder         = (id: number)  => del<{ cancelled: boolean }>(`/api/orders/${id}`)

export const placeManualOrder = (body: {
  symbol: string
  action: 'BUY' | 'SELL'
  quantity: number
  order_type?: 'MKT' | 'LMT'
  limit_price?: number
  asset_type?: 'STK' | 'OPT' | 'FUT'
}) => post<{ success?: boolean; message?: string }>('/api/orders/manual', body)

// Bot
export const startBot = () => post<{ running: boolean }>('/api/bot/start')
export const stopBot  = () => post<{ running: boolean }>('/api/bot/stop')
