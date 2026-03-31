import type { BacktestHistoryItem, BacktestRequest, BacktestResult } from '@/types'
import { get, post, del } from './client'

export const runBacktest = (body: BacktestRequest) =>
  post<BacktestResult>('/api/backtest/run', body)

export const saveBacktest = (name: string, result: BacktestResult) =>
  post<{ id: string; saved: boolean }>('/api/backtest/save', { name, result })

export const fetchBacktestHistory = () =>
  get<BacktestHistoryItem[]>('/api/backtest/history')

export const fetchBacktest = (id: string) =>
  get<BacktestResult>(`/api/backtest/${id}`)

export const deleteBacktest = (id: string) =>
  del<{ deleted: boolean }>(`/api/backtest/${id}`)
