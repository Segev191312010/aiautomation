import { get } from './client'

export interface DayPnl {
  realized: number
  unrealized: number
  total: number
  count_trades_today: number
}

export const fetchDayPnl = () => get<DayPnl>('/api/account/day-pnl')
