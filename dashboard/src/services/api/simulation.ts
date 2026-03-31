import type { PlaybackState, SimAccountState, SimOrderRecord, SimPosition } from '@/types'
import { get, post } from './client'

// Simulation
export const fetchSimAccount   = () => get<SimAccountState>('/api/simulation/account')
export const fetchSimPositions = () => get<SimPosition[]>('/api/simulation/positions')
export const fetchSimOrders    = (limit = 100) => get<SimOrderRecord[]>(`/api/simulation/orders?limit=${limit}`)
export const resetSimAccount   = () => post<{ reset: boolean }>('/api/simulation/reset')

export const placeSimOrder = (body: { symbol: string; action: 'BUY' | 'SELL'; qty: number; price: number }) =>
  post<{ success: boolean; message: string }>('/api/simulation/order', body)

// Playback
export const fetchPlaybackState  = () => get<PlaybackState>('/api/simulation/playback')
export const loadReplay = (symbol: string, period = '1y', interval = '1d') =>
  post<PlaybackState>('/api/simulation/playback/load', { symbol, period, interval })
export const playReplay          = () => post<PlaybackState>('/api/simulation/playback/play')
export const pauseReplay         = () => post<PlaybackState>('/api/simulation/playback/pause')
export const stopReplay          = () => post<PlaybackState>('/api/simulation/playback/stop')
export const setReplaySpeed      = (speed: number) =>
  post<{ speed: number }>('/api/simulation/playback/speed', { speed })
