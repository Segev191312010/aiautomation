import type { UserSettings } from '@/types'
import { get, put } from './client'

export const fetchSettings  = () => get<UserSettings>('/api/settings')
export const updateSettings = (partial: Partial<UserSettings>) => put<UserSettings>('/api/settings', partial)
