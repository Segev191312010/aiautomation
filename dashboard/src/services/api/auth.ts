import type { User } from '@/types'
import { get, post } from './client'

export const fetchAuthToken = () => post<{ access_token: string; token_type: string }>('/api/auth/token')
export const fetchAuthMe    = () => get<User>('/api/auth/me')
